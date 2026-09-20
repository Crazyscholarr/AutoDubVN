@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
echo ============================================
echo   DANG NHAP CHATGPT CHO AUTODUBVN
echo ============================================
echo.
echo  May nay se mo Chrome hoac Edge voi ho so rieng
echo  (khong dung chung voi Gemini).
echo.

set "PROFILE=%~dp0browser_profile_chatgpt"
if not exist "%PROFILE%" mkdir "%PROFILE%"

REM May khong cai Edge thi van mo duoc bang Chrome.
set "BROWSER="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "BROWSER=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not defined BROWSER if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not defined BROWSER if exist "%LocalAppData%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%LocalAppData%\Microsoft\Edge\Application\msedge.exe"

if not defined BROWSER (
  echo [LOI] Khong thay Google Chrome hoac Microsoft Edge.
  echo Hay cai Chrome roi chay lai file nay.
  echo.
  pause
  exit /b 1
)

echo Dang mo: %BROWSER%
echo Ho so:   %PROFILE%
echo.

REM start "" bat buoc: neu thieu title, Windows hieu nham duong dan la tieu de cua so.
start "" "%BROWSER%" --user-data-dir="%PROFILE%" --start-maximized --new-window "https://chatgpt.com/"
if errorlevel 1 (
  echo [LOI] Khong mo duoc trinh duyet. Thu cach Python...
  if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" -c "from autodub.chatgpt_web import launch_chatgpt_login; r=launch_chatgpt_login(); print(r); raise SystemExit(0 if r.get('ok') else 1)"
    if errorlevel 1 (
      echo Van khong mo duoc. Hay mo Chrome thu cong toi https://chatgpt.com/
      pause
      exit /b 1
    )
  ) else (
    pause
    exit /b 1
  )
)

echo Da mo trinh duyet ChatGPT.
echo Dang nhap tai khoan xong thi DONG HET cua so do
echo roi moi quay lai AutoDubVN.
echo.
pause
endlocal
