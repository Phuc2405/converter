"""Dịch vụ Video: thêm video, chép lời có mốc giờ, xem video cạnh lời; chữ đang đọc được tô theo video, bấm chữ là tua.

Chạy:  .venv\\Scripts\\python server.py   (start.bat làm giúp)   rồi mở http://localhost:6471
- Video gốc ở data/goc/, lời ở data/md/<cùng tên>.md (mỗi đoạn mở đầu bằng **[mm:ss]**),
  giờ của từng chữ ở data/word-times/<cùng tên>.json. Không có database: thư mục chính là kho.
- Thêm video: server lưu file rồi chép lời ở nền, lần lượt từng video (tools/transcribe.py: faster-whisper, GPU NVIDIA nếu có).
  transcribe.py ghi luôn giờ từng chữ (tools/word_times.py).
- Video tải về chỉ có hình: để phần tiếng ở data/goc/_tiếng/<cùng tên>.m4a, server chép lời từ file đó và app phát kèm.
- Xóa video: chuyển video, lời, giờ từng chữ vào data/_da-xoa/<thời điểm>/ (xóa hẳn thì xóa thư mục đó bằng tay).
- Chỉ nghe ở 127.0.0.1, chỉ nhận yêu cầu từ chính trang của app (kiểm Host, Origin), không bật CORS.
"""
import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
GOC, MD, WT, TRASH = DATA / "goc", DATA / "md", DATA / "word-times", DATA / "_da-xoa"
SOUND_DIR = GOC / "_tiếng"
STATIC = ROOT / "static"
PORT = int(os.environ.get("VIDEO_PORT", "6471"))
MEDIA_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".mp3", ".m4a", ".wav", ".ogg")
SOUND_EXTS = (".m4a", ".mp3", ".webm", ".opus", ".aac")
PY = sys.executable
MAX_UPLOAD = 8 * 1024 ** 3  # 8 GB
HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; object-src 'none'"
GPU = os.environ.get("VIDEO_GPU", "auto")  # "cpu" để không dùng GPU

for d in (GOC, MD, WT):
    d.mkdir(parents=True, exist_ok=True)

