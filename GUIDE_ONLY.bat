@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
if not exist "logs" mkdir "logs"

echo ============================================================
echo Vocal Translate AI v7.5.0 - GUIDE ONLY
echo DiffSinger preview only. Seed-VC is skipped.
echo ============================================================
echo.

where pwsh >nul 2>&1
if %errorlevel%==0 (
    set "PS=pwsh"
) else (
    set "PS=powershell"
)

%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 goto :fail

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
echo FAILED. Check logs\run.log and logs\diffsinger.log
pause
exit /b 1
