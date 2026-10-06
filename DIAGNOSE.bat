@echo off
setlocal
cd /d "%~dp0"
where pwsh >nul 2>&1
if %errorlevel%==0 (set "PS=pwsh") else (set "PS=powershell")
%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0diagnose.ps1"
echo.
echo Diagnostic log: logs\diagnose.log
pause
