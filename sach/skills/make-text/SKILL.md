---
name: make-text
description: Tạo bản chữ (.md) cho sách trong dịch vụ Sách: PDF có lớp chữ, Word, PowerPoint, Excel, .txt; PDF chỉ có ảnh quét thì OCR (Tesseract, có card NVIDIA thì ghép thêm VietOCR). Dùng khi được nhờ "thêm sách", "convert sang md", "tạo bản chữ", "OCR", "bản chữ sai dấu", hoặc một cuốn trong data/goc chưa có bản chữ.
---

# Tạo bản chữ cho sách

Dịch vụ Sách (`server.py`, http://localhost:6470) giữ bản gốc ở `data/goc/`, bản chữ ở `data/md/<cùng tên>.md`. Không có database: thư mục chính là kho. Bản gốc không sửa, không đổi tên (app ghép gốc với chữ theo tên giống hệt).

## 1. Làm ở đâu
- **Trong app (cách chính):** nút "＋ Thêm sách" hoặc thả file vào cửa sổ. Server lưu file vào `data/goc/`, rồi chuyển sang chữ ở nền, **lần lượt từng cuốn** (một hàng chờ, không chạy song song). Danh sách bên trái hiện "Đang tạo bản chữ", "Đang OCR" kèm phần trăm. Lỗi thì có nút "Thử lại".
- Tắt server giữa chừng: lần mở sau, cuốn nào có bản gốc mà chưa có bản chữ thì tự xếp lại vào hàng chờ.
- **Dòng lệnh** (chạy trong thư mục `sach/`):
  - PDF có chữ, Word…: `.venv\Scripts\python tools\convert.py "data/goc/<file>" -o data/md --force`
  - PDF quét: `.venv\Scripts\python tools\ocr.py "data/goc/<file>.pdf" -o data/md` (`--lang vie+chi_tra` cho sách nhiều chữ Hán, `--pages 1-20` để thử).

## 2. Hai bước của server
1. `tools/convert.py` lấy lớp chữ của PDF (PyMuPDF) hoặc của Word, PowerPoint, Excel (markitdown). PDF giữ dấu `<!-- page N -->` ở đầu mỗi trang; app biến dấu này thành vạch "Trang N", bấm thì bản gốc nhảy tới trang đó. Script bỏ dòng lặp ở đầu, cuối trang (watermark, tên sách) và sửa chữ trông giống nhau ("ƣ" thành "ư"), đổi font TCVN3 cũ sang Unicode.
2. convert.py in `WARNING needs OCR` (PDF chỉ có ảnh, mất dấu, chữ vỡ) thì server chạy `tools/ocr.py`:
   - **Bộ ghép (khi máy có PyTorch và VietOCR, `requirements-ocr.txt`, cần card NVIDIA):** Tesseract tìm dòng và từ, VietOCR (nhận chữ tiếng Việt) đọc lại, hai cách đọc ghép theo từng từ. Khoảng 4–7 giây mỗi trang.
   - **Tesseract một mình:** khi không có GPU. Nhanh, nhưng sai dấu nhiều ("Khám" thay cho "Khảm").
   - Dữ liệu ngôn ngữ (vie, eng, chi_tra, osd) nằm ở `tools/tessdata/`. Model VietOCR tải lần đầu (khoảng 150 MB) vào `data/models/vietocr/`.
   - Bản OCR mở đầu bằng dòng `<!-- bản chữ do OCR … -->`, app hiện thành ghi chú. Văn xuôi đọc tốt; thơ, bảng, hình vẽ, chữ Hán vẫn hay sai: trích nguyên văn thì đối chiếu ảnh trang (chế độ "Xem cùng bản gốc").

## 3. Ước tính thời gian (đo trên RTX 5060 Laptop, 11/2026)
- PDF có chữ: vài phần trăm giây mỗi trang.
- PDF quét, bộ ghép: 4–7 giây mỗi trang (sách 400 trang khoảng 30–45 phút). Tesseract chạy ở mức ưu tiên thấp để máy không đơ.

## 4. Khi nào báo người dùng
- **Không có Tesseract** (lỗi khi OCR): chạy lại `setup.bat`.
- **Bản OCR sai nhiều** (chữ Hán nhiều, bản quét mờ): chạy lại `ocr.py` với `--lang vie+chi_tra`, hoặc tìm bản PDF có chữ.
- **Word cũ `.doc`:** markitdown không đọc được, mở bằng Word rồi lưu thành `.docx`.
- Đừng trả lời từ bản chữ bị lỗi OCR. Nói ra là bản chữ lỗi.
