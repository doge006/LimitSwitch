@echo off
cd /d "%~dp0"
rem Checks real account switching with your saved accounts. Quit Account Switcher first.
rem Usage: Verify-Switch.cmd            (Codex)
rem        Verify-Switch.cmd --provider claude
set "PY=%LocalAppData%\Programs\Python\Python313\python.exe"
if exist "%PY%" goto run
set "PY=python"
where py >/dev/null 2>&1 && set "PY=py"
:run
"%PY%" -m account_switcher.verify %*
echo.
pause
