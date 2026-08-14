@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

rem ============================================================
rem  AutoDubVN - mo APP DESKTOP (cua so rieng, khong phai trinh duyet)
rem  Double-click file nay de chay.
rem ============================================================

set "PY=venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

rem Cai thu vien giao dien lan dau (neu thieu)
"%PY%" -c "import webview, yaml" 2>nul
if errorlevel 1 (
  echo [i] Lan dau chay - dang cai thu vien giao dien...
  "%PY%" -m pip install -r "gui\requirements-gui.txt"
)

"%PY%" "gui\app.py"
if errorlevel 1 (
  echo.
  echo [x] App thoat voi loi. Xem thong bao ben tren.
  pause
)
endlocal
