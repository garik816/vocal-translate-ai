@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
if not exist "logs" mkdir "logs"
echo ============================================================
echo Vocal Translate AI v8 - FINAL VOICE CONVERSION
echo ============================================================
echo.
where pwsh >nul 2>&1
if %errorlevel%==0 (set "PS=pwsh") else (set "PS=powershell")
%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 goto :fail
set "PY=runtime\seed-vc\.venv\Scripts\python.exe"
if not exist "%PY%" goto :fail
"%PY%" "%~dp0app.py" --from-ace
if errorlevel 1 goto :fail
echo.
echo DONE. Result: out\*_uk.mp3
pause
exit /b 0
:fail
echo.
echo FAILED. Check logs\run.log and stage logs.
pause
exit /b 1
