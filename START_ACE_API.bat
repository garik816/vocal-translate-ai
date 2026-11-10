@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
where pwsh >nul 2>&1
if %errorlevel%==0 (set "PS=pwsh") else (set "PS=powershell")
%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_ace_api.ps1"
if errorlevel 1 goto :fail
exit /b 0
:fail
echo.
echo ACE API startup failed. See logs\ace_api.log.
pause
exit /b 1
