# Converter: Sách và Video

Hai dịch vụ nhỏ chạy trên máy (Windows, Python), để biến sách và video thành bản chữ rồi đọc ngay trong trình duyệt:

- **`sach/`** (http://localhost:6470): thêm sách PDF, Word, PowerPoint, Excel, .txt; tự chuyển sang bản chữ Markdown, PDF quét thì tự OCR tiếng Việt. Đọc **cùng bản gốc** (PDF bên trái, chữ bên phải) hoặc **chỉ bản chữ**.
- **`video/`** (http://localhost:6471): thêm, xóa video; tự chép lời có mốc giờ. Xem video cạnh lời: **chữ đang đọc được tô và gạch chân, lời tự cuộn theo**; bấm mốc giờ hoặc **bấm một chữ là video chạy từ chữ đó**.

Mỗi dịch vụ là một thư mục độc lập: server riêng, môi trường Python riêng, cài đặt riêng. Dùng một cái hay cả hai đều được.

## Tính năng

**Sách**
- "＋ Thêm sách" hoặc thả file vào cửa sổ. Server chuyển sang chữ ở nền, từng cuốn một, có phần trăm.
- PDF có lớp chữ: lấy chữ bằng PyMuPDF, bỏ dòng lặp ở đầu và cuối trang (watermark, tên sách), đổi font TCVN3 cũ sang Unicode.
- PDF chỉ có ảnh quét: OCR bằng Tesseract; máy có card NVIDIA thì ghép thêm VietOCR để đúng dấu tiếng Việt hơn.
- Đọc cùng bản gốc: vạch "Trang N" trong bản chữ, bấm thì PDF nhảy tới trang đó; nút "Tới trang đang đọc".
- Nhớ cuốn đang đọc và chỗ đang cuộn. Mỗi cuốn có link riêng (`#tên sách`).

**Video**
- "＋ Thêm video" hoặc thả file (mp4, mkv, webm, mov, mp3, m4a…). Server chép lời ở nền bằng faster-whisper (GPU NVIDIA nếu có), từng video một.
- Lời chia đoạn, mỗi đoạn có mốc `[mm:ss]` bấm để tua.
- Giờ của từng chữ (whisper word timestamps): chữ đang đọc tô son, gạch chân; bấm một chữ là video chạy từ đúng chữ đó.
- Video tải về chỉ có hình: để phần tiếng ở `video/data/goc/_tiếng/<cùng tên>.m4a`, server chép lời từ đó và phát tiếng theo video.
- Xóa video: chuyển video, lời, giờ từng chữ vào `video/data/_da-xoa/` (chưa mất hẳn).

**Chung:** nút A− A+ đổi cỡ chữ, ô lọc theo tên, giao diện giấy kem viền son.

## Cài đặt

Cần **Windows** và **Python 3.13** (`py -3.13`).

1. Tải repo về:
   ```
   git clone https://github.com/<tài-khoản>/converter.git
   ```
2. Bấm đúp `sach\setup.bat` và/hoặc `video\setup.bat`. Mỗi script tạo `.venv` trong thư mục của nó rồi cài:

   | Dịch vụ | Luôn cài | Máy có card NVIDIA thì cài thêm |
   |---|---|---|
   | Sách | `sach/requirements.txt` (PyMuPDF, markitdown, pillow), Tesseract OCR qua winget | PyTorch + VietOCR (`sach/requirements-ocr.txt`, khoảng 3 GB) |
   | Video | `video/requirements.txt` (faster-whisper, yt-dlp) | thư viện CUDA (`video/requirements-gpu.txt`, khoảng 1 GB) |

3. Bấm đúp `start.bat` của dịch vụ: server chạy ẩn, trình duyệt tự mở.

Lần chép lời đầu tiên tải model nhận giọng nói (khoảng 1,6 GB với GPU, 500 MB với CPU). Lần OCR đầu tiên bằng VietOCR tải model khoảng 150 MB.

Cài bằng tay (mọi hệ điều hành có Python, OCR cần Tesseract trong PATH):
```
cd sach   # hoặc video
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python server.py --open
```
`requirements.txt` ở thư mục gốc gom thư viện của cả hai dịch vụ, cho ai muốn dùng chung một môi trường.

### Tùy chỉnh
- Đổi cổng: biến môi trường `SACH_PORT` (mặc định 6470), `VIDEO_PORT` (mặc định 6471).
- Chép lời không dùng GPU: `VIDEO_GPU=cpu`.

## Dùng từ dòng lệnh
```
cd sach
.venv\Scripts\python tools\convert.py "data/goc/sach.pdf" -o data/md          # PDF có chữ, Word…
.venv\Scripts\python tools\ocr.py "data/goc/sach-quet.pdf" -o data/md         # PDF quét (--lang vie+chi_tra cho sách nhiều chữ Hán)

cd video
.venv\Scripts\python tools\transcribe.py "data/goc/bai-giang.mp4" -o data/md  # chép lời + giờ từng chữ
.venv\Scripts\python tools\word_times.py --all                                # giờ từng chữ cho lời có sẵn (không sửa lời)
```

## Dữ liệu
Không có database, thư mục chính là kho (không đưa lên git, xem `.gitignore`):
- `sach/data/goc/` bản gốc, `sach/data/md/` bản chữ cùng tên.
- `video/data/goc/` video, `video/data/md/` lời, `video/data/word-times/` giờ từng chữ, `video/data/_da-xoa/` video đã xóa.

## Cấu trúc
```
sach/  video/
  server.py        server HTTP (thư viện chuẩn của Python), hàng chờ chạy nền
  static/          giao diện: HTML, CSS, JS thuần, không cần build
  tools/           công cụ dòng lệnh (sách: convert.py, ocr.py, tessdata/; video: transcribe.py, word_times.py)
  skills/          hướng dẫn cho Claude Code: cách làm, ước tính thời gian, lỗi hay gặp
  requirements*.txt, setup.bat, start.bat
```

API (JSON, chỉ nhận từ chính trang của app):
- Sách: `GET /api/books`, `GET /api/book?name=`, `POST /api/upload` (thân là file, tên ở header `X-Name`), `POST /api/retry`.
- Video: `GET /api/videos`, `GET /api/video?name=`, `POST /api/upload`, `POST /api/delete`, `POST /api/retry`, `GET /media/<file>` (có Range để tua).

## Bảo mật
Server chỉ nghe ở `127.0.0.1`, chỉ nhận yêu cầu có Host và Origin là chính nó (không bật CORS), trang có Content-Security-Policy, nội dung sách và lời luôn được escape trước khi hiện. Không có đăng nhập: đừng mở cổng ra mạng ngoài.

## Ghi công
- Dữ liệu OCR `sach/tools/tessdata/*.traineddata` lấy từ [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best) (Apache License 2.0).
- [Tesseract](https://github.com/tesseract-ocr/tesseract), [VietOCR](https://github.com/pbcquoc/vietocr), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [PyMuPDF](https://github.com/pymupdf/PyMuPDF), [markitdown](https://github.com/microsoft/markitdown).
