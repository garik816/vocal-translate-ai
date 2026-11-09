@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
if not exist "logs" mkdir "logs"

echo ============================================================
echo Vocal Translate AI v8.0 - GUIDE ONLY
echo Guide preview only. Seed-VC is skipped.
echo ============================================================
echo.

where pwsh >nul 2>&1
if %errorlevel%==0 (
    set "PS=pwsh"
) else (
    set "PS=powershell"
)

rem The environment is reused between runs; rebuilding OpenUtau each time is not needed.
if not exist "runtime\seed-vc\.venv\Scripts\python.exe" (
    %PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
    if errorlevel 1 goto :fail
)

set "PY=runtime\seed-vc\.venv\Scripts\python.exe"
if not exist "%PY%" goto :fail

"%PY%" "%~dp0app.py" --guide-only
if errorlevel 1 goto :fail

echo.
echo DONE. Listen to out\*_uk_guide.mp3
pause
exit /b 0

:fail
echo.
echo FAILED. Check logs\run.log. For ACE covers run ACE_ONLY.bat first, then FROM_ACE_GUIDE_ONLY.bat
pause
exit /b 1
