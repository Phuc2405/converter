"""OCR a scanned PDF (image-only pages) into Markdown with <!-- page N --> markers.

Usage
  python tools/ocr.py "data/goc/book.pdf" [-o data/md] [--engine auto|viet|tesseract]
                      [--lang vie] [--dpi 300] [--pages 1-20]

Two engines
- tesseract: Tesseract alone. Fast (about half a second per page) but its Vietnamese model gets many accents wrong
  ("Khám" for "Khảm", "quề" for "quẻ"); the user found the result too poor to read or quote.
- viet (used by "auto" when PyTorch and VietOCR are installed, see tools/requirements-ocr.txt): Tesseract only finds the
  layout (lines and words, reading order), then VietOCR (github.com/pbcquoc/vietocr, a recogniser trained on Vietnamese)
  re-reads the text in short pieces of each line, and the two readings are merged word by word:
    * same letters, different accents or case: accents from the reading that the book itself confirms more often
      (words on which both engines agree form the book's own word list), upper/lower case from Tesseract;
    * different letters: the reading that is a possible Vietnamese syllable, else the one the word list knows;
    * a piece that Tesseract reads as symbols (Chinese characters, hexagram drawings, table rules) keeps Tesseract's
      reading, because VietOCR would invent Vietnamese-looking words there.
  Measured on Tăng San Bốc Dịch, Bốc Phệ Chính Tông and Nguyễn Hiến Lê's Kinh Dịch (25/11/2026): prose pages go from
  several wrong words per paragraph to roughly one. Italic or faint pages are still imperfect.
  Needs an NVIDIA GPU to be practical (about 1 to 2 seconds per page); on CPU it is slow.

- Needs Tesseract (github.com/tesseract-ocr/tesseract). Windows: winget install UB-Mannheim.TesseractOCR
  (tools/setup.bat does it). Language data (vie, eng, chi_tra) lives in tools/tessdata/, from github.com/tesseract-ocr/tessdata_best.
- --lang: Tesseract language(s). "vie" (default); "vie+chi_tra" only for books with a lot of Chinese (tesseract engine).
- Prints "PROGRESS <percent>" lines so the app can show how far it got. Writes <out>/<pdf name>.md (overwrites).
- OCR text still has mistakes (verse, tables, hexagram diagrams worst). The first line of the file says it is OCR,
  so Claude checks the page image before quoting word for word.
"""
import argparse
import csv
import difflib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
TESSDATA = ROOT / "tools" / "tessdata"
MODELS = ROOT / "data" / "models" / "vietocr"
VIET_WEIGHTS = "https://vocr.vn/data/vietocr/vgg_transformer.pth"
# Tesseract chạy ẩn và ở mức ưu tiên thấp: một cuốn sách chạy hàng chục phút với mọi nhân CPU, để ưu tiên thường thì cả máy (và app) đơ theo.
QUIET = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


