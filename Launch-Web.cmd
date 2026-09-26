@echo off
cd /d "%~dp0"
start "" "%LocalAppData%\Programs\Python\Python313\pythonw.exe" -m account_switcher.web %*
