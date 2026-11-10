@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
if not exist "logs" mkdir "logs"

echo ============================================================
echo Vocal Translate AI v8.0 experimental
echo Automatic guide selection -^> Seed-VC -^> original instrumental
echo ============================================================
echo.
echo [%date% %time%] RUN started>>"logs\launcher.log"

where pwsh >nul 2>&1
if %errorlevel%==0 (
    set "PS=pwsh"
) else (
    set "PS=powershell"
)

rem The environment is reused between runs; rebuilding OpenUtau each time is not needed.
if not exist "runtime\seed-vc\.venv\Scripts\python.exe" (
    %PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
    if errorlevel 1 goto :setup_fail
)

set "PY=runtime\seed-vc\.venv\Scripts\python.exe"
if not exist "%PY%" goto :python_fail

"%PY%" "%~dp0app.py" --auto-guide
if errorlevel 1 goto :pipeline_fail

echo.
echo ============================================================
echo DONE. Results are in the out folder.
echo ============================================================
echo [%date% %time%] RUN completed OK>>"logs\launcher.log"
pause
exit /b 0

:setup_fail
echo.
echo ============================================================
echo FAILED during SETUP.
echo Check logs\setup.log
echo ============================================================
echo [%date% %time%] RUN failed during SETUP>>"logs\launcher.log"
pause
exit /b 1

:python_fail
echo.
echo ============================================================
echo FAILED: Python environment was not created.
echo Expected: %PY%
echo Check logs\setup.log
echo ============================================================
echo [%date% %time%] RUN failed: Seed Python missing>>"logs\launcher.log"
pause
exit /b 20

:pipeline_fail
echo.
echo ============================================================
echo FAILED during PIPELINE.
echo Check:
echo   logs\run.log
echo   logs\demucs.log
echo   logs\asr.log
echo   logs\melody.log
echo   logs\ffmpeg.log
echo   logs\seed_vc.log
echo   logs\ffmpeg.log
echo ============================================================
echo [%date% %time%] RUN failed during PIPELINE>>"logs\launcher.log"
pause
exit /b 1
