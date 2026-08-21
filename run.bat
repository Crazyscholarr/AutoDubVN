@echo off
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
echo [%time:~0,8%] [BOOT] Project: %CD%
echo [%time:~0,8%] [BOOT] Python: venv\Scripts\python.exe
if not exist venv\Scripts\activate.bat (
  echo [x] Chua cai dat. Chay install.bat truoc.
  pause & exit /b 1
)
call venv\Scripts\activate.bat
python main.py %*
pause
