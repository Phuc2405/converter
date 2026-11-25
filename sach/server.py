"""Dịch vụ Sách: thêm sách (PDF, Word, PowerPoint, Excel, .txt), chuyển sang bản chữ, đọc bản chữ cạnh bản gốc.

Chạy:  .venv\\Scripts\\python server.py   (start.bat làm giúp)   rồi mở http://localhost:6470
- Bản gốc nằm ở data/goc/, bản chữ ở data/md/<cùng tên>.md. Không có database: thư mục chính là kho.
- Thêm sách: server lưu file vào data/goc/ rồi chuyển sang chữ ở nền, lần lượt từng cuốn (tools/convert.py).
  PDF chỉ có ảnh quét (convert.py báo "needs OCR") thì tự chạy tools/ocr.py (Tesseract, có VietOCR thì ghép thêm).
- Chỉ nghe ở 127.0.0.1, chỉ nhận yêu cầu từ chính trang của app (kiểm Host, Origin), không bật CORS.
"""
import json
import mimetypes
import os
import queue
import re
import subprocess
import sys
import threading
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
GOC, MD = DATA / "goc", DATA / "md"
STATIC = ROOT / "static"
PORT = int(os.environ.get("SACH_PORT", "6470"))
BOOK_EXTS = (".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".txt", ".md", ".epub", ".html")
PY = sys.executable
MAX_UPLOAD = 1024 ** 3  # 1 GB
HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'; object-src 'self'"

for d in (GOC, MD):
    d.mkdir(parents=True, exist_ok=True)

JOBS: dict[str, dict] = {}  # tên sách -> {state: queued|text|ocr|done|error, pct, msg}
WORK: "queue.Queue[str]" = queue.Queue()


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def safe_name(name: str) -> str:
    """Tên file an toàn: không thư mục, không ký tự cấm của Windows."""
    name = nfc(Path(name.replace("\\", "/")).name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")
    return name[:180] or "sach"


def orig_of(stem: str) -> Path | None:
    for f in GOC.iterdir():
        if f.is_file() and f.stem == stem and f.suffix.lower() in BOOK_EXTS:
            return f
    return None


def books() -> list:
    out, seen = [], set()
    for f in sorted(GOC.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not f.is_file() or f.suffix.lower() not in BOOK_EXTS:
            continue
        seen.add(f.stem)
        md = MD / (f.stem + ".md")
        out.append({"name": f.stem, "orig": f.name, "ext": f.suffix.lower().lstrip("."), "size": f.stat().st_size,
                    "text": md.is_file(), "ocr": md.is_file() and md.read_text(encoding="utf-8", errors="ignore")[:200].startswith("<!-- bản chữ do OCR"),
                    "job": JOBS.get(f.stem)})
    for md in sorted(MD.glob("*.md")):  # bản chữ không có bản gốc (thả .md thẳng vào data/md)
        if md.stem not in seen:
            out.append({"name": md.stem, "orig": None, "ext": "md", "size": md.stat().st_size, "text": True, "ocr": False, "job": None})
    return out


def book(name: str) -> dict:
    md = MD / (safe_name(name) + ".md")
    if not md.is_file():
        raise LookupError("Sách này chưa có bản chữ.")
    o = orig_of(md.stem)
    return {"name": md.stem, "text": md.read_text(encoding="utf-8", errors="replace"), "orig": o.name if o else None,
            "ext": o.suffix.lower().lstrip(".") if o else None}


# ---------- chuyển sang chữ, chạy nền lần lượt ----------

def run_tool(args: list, stem: str, state: str) -> tuple[int, str]:
    """Chạy một script trong tools/, đọc dòng PROGRESS để báo phần trăm. Trả (mã thoát, mấy dòng cuối)."""
    JOBS[stem] = {"state": state, "pct": 0, "msg": ""}
    p = subprocess.Popen([PY, *args], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                         creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    tail = []
    for line in p.stdout:
        line = line.strip()
        m = re.match(r"PROGRESS (\d+)", line)
        if m:
            JOBS[stem]["pct"] = int(m.group(1))
        elif line:
            tail = (tail + [line])[-12:]
    return p.wait(), "\n".join(tail)


def worker():
    while True:
        name = WORK.get()
        src = GOC / name
        stem = src.stem
        try:
            if src.suffix.lower() in (".txt", ".md"):
                (MD / (stem + ".md")).write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
                JOBS[stem] = {"state": "done", "pct": 100, "msg": ""}
                continue
            code, out = run_tool(["tools/convert.py", str(src), "-o", str(MD), "--force"], stem, "text")
            md = MD / (stem + ".md")
            if code or not md.is_file():
                JOBS[stem] = {"state": "error", "pct": 0, "msg": out[-600:] or "Chuyển sang chữ bị lỗi."}
                continue
            if "WARNING needs OCR" in out and src.suffix.lower() == ".pdf":
                code, out2 = run_tool(["tools/ocr.py", str(src), "-o", str(MD)], stem, "ocr")
                if code:
                    JOBS[stem] = {"state": "error", "pct": 0, "msg": "PDF quét cần OCR nhưng OCR bị lỗi (đã chạy setup.bat chưa?):\n" + out2[-500:]}
                    continue
            JOBS[stem] = {"state": "done", "pct": 100, "msg": ""}
        except Exception as e:  # noqa: BLE001  (một cuốn lỗi thì làm tiếp cuốn sau)
            JOBS[stem] = {"state": "error", "pct": 0, "msg": f"{type(e).__name__}: {e}"}
        finally:
            WORK.task_done()


def resume_missing():
    """Khởi động: sách có bản gốc mà chưa có bản chữ thì xếp vào hàng chờ (lần trước tắt giữa chừng)."""
    for b in books():
        if b["orig"] and not b["text"]:
            JOBS[b["name"]] = {"state": "queued", "pct": 0, "msg": ""}
            WORK.put(b["orig"])


def add_book(filename: str, body: bytes) -> dict:
    name = safe_name(filename)
    if Path(name).suffix.lower() not in BOOK_EXTS:
        raise ValueError("Chỉ nhận " + ", ".join(BOOK_EXTS))
    dest, n = GOC / name, 2
    while dest.exists() or (MD / (dest.stem + ".md")).exists():  # trùng tên thì thêm (2), (3)…
        dest = GOC / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    dest.write_bytes(body)
    JOBS[dest.stem] = {"state": "queued", "pct": 0, "msg": ""}
    WORK.put(dest.name)
    return {"ok": True, "name": dest.stem}


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "Sach/1"

    def log_message(self, *a):
        pass

    def allowed(self) -> bool:
        if self.headers.get("Host", "") not in HOSTS:
            return False
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc in HOSTS

    def send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        if not self.allowed():
            return self.json({"error": "forbidden"}, 403)
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/":
                return self.file(STATIC / "index.html")
            if url.path.startswith("/static/"):
                return self.file(STATIC / unquote(url.path[len("/static/"):]), STATIC)
            if url.path.startswith("/goc/"):  # bản gốc, để xem cạnh bản chữ (PDF hiện bằng trình đọc PDF của trình duyệt)
                return self.file(GOC / unquote(url.path[len("/goc/"):]), GOC)
            if url.path == "/api/books":
                return self.json({"books": books(), "queue": WORK.qsize()})
            if url.path == "/api/book":
                return self.json(book(q.get("name", "")))
        except LookupError as e:
            return self.json({"error": str(e)}, 404)
        self.json({"error": "not found"}, 404)

    def do_POST(self):
        if not self.allowed():
            return self.json({"error": "forbidden"}, 403)
        url = urlparse(self.path)
        size = int(self.headers.get("Content-Length") or 0)
        try:
            if url.path == "/api/upload":  # thân yêu cầu là chính file; tên file ở header X-Name (đã mã hóa URL)
                if size <= 0 or size > MAX_UPLOAD:
                    return self.json({"error": "File rỗng hoặc lớn quá 1 GB."}, 400)
                name = unquote(self.headers.get("X-Name", ""))
                return self.json(add_book(name, self.rfile.read(size)))
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.json({"error": "Chỉ nhận JSON."}, 415)
            body = json.loads(self.rfile.read(size) or b"{}")
            if url.path == "/api/retry":
                b = orig_of(safe_name(str(body.get("name", ""))))
                if not b:
                    return self.json({"error": "Không thấy bản gốc."}, 404)
                JOBS[b.stem] = {"state": "queued", "pct": 0, "msg": ""}
                WORK.put(b.name)
                return self.json({"ok": True})
        except ValueError as e:
            return self.json({"error": str(e)}, 400)
        self.json({"error": "not found"}, 404)

    def file(self, p: Path, base: Path | None = None):
        p = p.resolve()
        if base and base.resolve() not in p.parents or not p.is_file():
            return self.json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self.send(200, p.read_bytes(), ctype)


def main():
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/markdown", ".md")
    threading.Thread(target=worker, daemon=True).start()
    resume_missing()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Sách: http://localhost:{PORT}", flush=True)
    if "--open" in sys.argv:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
