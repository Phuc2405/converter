@echo off
rem Mo dich vu Sach: server chay an, trinh duyet mo http://localhost:6470
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Chua cai dat. Bam dup setup.bat truoc.
  pause
  exit /b
)
start "" ".venv\Scripts\pythonw.exe" server.py --open
