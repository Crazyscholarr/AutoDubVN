@echo off
cd /d %~dp0
chcp 65001 >nul
title AutoDubVN - Tai model ve may
if not exist venv\Scripts\activate.bat (
  echo [x] Chua cai dat. Chay install.bat truoc.
  pause & exit /b 1
)
call venv\Scripts\activate.bat
python tools\tai_model.py %*
pause
