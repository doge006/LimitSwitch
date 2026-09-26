@echo off
cd /d "%~dp0"
rem Prefer the python.org install; fall back to whatever windowless Python is on PATH.
set "PYW=%LocalAppData%\Programs\Python\Python313\pythonw.exe"
if exist "%PYW%" goto run
set "PYW=pythonw"
where pyw >nul 2>&1 && set "PYW=pyw"
:run
start "" "%PYW%" -m account_switcher.web --url-file "%~dp0.runtime\web.url" %*
