@echo off
rem One-shot update from GitHub. Options: -Branch NAME, -Force
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update.ps1" %*
set "RC=%ERRORLEVEL%"
rem Keep the window open when started by double-click.
echo %cmdcmdline% | find /i "%~0" >nul && pause
exit /b %RC%
