@echo off
cd /d "%~dp0"
rem Starts the tray app (opens the dashboard; add --quiet to start in the tray only).
rem Prefers the installer's private environment (.venv), then a python.org install.
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if exist "%PYW%" goto run
set "PYW=%LocalAppData%\Programs\Python\Python313\pythonw.exe"
if exist "%PYW%" goto run
set "PYW=pythonw"
where pyw >nul 2>&1 && set "PYW=pyw"
:run
start "" "%PYW%" -m account_switcher.tray --url-file "%~dp0.runtime\tray.url" %*
