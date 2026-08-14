@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0\.."

rem ============================================================
rem  Dong goi app desktop AutoDubVN thanh .exe (PyInstaller).
rem  Chay trong thu muc goc AutoDubVN. Ket qua: dist\AutoDubVN\AutoDubVN.exe
rem ============================================================

set "PY=venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo [i] Cai PyInstaller + thu vien giao dien...
  "%PY%" -m pip install -r "gui\requirements-gui.txt"
)

echo [i] Dang build...
"%PY%" -m PyInstaller gui\autodubvn_gui.spec --noconfirm --clean

echo.
echo [i] Xong. Chay: dist\AutoDubVN\AutoDubVN.exe
echo     (De .exe canh main.py, hoac chep ca thu muc dist\AutoDubVN vao project.)
pause
endlocal
