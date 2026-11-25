"""Turn a video or audio file (or a YouTube link) into a Markdown transcript with timestamps.

Usage
  python tools/transcribe.py <file-or-youtube-url> [-o data/md] [--lang vi] [--model small]

- YouTube link: uses the existing subtitles first (fast, free). Falls back to speech recognition.
- Local file: speech recognition with faster-whisper. Uses the NVIDIA GPU when there is one (CUDA libraries from the pip
  packages nvidia-cublas-cu12 and nvidia-cudnn-cu12, float16, model large-v3-turbo), otherwise the CPU (int8, model small).
  On a laptop RTX 5060 one minute of speech takes about 5 to 10 seconds on the GPU, 20 seconds or more on the CPU.
- Prints "PROGRESS <percent>" lines so the app can show how far it got.
- Output: <out>/<name>.md (default data/md) with a [mm:ss] mark at the start of each paragraph,
  so notes can point back to the exact moment in the video. Speech recognition also saves the start time of every word
  (data/word-times/<name>.json, tools/word_times.py) so the app can highlight the word being spoken.

Models: tiny, base, small, medium, large-v3, large-v3-turbo. Default: large-v3-turbo on GPU, small on CPU.
--lang auto (default) lets the model detect the language from the first 30 seconds (some videos are in English or Chinese).
For Vietnamese, "erax-ai/EraX-WoW-Turbo-V1.1-CT2" is a model tuned for Vietnamese.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# Câu Whisper hay bịa ở chỗ có nhạc, tiếng ồn hay lời chào đầu, cuối video (học từ phụ đề YouTube), không phải lời người giảng.
HALLU = re.compile(r"subscribe|ghiền mì gõ|đăng ký kênh|bỏ lỡ những video|like và share|amara\.org|subtitles by|thanks for watching", re.I)


def ts(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def to_paragraphs(segments, gap=2.5, max_chars=600):
    """segments: list of (start, end, text). Group into paragraphs with a start time."""
    paras, cur, start, last_end = [], [], None, 0.0
    for s, e, t in segments:
        t = t.strip()
        if not t:
            continue
        if cur and (s - last_end > gap or sum(len(x) for x in cur) > max_chars):
            paras.append((start, " ".join(cur)))
            cur = []
        if not cur:
            start = s
        cur.append(t)
        last_end = e
    if cur:
        paras.append((start, " ".join(cur)))
    return paras


def write_md(out: Path, title: str, source: str, method: str, paras):
    lines = [f"# {title}", "", f"- **Nguồn** {source}", f"- **Cách lấy** {method}", ""]
    for start, text in paras:
        lines += [f"**[{ts(start)}]** {text}", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"ok: {out} ({len(paras)} paragraphs)")


def parse_vtt(path: Path):
    segs, recent = [], []
    blocks = path.read_text(encoding="utf-8", errors="ignore").split("\n\n")
    for b in blocks:
        m = re.search(r"(\d+):(\d+):(\d+)\.\d+\s+-->\s+(\d+):(\d+):(\d+)", b)
        if not m:
            continue
        h, mi, s, h2, m2, s2 = map(int, m.groups())
        # YouTube auto-subs roll: each line shows up in several cues. Keep a line only if it is not among the last few kept.
        for line in (l for l in b.splitlines()[1:] if "-->" not in l):
            text = re.sub(r"<[^>]+>", "", line).strip()
            if text and text not in recent:
                segs.append((h * 3600 + mi * 60 + s, h2 * 3600 + m2 * 60 + s2, text))
                recent = (recent + [text])[-4:]
    return segs


def youtube(url, out_dir, lang, model):
    tmp = Path(tempfile.mkdtemp())
    base = [sys.executable, "-m", "yt_dlp", "--js-runtimes", "node", "--no-playlist", "-o", str(tmp / "%(title)s.%(ext)s")]
    title = subprocess.run([sys.executable, "-m", "yt_dlp", "--js-runtimes", "node", "--no-playlist", "--get-title", url], capture_output=True, text=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"}).stdout.strip() or "video"
    subprocess.run(base + ["--skip-download", "--write-subs", "--write-auto-subs",
                           "--sub-langs", f"{lang}.*,{lang}", "--sub-format", "vtt", url], capture_output=True)
    vtts = sorted(tmp.glob("*.vtt"))
    out = out_dir / (safe(title) + ".md")
    if vtts:
        write_md(out, title, url, f"phụ đề YouTube ({vtts[0].suffixes[-2].strip('.')})", to_paragraphs(parse_vtt(vtts[0])))
        return
    print("No subtitles found, downloading audio for speech recognition...")
    subprocess.run(base + ["-x", "--audio-format", "m4a", url], check=True)
    audio = next(tmp.glob("*.m4a"))
    whisper(audio, out, title, url, lang, model)


def cuda_ready() -> bool:
    """Make the CUDA DLLs from the nvidia-* pip packages loadable (Windows), then ask CTranslate2 whether a GPU is there."""
    try:
        import nvidia  # noqa: F401  (namespace package of nvidia-cublas-cu12, nvidia-cudnn-cu12)
        for base in getattr(nvidia, "__path__", []):
            for d in Path(base).glob("*/bin"):
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(str(d))
    except ImportError:
        pass
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


def load_model(model_name: str, device: str = "auto"):
    """device "auto": GPU (float16) if it works, else CPU (int8). "cpu": always the CPU (app setting "Dùng GPU" off). Returns (model, device, model name)."""
    from faster_whisper import WhisperModel
    if device != "cpu" and cuda_ready():
        name = model_name or "large-v3-turbo"
        try:
            return WhisperModel(name, device="cuda", compute_type="float16"), "GPU", name
        except Exception as e:  # noqa: BLE001  (missing DLL, driver too old…): fall back to the CPU
            print(f"GPU not usable ({type(e).__name__}: {e}), using the CPU.", flush=True)
    name = model_name or "small"
    return WhisperModel(name, device="cpu", compute_type="int8"), "CPU", name


def whisper_compat():
    """Newer PyAV dropped open(metadata_errors=...), which faster-whisper still passes."""
    import av
    if not getattr(av.open, "_patched", False):
        _open = av.open

        def _compat_open(*a, metadata_errors=None, **k):
            return _open(*a, **k)
        _compat_open._patched = True
        av.open = _compat_open


def whisper(path: Path, out: Path, title: str, source: str, lang: str, model_name: str, device: str = "auto"):
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        sys.exit("faster-whisper is not installed. Run tools/setup.bat first.")
    whisper_compat()
    print("Loading model (first run downloads it)...", flush=True)
    model, device, model_name = load_model(model_name, device)
    lang = None if lang in ("", "auto") else lang
    # condition_on_previous_text=False: không lấy câu vừa chép làm mồi cho câu sau. Bật thì một câu bịa (Whisper hay bịa "Hãy subscribe
    # cho kênh…" ở đoạn nhạc, lời chào đầu video) lặp mãi, nuốt mất mấy phút lời thật (video Đỗ Đức Ngọc tập 5 mất gần 5 phút).
    segments, info = model.transcribe(str(path), language=lang, vad_filter=True, word_timestamps=True, condition_on_previous_text=False)
    print(f"Model {model_name} on {device}, language {info.language}, {ts(info.duration)} of audio", flush=True)
    segs, words, last = [], [], -1
    dropped = 0
    for seg in segments:
        if HALLU.search(seg.text):  # câu bịa quen thuộc của Whisper: bỏ cả đoạn
            dropped += 1
            continue
        segs.append((seg.start, seg.end, seg.text))
        words += [(w.start, w.word.strip()) for w in seg.words or []]
        pct = int(seg.end * 100 / info.duration) if info.duration else 0
        if pct != last:
            print(f"PROGRESS {min(pct, 99)}", flush=True)
            last = pct
    if dropped:
        print(f"bỏ {dropped} đoạn Whisper bịa (subscribe, đăng ký kênh…)", flush=True)
    write_md(out, title, source, f"faster-whisper, model {model_name} ({device}), ngôn ngữ {info.language}", to_paragraphs(segs))
    try:  # mốc giờ của từng chữ, để app tô sáng chữ đang đọc (tools/word_times.py)
        from word_times import save
        save(out, words)
    except Exception as e:  # noqa: BLE001  (thiếu mốc thì app chỉ tô cả đoạn, bản chữ vẫn dùng được)
        print(f"word times skipped: {type(e).__name__}: {e}", flush=True)


def safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip()[:120]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("-o", "--out", default="data/md")
    ap.add_argument("--lang", default="auto", help="vi, zh, en ... (auto = detect)")
    ap.add_argument("--model", default="", help="empty = large-v3-turbo on GPU, small on CPU")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu"], help="auto = GPU when there is one, cpu = never use the GPU")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.src.startswith(("http://", "https://")):
        youtube(args.src, out_dir, "vi" if args.lang == "auto" else args.lang, args.model)
    else:
        p = Path(args.src)
        whisper(p, out_dir / (p.stem + ".md"), p.stem, str(p), args.lang, args.model, args.device)


if __name__ == "__main__":
    main()