def tesseract_bin() -> str | None:
    for c in (shutil.which("tesseract"), r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe")):
        if c and Path(c).is_file():
            return c
    return None


def tessdata_dir() -> Path:
    """Tesseract trên Windows không mở được đường dẫn có dấu (D:/Học/…): chép dữ liệu ngôn ngữ sang thư mục không dấu nếu cần."""
    if str(TESSDATA).isascii():
        return TESSDATA
    safe = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "ngu-thuat-tessdata"
    if not str(safe).isascii():
        safe = Path(os.environ.get("SystemDrive", "C:") + "\\") / "ProgramData" / "ngu-thuat-tessdata"
    safe.mkdir(parents=True, exist_ok=True)
    for f in TESSDATA.glob("*.traineddata"):
        t = safe / f.name
        if not t.exists() or t.stat().st_size != f.stat().st_size:
            shutil.copyfile(f, t)
    return safe


def page_range(spec: str, n: int) -> list:
    if not spec:
        return list(range(n))
    a, _, b = spec.partition("-")
    return list(range(max(int(a), 1) - 1, min(int(b or a), n)))


def viet_ready() -> bool:
    """Có đủ thư viện cho engine "viet" không (PyTorch, VietOCR; tools/requirements-ocr.txt)."""
    try:
        import torch  # noqa: F401
        from vietocr.tool.predictor import Predictor  # noqa: F401  (kéo theo cả các gói phụ của VietOCR: thiếu một gói là biết ngay)
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------- ghép hai cách đọc ----------

_VN = "aàáảãạăằắẳẵặâầấẩẫậbcdđeèéẻẽẹêềếểễệfghiìíỉĩịjklmnoòóỏõọôồốổỗộơờớởỡợpqrstuùúủũụưừứửữựvwxyỳýỷỹỵz"
_VN_SET = set(_VN + _VN.upper())
_SYLL = re.compile(r"^(?:ngh|ch|gh|gi|kh|ng|nh|ph|qu|th|tr|[bcdghklmnpqrstvx])?[aeiouy]{1,3}(?:ch|ng|nh|[cmnpt])?$")
_PUNCT = ".,;:!?()\"'“”‘’«»-–—/%…"


def base(w: str) -> str:
    """Chữ cái gốc của một từ: thường, bỏ dấu (để biết hai cách đọc có cùng chữ cái không)."""
    t = unicodedata.normalize("NFD", w.lower().replace("đ", "d"))
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def split_punct(w: str) -> tuple:
    """(dấu câu đầu, phần chữ, dấu câu cuối)."""
    a = 0
    while a < len(w) and w[a] in _PUNCT:
        a += 1
    b = len(w)
    while b > a and w[b - 1] in _PUNCT:
        b -= 1
    return w[:a], w[a:b], w[b:]


def is_syllable(core: str) -> bool:
    """Phần chữ có thể là một âm tiết tiếng Việt (hoặc một số) không."""
    if not core:
        return False
    if core.isdigit():
        return True
    return all(ch in _VN_SET for ch in core) and bool(_SYLL.match(base(core)))


def recase(word: str, like: str) -> str:
    """Giữ chữ và dấu của word, lấy hoa thường theo like (Tesseract ít khi nhầm hoa thường, VietOCR hay nhầm)."""
    if len(word) != len(like):
        return word
    return "".join(a.upper() if b.isupper() else a.lower() if b.islower() else a for a, b in zip(word, like))


def pick(v: str, t: str, lex: Counter) -> str:
    """Chọn một từ từ hai cách đọc: v của VietOCR, t của Tesseract. lex: số lần mỗi từ được cả hai cùng đọc giống nhau trong cuốn này."""
    pv, cv, sv = split_punct(v)
    pt, ct, st = split_punct(t)
    if not ct:
        return t if not cv else v
    if not cv:
        return t
    lead, tail = (pt, st) if base(cv) == base(ct) or not is_syllable(cv) else (pv or pt, sv or st)
    if cv == ct:
        return lead + ct + tail
    if base(cv) == base(ct):  # cùng chữ cái, khác dấu hoặc hoa thường: dấu theo bên được cuốn sách xác nhận nhiều hơn
        nv, nt = lex[cv.lower()], lex[ct.lower()]
        word = ct if nt > nv * 1.5 + 1 and is_syllable(ct) else cv if is_syllable(cv) else ct
        return lead + recase(word, ct) + tail
    okv, okt = is_syllable(cv), is_syllable(ct)
    if okv != okt:
        return (pv or pt) + cv + (sv or st) if okv else t
    if okv:  # cả hai đều đọc ra tiếng Việt nhưng khác chữ: theo bên cuốn sách biết; ngang nhau thì theo VietOCR
        return t if lex[ct.lower()] > lex[cv.lower()] * 2 + 2 else (pv or pt) + cv + (sv or st)
    return t  # cả hai đều không ra chữ (chữ Hán, hình): giữ cách đọc của Tesseract, để còn nhận ra là rác


def merge_piece(v_text: str, t_words: list, prob: float, lex: Counter) -> str:
    """Ghép một đoạn của dòng. Đoạn mà Tesseract đọc ra toàn ký hiệu (chữ Hán, hình quẻ, nét kẻ bảng) thì giữ nguyên của Tesseract:
    VietOCR sẽ bịa ra chữ trông như tiếng Việt ở những chỗ đó."""
    v_words = v_text.split()
    cores = [split_punct(w)[1] for w in t_words]
    real = sum(1 for c in cores if is_syllable(c))
    if not v_words or real * 2 < len(t_words) or (prob < 0.45 and real < len(t_words)):
        return " ".join(t_words)
    if len(v_words) == len(t_words):
        return " ".join(pick(v, t, lex) for v, t in zip(v_words, t_words))
    out = []
    sm = difflib.SequenceMatcher(None, [base(split_punct(w)[1]) for w in v_words], [base(c) for c in cores], autojunk=False)
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal" or (op == "replace" and a1 - a0 == b1 - b0):
            out += [pick(v, t, lex) for v, t in zip(v_words[a0:a1], t_words[b0:b1])]
        elif op == "delete":  # VietOCR có, Tesseract không: giữ nếu là tiếng Việt
            out += [w for w in v_words[a0:a1] if is_syllable(split_punct(w)[1])]
        elif op == "insert":  # Tesseract có, VietOCR bỏ sót (thường là dấu câu, số)
            out += [w for w in t_words[b0:b1] if not split_punct(w)[1] or split_punct(w)[1].isdigit()]
        else:  # số từ lệch nhau: theo bên có nhiều âm tiết tiếng Việt hơn
            vs, ts = v_words[a0:a1], t_words[b0:b1]
            out += vs if sum(is_syllable(split_punct(w)[1]) for w in vs) >= sum(is_syllable(split_punct(w)[1]) for w in ts) else ts
    return " ".join(out)


# ---------- engine "viet": Tesseract tìm bố cục, VietOCR đọc chữ ----------

def tsv_lines(tess: str, tdir: Path, img: Path, lang: str, env: dict) -> list:
    """Các dòng của một trang theo Tesseract: [(khối, đoạn, dòng), [(x, y, w, h, chữ), …]], theo thứ tự đọc."""
    r = subprocess.run([tess, str(img), "stdout", "--tessdata-dir", str(tdir), "-l", lang, "--psm", "3", "-c", "tessedit_create_tsv=1"],
                       capture_output=True, env=env, creationflags=QUIET)
    lines: dict = {}
    for x in csv.DictReader(io.StringIO(r.stdout.decode("utf-8", errors="replace")), delimiter="\t", quoting=csv.QUOTE_NONE):
        if x.get("level") == "5" and (x.get("text") or "").strip():
            key = (int(x["block_num"]), int(x["par_num"]), int(x["line_num"]))
            lines.setdefault(key, []).append((int(x["left"]), int(x["top"]), int(x["width"]), int(x["height"]), x["text"].strip()))
    return list(lines.items())


def pieces_of(words: list) -> list:
    """Cắt một dòng thành các đoạn ngắn (rộng chừng 12 lần chiều cao chữ): VietOCR thu mỗi ảnh về cao 32 điểm, dòng dài nguyên thì chữ quá nhỏ.
    Chỗ cách xa (cột của bảng) cũng cắt."""
    h = max(w[3] for w in words) or 20
    out, cur = [], []
    for w in words:
        if cur and (w[0] + w[2] - cur[0][0] > 12 * h or w[0] - (cur[-1][0] + cur[-1][2]) > 2.5 * h):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def load_viet():
    import torch
    from vietocr.tool.config import Cfg
    from vietocr.tool.predictor import Predictor
    MODELS.mkdir(parents=True, exist_ok=True)
    weights = MODELS / "vgg_transformer.pth"
    if not weights.is_file():
        print("Tải model VietOCR (chừng 150 MB, một lần)…", flush=True)
        import urllib.request
        tmp = weights.with_suffix(".part")
        urllib.request.urlretrieve(VIET_WEIGHTS, tmp)
        tmp.replace(weights)
    cfg = Cfg.load_config_from_name("vgg_transformer")
    cfg["weights"] = str(weights)
    cfg["cnn"]["pretrained"] = False  # không cần tải thêm trọng số ImageNet: file trên đã đủ
    cfg["device"] = "cuda:0" if torch.cuda.is_available() else "cpu"
    cfg["predictor"]["beamsearch"] = False
    return Predictor(cfg), cfg["device"]


def run_viet(src: Path, pages: list, args, tess: str, tdir: Path, tmp: Path) -> dict:
    import pymupdf
    from PIL import Image
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    layout, done = {}, [0]

    def one(i: int):
        img = tmp / f"p{i + 1:04d}.png"
        doc_i = pymupdf.open(str(src))  # mỗi luồng mở riêng: PyMuPDF không an toàn khi dùng chung giữa các luồng
        doc_i[i].get_pixmap(dpi=args.dpi, colorspace=pymupdf.csGRAY).save(str(img))
        doc_i.close()
        layout[i] = tsv_lines(tess, tdir, img, args.lang, env)
        done[0] += 1
        print(f"PROGRESS {min(29, done[0] * 30 // len(pages))}", flush=True)

    workers = max(1, (os.cpu_count() or 2) - 1)
    print(f"OCR {len(pages)} pages: layout with Tesseract ({workers} at a time), text with VietOCR", flush=True)
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, pages))
    pred, device = load_viet()
    print(f"VietOCR on {device}", flush=True)
    import torch
    read = {}  # (trang, số dòng, số đoạn) -> (chữ VietOCR, độ tin)
    step = 32 if device != "cpu" else 2

    def recognise(crops: list) -> tuple:
        """Đọc một loạt ảnh đoạn chữ. VietOCR gom ảnh cùng bề rộng thành một lô và giải mã cả lô cho tới khi câu dài nhất xong,
        nên xếp ảnh theo bề rộng rồi chia nhóm 128: mỗi nhóm chỉ vài lô, lô nào cũng đầy (nhanh gấp mấy lần gọi lẻ từng trang)."""
        order = sorted(range(len(crops)), key=lambda k: crops[k].width / max(1, crops[k].height))
        sents, probs = [""] * len(crops), [0.0] * len(crops)
        for a in range(0, len(order), 128):
            idx = order[a:a + 128]
            with torch.autocast("cuda", dtype=torch.float16, enabled=device != "cpu"):
                s, p = pred.predict_batch([crops[k] for k in idx], return_prob=True)
            for k, sent, pr in zip(idx, s, p):
                sents[k], probs[k] = sent, float(pr)
        return sents, probs

    for s in range(0, len(pages), step):
        crops, keys = [], []
        for i in pages[s:s + step]:
            im = Image.open(tmp / f"p{i + 1:04d}.png").convert("RGB")
            for li, (_key, words) in enumerate(layout[i]):
                for pi, piece in enumerate(pieces_of(words)):
                    x0, y0 = min(w[0] for w in piece), min(w[1] for w in piece)
                    x1, y1 = max(w[0] + w[2] for w in piece), max(w[1] + w[3] for w in piece)
                    pad = max(3, (y1 - y0) // 5)
                    crops.append(im.crop((max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad), min(im.height, y1 + pad))))
                    keys.append((i, li, pi))
            im.close()
        if crops:
            sents, probs = recognise(crops)
            for k, sent, p in zip(keys, sents, probs):
                read[k] = (sent, float(p))
        print(f"PROGRESS {min(99, 30 + (s + step) * 69 // len(pages))}", flush=True)
    # Từ mà hai bên đọc giống hệt nhau: danh sách từ của chính cuốn sách, dùng để phân xử chỗ hai bên đọc khác dấu.
    lex = Counter()
    for i in pages:
        for li, (_key, words) in enumerate(layout[i]):
            for pi, piece in enumerate(pieces_of(words)):
                v = read.get((i, li, pi), ("", 0.0))[0].split()
                if len(v) == len(piece):
                    for a, w in zip(v, piece):
                        ca, cb = split_punct(a)[1], split_punct(w[4])[1]
                        if ca and ca.lower() == cb.lower():
                            lex[ca.lower()] += 1
    texts = {}
    for i in pages:
        out, prev = [], None
        for li, (key, words) in enumerate(layout[i]):
            if prev is not None and key[:2] != prev[:2]:
                out.append("")
            prev = key
            parts = []
            for pi, piece in enumerate(pieces_of(words)):
                v, p = read.get((i, li, pi), ("", 0.0))
                parts.append(merge_piece(v, [w[4] for w in piece], p, lex))
            out.append(" ".join(x for x in parts if x))
        texts[i] = "\n".join(out)
    return texts


