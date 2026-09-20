@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
echo ============================================
echo   DANG NHAP GEMINI PRO CHO AUTODUBVN
echo ============================================
echo.
echo  Mo Chrome hoac Edge voi ho so rieng de dang nhap Google.
echo  Dang nhap xong, DONG HET cua so do roi moi chay AutoDubVN.
echo.

set "PROFILE=%~dp0browser_profile"
if not exist "%PROFILE%" mkdir "%PROFILE%"

set "BROWSER="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "BROWSER=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not defined BROWSER if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not defined BROWSER if exist "%LocalAppData%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%LocalAppData%\Microsoft\Edge\Application\msedge.exe"

if not defined BROWSER (
  echo [LOI] Khong thay Google Chrome hoac Microsoft Edge.
  pause
  exit /b 1
)

echo Dang mo: %BROWSER%
start "" "%BROWSER%" --user-data-dir="%PROFILE%" --start-maximized --new-window "https://gemini.google.com/app"
echo.
echo Da mo Gemini. Dang nhap xong thi dong het cua so nay.
pause
endlocal
