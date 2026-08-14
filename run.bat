@echo off
cd /d %~dp0
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not exist venv\Scripts\activate.bat (
  echo [x] Chua cai dat. Chay install.bat truoc.
  pause & exit /b 1
)
call venv\Scripts\activate.bat
python main.py %*
pause
