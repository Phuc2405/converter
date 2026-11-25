@echo off
rem Cai moi truong cho dich vu Sach (chay mot lan). Bam dup file nay.
cd /d "%~dp0"
py -3.13 -m venv .venv || python -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt

rem OCR cho PDF quet toan anh: Tesseract va du lieu tieng Viet, Anh, chu Han phon the (tools\tessdata, da co san trong thu muc).
if not exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
  echo Cai Tesseract OCR...
  winget install --id UB-Mannheim.TesseractOCR --exact --silent --accept-package-agreements --accept-source-agreements
)
if not exist "tools\tessdata" mkdir "tools\tessdata"
for %%L in (vie eng chi_tra osd) do (
  if not exist "tools\tessdata\%%L.traineddata" (
    echo Tai du lieu OCR %%L...
    curl -sSL -o "tools\tessdata\%%L.traineddata" "https://github.com/tesseract-ocr/tessdata_best/raw/main/%%L.traineddata"
  )
)

rem May co card NVIDIA: cai them bo doc chu tieng Viet (PyTorch + VietOCR, khoang 3 GB) de OCR dung dau hon nhieu.
where nvidia-smi >nul 2>nul
if %errorlevel%==0 (
  echo Thay card NVIDIA: cai bo OCR tieng Viet ^(PyTorch, VietOCR, khoang 3 GB^)...
  pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
  pip install --no-deps vietocr
  pip install -r requirements-ocr.txt
) else (
  echo Khong thay card NVIDIA: OCR chi dung Tesseract ^(nhanh, sai dau nhieu hon^).
)

echo.
echo Xong. Bam dup start.bat de mo dich vu Sach (http://localhost:6470).
pause
