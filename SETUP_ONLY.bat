@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
where pwsh >nul 2>&1
if %errorlevel%==0 (set "PS=pwsh") else (set "PS=powershell")
%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set "RC=%errorlevel%"
echo.
if "%RC%"=="0" (
    echo Setup verification completed successfully.
) else (
    echo Setup failed with exit code %RC%. See logs\setup.log.
)
pause
exit /b %RC%
