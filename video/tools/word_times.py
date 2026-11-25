"""Mốc giờ của từng chữ trong bản chữ của video, để app tô sáng đúng chữ đang được đọc.

Bản chữ (`data/md/<tên>.md`, mỗi đoạn mở đầu bằng **[mm:ss]**) KHÔNG bị sửa: script nghe lại tiếng của video
(faster-whisper, word_timestamps), rồi so khớp từng chữ nghe được với từng chữ của bản chữ (difflib); chữ không khớp thì
lấy giờ nội suy giữa hai chữ khớp gần nhất. Kết quả ghi riêng ở `data/word-times/<tên>.json`:
  {"v": 1, "paras": {"<giây bắt đầu của đoạn>": [giờ bắt đầu của chữ 1, chữ 2, ...]}}
Khóa theo tên file (không theo đường dẫn) nên chuyển nhánh, đổi thư mục vẫn dùng được; đổi tên file thì chạy lại.

Dùng
  python tools/word_times.py "data/md/<tên>.md"            một bản chữ
  python tools/word_times.py --all [--force]                mọi bản chữ của video chưa có mốc (force: làm lại hết)
tools/transcribe.py gọi save() ngay sau khi chép lời, nên video mới thêm có sẵn mốc.
"""
import argparse
import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "word-times"
MEDIA = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".mp3", ".m4a", ".wav", ".ogg")
SOUND = (".m4a", ".mp3", ".webm", ".opus", ".aac")
PARA = re.compile(r"^\*\*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\*\*\s*(.*)$")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def secs(mark: str) -> int:
    n = 0
    for x in mark.split(":"):
        n = n * 60 + int(x)
    return n


def norm(w: str) -> str:
    w = unicodedata.normalize("NFC", w).lower()
    return "".join(c for c in w if c.isalnum())


def paragraphs(md: Path):
    """[(giây bắt đầu, [chữ, ...])] theo đúng cách app tách chữ (khoảng trắng)."""
    out = []
    for line in md.read_text(encoding="utf-8").splitlines():
        m = PARA.match(line.strip())
        if m:
            out.append((secs(m.group(1)), m.group(2).split()))
    return out


def align(paras, words):
    """paras: [(start, tokens)], words: [(start, text)] nghe được. Trả {str(start): [giờ của từng token]}."""
    flat = [(i, t) for i, (_, toks) in enumerate(paras) for t in toks]
    a = [norm(t) for _, t in flat]
    b = [norm(w) for _, w in words]
    times = [None] * len(flat)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            times[blk.a + k] = words[blk.b + k][0]
    starts = [s for s, _ in paras]
    bound = lambda i: (starts[i], starts[i + 1] if i + 1 < len(starts) else float("inf"))  # noqa: E731
    # chữ không khớp: nội suy giữa hai chữ khớp gần nhất (hoặc đầu đoạn)
    k = 0
    while k < len(flat):
        if times[k] is not None:
            k += 1
            continue
        j = k
        while j < len(flat) and times[j] is None:
            j += 1
        lo = times[k - 1] if k else starts[flat[k][0]]
        hi = times[j] if j < len(flat) else (lo + 0.35 * (j - k + 1))
        for n in range(k, j):
            times[n] = lo + (hi - lo) * (n - k + 1) / (j - k + 1)
        k = j
    res, last = {}, 0.0
    for (pi, _), t in zip(flat, times):
        a0, a1 = bound(pi)
        t = min(max(t, a0, last), a1)  # đi tới, không lùi, không ra ngoài đoạn
        last = t
        res.setdefault(str(paras[pi][0]), []).append(round(t, 2))
    matched = sum(blk.size for blk in sm.get_matching_blocks())
    return res, (matched / len(flat) if flat else 0)


def save(md: Path, words) -> Path | None:
    paras = paragraphs(md)
    if not paras or not words:
        return None
    res, ratio = align(paras, words)
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / (md.stem + ".json")
    out.write_text(json.dumps({"v": 1, "match": round(ratio, 3), "paras": res}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"ok: {out.name} (khớp {ratio:.0%} số chữ)", flush=True)
    return out


def sound_of(md: Path) -> Path | None:
    """Tiếng của video: gốc/_tiếng/<tên>.<m4a…> nếu có (video tải về chỉ có hình), không thì chính video trong gốc/."""
    home = md.parent.parent / "goc"  # video ở data/goc/, phần tiếng tách riêng (nếu có) ở data/goc/_tiếng/
    found = [f for f in home.glob("**/" + glob_escape(md.stem) + ".*") if f.suffix.lower() in MEDIA + SOUND]
    for f in found:
        if "_tiếng" in f.relative_to(home).parts:
            return f
    return next((f for f in found if f.suffix.lower() in MEDIA), None)


def glob_escape(s: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", s)


def listen(model, path: Path, lang: str | None):
    segs, info = model.transcribe(str(path), language=lang, vad_filter=True, word_timestamps=True, condition_on_previous_text=False)
    words, last = [], -1
    for seg in segs:
        for w in seg.words or []:
            words.append((w.start, w.word.strip()))
        pct = int(seg.end * 100 / info.duration) if info.duration else 0
        if pct != last:
            print(f"PROGRESS {min(pct, 99)}", flush=True)
            last = pct
    return words


def lang_of(md: Path) -> str | None:
    # chỉ đọc dòng "- **Cách lấy** …, ngôn ngữ vi" ở đầu file (lời video cũng có thể có chữ "ngôn ngữ và…")
    m = re.search(r"^- \*\*Cách lấy\*\*.*ngôn ngữ ([a-z]{2,3})\s*$", md.read_text(encoding="utf-8")[:800], re.M)
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("md", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu"])
    args = ap.parse_args()
    if args.all:
        todo = [f for f in sorted((ROOT / "data" / "md").glob("*.md"))
                if paragraphs(f) and sound_of(f) and (args.force or not (OUT / (f.stem + ".json")).exists())]
    elif args.md:
        todo = [Path(args.md).resolve()]
    else:
        ap.error("cần một file .md hoặc --all")
    if not todo:
        print("Không có bản chữ nào cần làm.")
        return
    sys.path.insert(0, str(Path(__file__).parent))
    from transcribe import load_model, whisper_compat  # noqa: E402
    whisper_compat()
    model, device, name = load_model("", args.device)
    print(f"Model {name} on {device}; {len(todo)} bản chữ", flush=True)
    for n, md in enumerate(todo, 1):
        snd = sound_of(md)
        if not snd:
            print(f"bỏ qua (không thấy video): {md.name}")
            continue
        print(f"[{n}/{len(todo)}] {md.name}", flush=True)
        try:
            save(md, listen(model, snd, lang_of(md)))
        except Exception as e:  # noqa: BLE001  (một video lỗi thì làm tiếp video khác)
            print(f"lỗi {md.name}: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
