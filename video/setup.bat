@echo off
rem Cai moi truong cho dich vu Video (chay mot lan). Bam dup file nay.
cd /d "%~dp0"
py -3.13 -m venv .venv || python -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt

rem May co card NVIDIA: cai them thu vien CUDA de chep loi bang GPU (nhanh hon nhieu, khoang 1 GB). Khong co thi chep loi bang CPU.
where nvidia-smi >nul 2>nul
if %errorlevel%==0 (
  echo Thay card NVIDIA: cai thu vien GPU cho chep loi...
  pip install -r requirements-gpu.txt
) else (
  echo Khong thay card NVIDIA: chep loi se chay bang CPU ^(cham hon^).
)

where ffmpeg >nul 2>nul
if not %errorlevel%==0 echo Chua co ffmpeg: chi can khi lay loi tu link YouTube bang dong lenh. Cai bang: winget install Gyan.FFmpeg

echo.
echo Xong. Lan chep loi dau tien se tai model nhan giong noi (khoang 1.6 GB tren GPU, 500 MB tren CPU).
echo Bam dup start.bat de mo dich vu Video (http://localhost:6471).
pause
