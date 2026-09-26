@echo off
cd /d "%~dp0"
rem Starts the tray app (opens the dashboard; add --quiet to start in the tray only).
rem Prefer the python.org install; fall back to whatever windowless Python is on PATH.
set "PYW=%LocalAppData%\Programs\Python\Python313\pythonw.exe"
if exist "%PYW%" goto run
set "PYW=pythonw"
where pyw >nul 2>&1 && set "PYW=pyw"
:run
start "" "%PYW%" -m account_switcher.tray --url-file "%~dp0.runtime\tray.url" %*
