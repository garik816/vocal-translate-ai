@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
if not exist "logs" mkdir "logs"

echo ============================================================
echo Vocal Translate AI v7 - SOURCE LYRICS EXTRACTOR
echo MP3 -^> Demucs vocals -^> ID3/Whisper -^> TXT + SRT + JSON
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

"%PY%" "%~dp0app.py" --extract-only
if errorlevel 1 goto :fail

echo.
echo DONE.
echo Source lyrics are in out:
echo   *_source_lyrics.txt
echo   *_source_lyrics.srt
echo   *_source_lyrics.json
pause
exit /b 0

:fail
echo.
echo FAILED. Check logs\setup.log, logs\run.log, logs\demucs.log and logs\asr.log.
pause
exit /b 1