def run_tesseract(src: Path, pages: list, args, tess: str, tdir: Path, tmp: Path) -> dict:
    import pymupdf
    texts, done = {}, [0]
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}  # mỗi tiến trình một nhân: chạy nhiều trang song song nhanh hơn

    def one(i: int):
        img = tmp / f"p{i + 1:04d}.png"
        doc_i = pymupdf.open(str(src))
        doc_i[i].get_pixmap(dpi=args.dpi, colorspace=pymupdf.csGRAY).save(str(img))
        doc_i.close()
        r = subprocess.run([tess, str(img), "stdout", "--tessdata-dir", str(tdir), "-l", args.lang, "--psm", "3"],
                           capture_output=True, env=env, creationflags=QUIET)
        img.unlink(missing_ok=True)
        texts[i] = r.stdout.decode("utf-8", errors="replace")
        done[0] += 1
        print(f"PROGRESS {min(99, done[0] * 100 // len(pages))}", flush=True)

    workers = max(1, (os.cpu_count() or 2) - 1)
    print(f"OCR {len(pages)} pages, {workers} at a time, language {args.lang}", flush=True)
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, pages))
    return texts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", default="data/md")
    ap.add_argument("--engine", default="auto", choices=("auto", "viet", "tesseract"))
    ap.add_argument("--lang", default="vie")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--pages", default="", help="e.g. 1-20 (default: all)")
    ap.add_argument("--name", default="", help="output file name without .md (default: the PDF's name)")
    args = ap.parse_args()
    tess = tesseract_bin()
    if not tess:
        sys.exit("Tesseract is not installed. Run: winget install UB-Mannheim.TesseractOCR (or tools/setup.bat)")
    if not all((TESSDATA / f"{l}.traineddata").is_file() for l in args.lang.split("+")):
        sys.exit(f"Missing language data in {TESSDATA} for '{args.lang}'. Run tools/setup.bat.")
    engine = args.engine
    if engine == "auto":
        engine = "viet" if args.lang == "vie" and viet_ready() else "tesseract"
    elif engine == "viet" and not viet_ready():
        sys.exit("Engine 'viet' needs PyTorch and VietOCR: pip install -r tools/requirements-ocr.txt (see that file).")
    import pymupdf
    src = Path(args.pdf)
    out = Path(args.out) / ((args.name or src.stem) + ".md")
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(src))
    pages = page_range(args.pages, doc.page_count)
    doc.close()
    tdir = tessdata_dir()
    tmp = Path(tempfile.mkdtemp(prefix="ocr-"))
    try:
        texts = (run_viet if engine == "viet" else run_tesseract)(src, pages, args, tess, tdir, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    how = "Tesseract tìm dòng, VietOCR đọc chữ" if engine == "viet" else f"Tesseract, {args.lang}"
    lines = [f"<!-- bản chữ do OCR ({how}) từ PDF quét: có thể sai dấu, vỡ dòng; trích nguyên văn thì đối chiếu ảnh trang -->", ""]
    for i in pages:
        body = "\n".join(l.rstrip() for l in texts.get(i, "").splitlines()).strip()
        lines += [f"<!-- page {i + 1} -->", "", body, ""]
    # Bảng chỉnh dấu thuật ngữ (tools/ocr_terms.py) là của dự án gốc, không mang theo; có file đó thì dùng.
    try:
        from ocr_terms import fix_terms
        text, fixed = fix_terms("\n".join(lines))
        if fixed:
            print(f"chỉnh dấu {fixed} từ theo bảng thuật ngữ (tools/ocr_terms.py)")
        lines = text.split("\n")
    except ImportError:
        pass
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    chars = sum(len(t.strip()) for t in texts.values())
    print(f"ok: {out} ({len(pages)} pages, {chars} characters, engine {engine})")


if __name__ == "__main__":
    main()
