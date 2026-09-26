@echo off
rem Adds Account Switcher to the Start menu with its icon. Options: -Desktop (also on the desktop), -Remove
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\shortcut.ps1" %*
set "RC=%ERRORLEVEL%"
rem Keep the window open when started by double-click.
echo %cmdcmdline% | find /i "%~0" >nul && pause
exit /b %RC%