JOBS: dict[str, dict] = {}  # tên video -> {state: queued|transcribe|done|error, pct, msg}
WORK: "queue.Queue[str]" = queue.Queue()
RUNNING: set = set()


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def safe_name(name: str) -> str:
    name = nfc(Path(name.replace("\\", "/")).name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")
    return name[:180] or "video"


def video_of(stem: str) -> Path | None:
    for f in GOC.iterdir():
        if f.is_file() and f.stem == stem and f.suffix.lower() in MEDIA_EXTS:
            return f
    return None


def sound_of(stem: str) -> Path | None:
    for ext in SOUND_EXTS:
        a = SOUND_DIR / (stem + ext)
        if a.is_file():
            return a
    return None


def videos() -> list:
    out = []
    for f in sorted(GOC.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not f.is_file() or f.suffix.lower() not in MEDIA_EXTS:
            continue
        out.append({"name": f.stem, "file": f.name, "size": f.stat().st_size, "text": (MD / (f.stem + ".md")).is_file(),
                    "words": (WT / (f.stem + ".json")).is_file(), "job": JOBS.get(f.stem)})
    return out


def video(name: str) -> dict:
    f = video_of(safe_name(name))
    if not f:
        raise LookupError("Không thấy video này.")
    md, wt, snd = MD / (f.stem + ".md"), WT / (f.stem + ".json"), sound_of(f.stem)
    words = None
    if wt.is_file():
        try:
            words = json.loads(wt.read_text(encoding="utf-8")).get("paras")
        except ValueError:
            words = None
    return {"name": f.stem, "file": f.name, "sound": snd.name if snd else None,
            "text": md.read_text(encoding="utf-8", errors="replace") if md.is_file() else None, "words": words}


# ---------- chép lời, chạy nền lần lượt ----------

def worker():
    while True:
        file = WORK.get()
        src = GOC / file
        stem = src.stem
        RUNNING.add(stem)
        try:
            if not src.is_file():
                JOBS.pop(stem, None)
                continue
            JOBS[stem] = {"state": "transcribe", "pct": 0, "msg": ""}
            snd = sound_of(stem) or src
            p = subprocess.Popen([PY, "tools/transcribe.py", str(snd), "-o", str(MD), "--device", GPU], cwd=ROOT,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                 env={**os.environ, "PYTHONIOENCODING": "utf-8"}, creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
            tail = []
            for line in p.stdout:
                line = line.strip()
                m = re.match(r"PROGRESS (\d+)", line)
                if m:
                    JOBS[stem]["pct"] = int(m.group(1))
                elif line:
                    tail = (tail + [line])[-12:]
            code = p.wait()
            out_md = MD / (snd.stem + ".md")
            if snd != src and out_md.is_file() and out_md.stem != stem:  # chép từ phần tiếng: đặt tên lời theo video
                out_md.replace(MD / (stem + ".md"))
            if code or not (MD / (stem + ".md")).is_file():
                JOBS[stem] = {"state": "error", "pct": 0, "msg": "\n".join(tail)[-600:] or "Chép lời bị lỗi."}
            else:
                JOBS[stem] = {"state": "done", "pct": 100, "msg": ""}
        except Exception as e:  # noqa: BLE001
            JOBS[stem] = {"state": "error", "pct": 0, "msg": f"{type(e).__name__}: {e}"}
        finally:
            RUNNING.discard(stem)
            WORK.task_done()


def resume_missing():
    for v in videos():
        if not v["text"]:
            JOBS[v["name"]] = {"state": "queued", "pct": 0, "msg": ""}
            WORK.put(v["file"])


def add_video(filename: str, rfile, size: int) -> dict:
    name = safe_name(filename)
    if Path(name).suffix.lower() not in MEDIA_EXTS:
        raise ValueError("Chỉ nhận " + ", ".join(MEDIA_EXTS))
    dest, n = GOC / name, 2
    while dest.exists() or (MD / (dest.stem + ".md")).exists():
        dest = GOC / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    tmp = dest.with_name(dest.name + ".part")
    with open(tmp, "wb") as f:  # video lớn: ghi dần, không giữ cả file trong bộ nhớ
        left = size
        while left > 0:
            chunk = rfile.read(min(1 << 20, left))
            if not chunk:
                break
            f.write(chunk)
            left -= len(chunk)
    if left:
        tmp.unlink(missing_ok=True)
        raise ValueError("Tải lên bị ngắt giữa chừng.")
    tmp.replace(dest)
    JOBS[dest.stem] = {"state": "queued", "pct": 0, "msg": ""}
    WORK.put(dest.name)
    return {"ok": True, "name": dest.stem}


def delete_video(name: str) -> dict:
    f = video_of(safe_name(name))
    if not f:
        raise LookupError("Không thấy video này.")
    if f.stem in RUNNING:
        raise ValueError("Video này đang được chép lời. Chờ xong rồi xóa.")
    box = TRASH / time.strftime("%Y%m%d-%H%M%S")
    box.mkdir(parents=True, exist_ok=True)
    try:
        for p in (f, MD / (f.stem + ".md"), WT / (f.stem + ".json"), sound_of(f.stem)):
            if p and p.is_file():
                shutil.move(str(p), str(box / p.name))
    except PermissionError:
        raise ValueError("Video đang được mở ở cửa sổ khác. Đóng nó rồi xóa lại.")
    JOBS.pop(f.stem, None)
    return {"ok": True, "trash": str(box.relative_to(ROOT))}


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "Video/1"

    def log_message(self, *a):
        pass

    def allowed(self) -> bool:
        if self.headers.get("Host", "") not in HOSTS:
            return False
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc in HOSTS

    def head(self, code: int, ctype: str, length: int, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.head(code, "application/json; charset=utf-8", len(body), {"Cache-Control": "no-store"})
        self.wfile.write(body)

    def do_GET(self):
        if not self.allowed():
            return self.json({"error": "forbidden"}, 403)
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/":
                return self.file(STATIC / "index.html", STATIC)
            if url.path.startswith("/static/"):
                return self.file(STATIC / unquote(url.path[len("/static/"):]), STATIC)
            if url.path.startswith("/media/"):  # video và phần tiếng, có hỗ trợ Range để tua
                return self.file(GOC / unquote(url.path[len("/media/"):]), GOC, ranged=True)
            if url.path == "/api/videos":
                return self.json({"videos": videos(), "queue": WORK.qsize()})
            if url.path == "/api/video":
                return self.json(video(q.get("name", "")))
        except LookupError as e:
            return self.json({"error": str(e)}, 404)
        except (ConnectionError, OSError):
            return
        self.json({"error": "not found"}, 404)

    def do_POST(self):
        if not self.allowed():
            return self.json({"error": "forbidden"}, 403)
        url = urlparse(self.path)
        size = int(self.headers.get("Content-Length") or 0)
        try:
            if url.path == "/api/upload":
                if size <= 0 or size > MAX_UPLOAD:
                    return self.json({"error": "File rỗng hoặc lớn quá 8 GB."}, 400)
                return self.json(add_video(unquote(self.headers.get("X-Name", "")), self.rfile, size))
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.json({"error": "Chỉ nhận JSON."}, 415)
            body = json.loads(self.rfile.read(size) or b"{}")
            if url.path == "/api/delete":
                return self.json(delete_video(str(body.get("name", ""))))
            if url.path == "/api/retry":
                f = video_of(safe_name(str(body.get("name", ""))))
                if not f:
                    return self.json({"error": "Không thấy video."}, 404)
                JOBS[f.stem] = {"state": "queued", "pct": 0, "msg": ""}
                WORK.put(f.name)
                return self.json({"ok": True})
        except LookupError as e:
            return self.json({"error": str(e)}, 404)
        except ValueError as e:
            return self.json({"error": str(e)}, 400)
        self.json({"error": "not found"}, 404)

    def file(self, p: Path, base: Path, ranged: bool = False):
        p = p.resolve()
        if base.resolve() not in p.parents or not p.is_file():
            return self.json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        size = p.stat().st_size
        rng = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", "")) if ranged else None
        if rng and (rng.group(1) or rng.group(2)):
            a = int(rng.group(1)) if rng.group(1) else max(0, size - int(rng.group(2)))
            b = int(rng.group(2)) if rng.group(1) and rng.group(2) else size - 1
            b = min(b, size - 1)
            if a > b:
                self.head(416, ctype, 0, {"Content-Range": f"bytes */{size}"})
                return
            self.head(206, ctype, b - a + 1, {"Content-Range": f"bytes {a}-{b}/{size}", "Accept-Ranges": "bytes"})
            with open(p, "rb") as f:
                f.seek(a)
                left = b - a + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            return
        self.head(200, ctype, size, {"Accept-Ranges": "bytes"} if ranged else None)
        with open(p, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 1 << 20)


def main():
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("video/mp4", ".m4v")
    mimetypes.add_type("audio/mp4", ".m4a")
    threading.Thread(target=worker, daemon=True).start()
    resume_missing()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Video: http://localhost:{PORT}", flush=True)
    if "--open" in sys.argv:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
