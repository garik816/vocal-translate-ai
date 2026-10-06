@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
if not exist "logs" mkdir "logs"

echo ============================================================
echo Vocal Translate AI v4
echo input MP3 + translated lyrics -^> final MP3 in out
echo ============================================================
echo.
echo [%date% %time%] RUN started>>"logs\launcher.log"

where pwsh >nul 2>&1
if %errorlevel%==0 (
    set "PS=pwsh"
) else (
    set "PS=powershell"
)

%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set "RC=%errorlevel%"
if not "%RC%"=="0" goto :fail

set "PY=runtime\ACE-Step-1.5\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo ERROR: ACE-Step Python not found: %PY%
    set "RC=20"
    goto :fail
)

"%PY%" "%~dp0app.py"
set "RC=%errorlevel%"
if not "%RC%"=="0" goto :fail

echo.
echo ============================================================
echo DONE. Final MP3 is in the out folder.
echo ============================================================
echo [%date% %time%] RUN completed OK>>"logs\launcher.log"
pause
exit /b 0

:fail
echo.
echo ============================================================
echo FAILED. Exit code: %RC%
echo Check these files:
echo   logs\launcher.log
echo   logs\setup.log
echo   logs\run.log
echo   logs\demucs.log
echo   logs\ace_api.log
echo   logs\seed_vc.log
echo   logs\ffmpeg.log
echo ============================================================
echo [%date% %time%] RUN failed, code %RC%>>"logs\launcher.log"
pause
exit /b %RC%
