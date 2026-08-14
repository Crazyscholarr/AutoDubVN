@echo off
setlocal
cd /d %~dp0
chcp 65001 >nul
echo ============================================
echo   AutoDubVN - Cai dat (Windows + RTX 3060)
echo ============================================
echo.
echo  Luu y: dung "python -m pip" (khong dung pip.exe) de tranh bi
echo  Device Guard/WDAC cua to chuc chan.
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [x] Chua co Python. Cai Python 3.10/3.11 tu python.org roi chay lai.
  pause & exit /b 1
)

echo [1/5] Tao moi truong ao (venv)...
if not exist venv\Scripts\activate.bat python -m venv venv
call venv\Scripts\activate.bat
python -m pip install --upgrade pip wheel

echo.
echo [2/5] Cai nhom goi NHE (chuong trinh chay duoc ngay ca khi buoc GPU loi)...
python -m pip install pyyaml edge-tts requests yt-dlp playwright
if errorlevel 1 (
  echo [x] Cai goi nhe that bai - kiem tra mang roi chay lai.
  pause & exit /b 1
)

echo.
echo [3/5] Cai PyTorch ban CUDA 12.1 (chay GPU RTX 3060, ~2.5GB)...
python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
if errorlevel 1 echo [!] Torch cai loi - co the cai lai sau. Cac phan khac van chay.

echo.
echo [4/5] Cai backend nhan phu de (FunASR Paraformer + faster-whisper)...
python -m pip install "funasr!=1.3.9" modelscope faster-whisper
if errorlevel 1 echo [!] Cai backend ASR loi - thu lai: python -m pip install funasr modelscope faster-whisper

echo.
echo [5/5] Kiem tra ffmpeg...
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [!] CHUA co ffmpeg trong PATH. Tai tai https://www.gyan.dev/ffmpeg/builds/
  echo     giai nen roi them thu muc bin vao Bien moi truong PATH.
) else (
  echo [ok] Da co ffmpeg.
)

echo.
echo [6/6] Cai thu vien APP DESKTOP (cua so rieng)...
python -m pip install -r gui\requirements-gui.txt
if errorlevel 1 echo [!] Cai giao dien loi - co the cai lai sau bang: python -m pip install pywebview pythonnet

echo.
echo ============================================
echo  XONG. Dich mac dinh dung tai khoan Gemini Pro qua Edge (khong can API key).
echo.
echo   Chay APP DESKTOP :  run_gui.bat     ^<-- khuyen dung
echo   Chay dong lenh   :  run.bat
echo ============================================
pause
