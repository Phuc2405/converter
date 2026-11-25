"""Convert PDF / Word / PowerPoint / Excel files to Markdown.

Usage
  python tools/convert.py <file-or-folder> [more ...] [-o OUTDIR] [--force]

- PDF  -> PyMuPDF (fast). Page markers <!-- page N --> are kept so you can cite page numbers.
- Word / PowerPoint / Excel -> Microsoft MarkItDown (https://github.com/microsoft/markitdown).
- Default output folder is data/md (run from the service folder). Existing .md files are skipped unless --force.
- PDFs with no text layer, or with a damaged one (lost accents, spaced letters), are flagged
  because they need OCR. See .claude/rules/documents.md.
"""
import argparse
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EXTS = {".pdf", ".docx", ".pptx", ".xlsx"}
STRAY = set("~|{}^`")


def quality_issue(text: str, pages: int = 0, empty_pages: int = 0):
    """Return a short reason if the extracted text looks unusable, else None."""
    if pages and empty_pages / pages > 0.5:
        return f"{empty_pages}/{pages} pages have no text (image-only scan)"
    body = text[3000:] if len(text) > 6000 else text
    words = body.split()[:6000]
    if len(words) < 150:
        return "almost no text (image-only scan)"
    singles = sum(1 for w in words if len(w) == 1 and w.isalpha())
    if singles / len(words) > 0.30:
        return "letters are split apart (bad OCR layer)"
    letters = sum(ch.isalpha() for ch in body[:60000]) or 1
    stray = sum(ch in STRAY for ch in body[:60000])
    if stray / letters > 0.004:
        return "accents are damaged (bad OCR layer)"
    return None


# Old Vietnamese font encoding TCVN3 (ABC, fonts .VnTime ...). Text extracts as "NguyÔn Ngäc Phi".
# Upper-case fonts (.VnTimeH) reuse the lower-case codes, so case is lost there.
_TCVN3_SRC = ("¡¢£¤¥¦§¨©ª«¬­−μ®µ¶·¸¹»¼½¾ÆÇÈÉÊËÌÎÏÐÑÒÓÔÕÖ×ØÜÝÞßáâãäåæçèéêëìíîïñòóôõö÷øùúûüýþ")
_TCVN3_DST = ("ĂÂÊÔƠƯĐăâêôơưưàđàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ")
_TCVN3 = str.maketrans(_TCVN3_SRC, _TCVN3_DST)
_TCVN3_SIGNS = set("©¨ª«®¹ËÓÔäÖµ¸Õ−μ")


def fix_tcvn3(text: str):
    """Convert TCVN3 text to Unicode if it looks like TCVN3. Returns (text, converted)."""
    viet = sum(0x1EA0 <= ord(c) <= 0x1EF9 for c in text)
    signs = sum(c in _TCVN3_SIGNS for c in text)
    if signs > 50 and signs > 3 * viet:
        return text.translate(_TCVN3), True
    return text, False


def span_text(span) -> str:
    """Text of one span. Spans in TCVN3 fonts (.VnTime, VnArial...) are converted to Unicode."""
    t, font = span["text"], span["font"].lstrip(".")
    if font.startswith("Vn"):
        t = t.translate(_TCVN3)
        if font.split(",")[0].endswith("H"):  # .VnTimeH etc. = all-caps fonts
            t = t.upper()
    return t


def page_text(page) -> str:
    blocks = []
    for b in page.get_text("dict")["blocks"]:
        lines = ["".join(span_text(s) for s in l["spans"]) for l in b.get("lines", [])]
        if lines:
            blocks.append("\n".join(lines))
    return "\n".join(blocks)


# Chữ mà một số file PDF dùng sai mã: "ƣ" (U+01A3) thay cho "ư", và bộ thủ Khang Hy (U+2F00–2FDF) thay cho chữ Hán thường.
# Nhìn thì giống nhưng tìm kiếm không ra ("Chƣơng" không khớp "Chương"). Đổi về chữ đúng; không đụng chữ nào khác.


def fix_lookalikes(text: str) -> str:
    import unicodedata
    if not any(c in "ƣƢ" or "⼀" <= c <= "⿕" for c in text):
        return text
    return "".join("ư" if c == "ƣ" else "Ư" if c == "Ƣ" else unicodedata.normalize("NFKC", c) if "⼀" <= c <= "⿕" else c for c in text)


def running_lines(pages: list) -> set:
    """Dòng lặp lại ở rất nhiều trang (tên trang web chèn vào từng trang, đầu trang, cuối trang: "KhoSachQuy · Tuyển tập số 1",
    "https://thuviensach.vn"): không phải nội dung sách, chỉ làm nhiễu khi tìm và khi trích. Trả các dòng (đã bỏ số) cần bỏ."""
    import re
    from collections import Counter
    seen = Counter()
    for t in pages:
        seen.update({re.sub(r"\d+", "#", ln.strip()) for ln in t.split("\n") if 3 <= len(ln.strip()) <= 90})
    need = max(8, int(len(pages) * 0.4))
    return {k for k, n in seen.items() if n >= need and not re.fullmatch(r"[#\s./-]*", k)}


def pdf_to_text(path: Path):
    import re
    import pymupdf  # PyMuPDF

    pages, empty = [], 0
    with pymupdf.open(path) as doc:
        n = len(doc)
        for page in doc:
            t = fix_lookalikes(page_text(page).strip())
            if len(t) < 20:
                empty += 1
            pages.append(t)
    drop = running_lines(pages)
    if drop:
        pages = ["\n".join(ln for ln in t.split("\n") if re.sub(r"\d+", "#", ln.strip()) not in drop) for t in pages]
        print(f"  (bỏ {len(drop)} dòng lặp lại ở đầu trang, cuối trang: " + "; ".join(sorted(drop))[:160] + ")")
    return "\n\n".join(f"<!-- page {i} -->\n\n{t.strip()}" for i, t in enumerate(pages, 1)), n, empty


def collect(paths):
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.suffix.lower() in EXTS)
        elif p.suffix.lower() in EXTS:
            files.append(p)
        else:
            print(f"skip (unsupported): {p}")
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("-o", "--out", default="data/md")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = None

    for src in collect(args.paths):
        out = out_dir / (src.stem + ".md")
        if out.exists() and not args.force:
            print(f"exists, skipped: {out}")
            continue
        t0 = time.time()
        pages = empty = 0
        try:
            if src.suffix.lower() == ".pdf":
                text, pages, empty = pdf_to_text(src)
            else:
                if md is None:
                    from markitdown import MarkItDown
                    md = MarkItDown(enable_plugins=False)
                text = md.convert(str(src)).text_content
        except ImportError as e:
            sys.exit(f"Missing package ({e}). Run tools/setup.bat first, or: pip install -r tools/requirements.txt")
        except Exception as e:  # keep going with the next file
            print(f"FAILED: {src.name} ({e})")
            continue
        text, was_tcvn3 = fix_tcvn3(text)
        out.write_text(text, encoding="utf-8")
        issue = quality_issue(text, pages, empty) if src.suffix.lower() == ".pdf" else None
        flag = f"\n    WARNING needs OCR: {issue}" if issue else ""
        if was_tcvn3:
            flag += "\n    note: converted from old TCVN3 font encoding to Unicode, spot-check a page"
        print(f"ok: {src.name} -> {out} ({len(text):,} chars, {time.time() - t0:.0f}s){flag}")


if __name__ == "__main__":
    main()
