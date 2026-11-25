---
name: add-video
description: Thêm video vào dịch vụ Video, chép lời thành bản chữ có mốc giờ, và tạo giờ của từng chữ để lời chạy theo video (tô chữ đang đọc, bấm chữ là tua). Dùng khi được nhờ "thêm video", "chép lời", "lời không chạy theo video", "video không có tiếng", hoặc một video trong data/goc chưa có lời.
---

# Thêm video, chép lời, chữ chạy theo video

Dịch vụ Video (`server.py`, http://localhost:6471). Không có database: thư mục chính là kho.
- Video gốc: `data/goc/<tên>.mp4` (mkv, webm, mov, mp3, m4a… cũng được).
- Lời: `data/md/<cùng tên>.md`. App ghép video với lời theo **tên giống hệt**, đừng đổi tên một bên.
- Giờ của từng chữ: `data/word-times/<cùng tên>.json` (`{"paras": {"<giây đầu đoạn>": [giờ chữ 1, chữ 2, …]}}`).
- Video tải về **chỉ có hình** (YouTube 1080p trở lên hay tách hình và tiếng): để phần tiếng ở `data/goc/_tiếng/<cùng tên>.m4a`. Server chép lời từ file đó, app phát tiếng theo video (phát, dừng, tua, tốc độ, âm lượng).

## 1. Trong app (cách chính)
- "＋ Thêm video" hoặc thả file vào cửa sổ. Server lưu vào `data/goc/`, rồi chép lời ở nền, **lần lượt từng video**. Danh sách bên trái hiện phần trăm.
- Mở một video: hình bên trái, lời bên phải. Chữ đang đọc tô son, gạch chân, lời tự cuộn theo (tự lăn chuột thì 6 giây không cuộn theo). Bấm mốc giờ hoặc bấm một chữ: video chạy từ đó. Đang bôi đen chữ thì không tua.
- Nút ✕ ở mỗi dòng: xóa video. Video, lời, giờ từng chữ, phần tiếng chuyển vào `data/_da-xoa/<thời điểm>/`; xóa hẳn thì xóa thư mục đó bằng tay.
- Tắt server giữa chừng: lần mở sau, video chưa có lời tự xếp lại vào hàng chờ.

## 2. Dòng lệnh (trong thư mục `video/`)
- Chép lời: `.venv\Scripts\python tools\transcribe.py "data/goc/<tên>.mp4" -o data/md` (thêm `--device cpu` để không dùng GPU, `--lang vi` nếu biết trước). Script ghi luôn giờ từng chữ.
- Lời có sẵn (ví dụ lấy từ phụ đề YouTube: `tools\transcribe.py <link> -o data/md`), cần thêm giờ từng chữ: khi đã có video hoặc phần tiếng trong `data/goc/`, chạy `.venv\Scripts\python tools\word_times.py "data/md/<tên>.md"` (hoặc `--all`, `--force` để làm lại). Script **không sửa lời**: nghe lại video, khớp từng chữ (difflib), chữ không khớp thì lấy giờ ở giữa hai chữ khớp gần nhất; in ra tỉ lệ khớp (lời do chính whisper chép thường khớp trên 90%).
- Kiểm tra lời: có dòng `- **Nguồn**`, `- **Cách lấy**`, mỗi đoạn mở đầu bằng `**[mm:ss]**`. Lời lặp lại nhiều lần là lỗi đọc phụ đề.

## 3. Chép lời chạy thế nào
- `tools/transcribe.py` dùng faster-whisper, tự nhận ngôn ngữ trong 30 giây đầu.
- Có card NVIDIA và đã cài `requirements-gpu.txt` (setup.bat tự cài khi thấy `nvidia-smi`): model large-v3-turbo trên GPU. Không có: model small trên CPU. Đặt biến môi trường `VIDEO_GPU=cpu` trước khi chạy server để không dùng GPU.
- Lần đầu tải model (khoảng 1,6 GB trên GPU, 500 MB trên CPU) vào thư mục cache của Hugging Face.
- Ước tính (RTX 5060 Laptop): GPU khoảng 0,05–0,1 lần thời lượng video, cộng 25 giây nạp model; CPU khoảng 0,35–0,4 lần thời lượng.

## 4. Khi nào báo người dùng
- **Video không có tiếng** (lời trống, hoặc ffprobe không thấy luồng tiếng: `ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "<video>"`): tải bản có tiếng, hoặc tải riêng phần tiếng vào `data/goc/_tiếng/` (`python -m yt_dlp -f 140 -o "data/goc/_tiếng/<tên>.%(ext)s" <link>`). Không chắc video nào khớp với link nào thì hỏi, đừng đoán.
- **Lỗi thiếu thư viện GPU** (`cublas64_12.dll`): chạy lại `setup.bat`, hoặc đặt `VIDEO_GPU=cpu`.
- **Chữ tô lệch nhiều:** video chưa có file giờ từng chữ (app đang ước theo độ dài chữ): chạy `word_times.py` cho video đó.
- Trích lời video thì ghi mốc giờ, ví dụ `(Bài 3, 12:34)`; lời lấy từ phụ đề tự động thì nói rõ có thể sai chữ.
