@echo off
rem Install or update Account Switcher (Windows). Options: --branch NAME, --force, --no-launch
cd /d "%~dp0"
set "PY="
for %%P in (python.exe py.exe) do if not defined PY (for /f "delims=" %%F in ('where %%P 2^>nul ^| findstr /v /i WindowsApps') do if not defined PY set "PY=%%F")
if not defined PY if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PY=%LocalAppData%\Programs\Python\Python313\python.exe"
if not defined PY (
  echo Python is not installed. Installing it with winget...
  winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
  set "PY=%LocalAppData%\Programs\Python\Python313\python.exe"
)
where git >nul 2>&1 || (
  echo git is not installed. Installing it with winget...
  winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
  set "PATH=%PATH%;%ProgramFiles%\Git\cmd"
)
"%PY%" "%~dp0scripts\installer.py" %*
set "RC=%ERRORLEVEL%"
rem Keep the window open when started by double-click.
echo %cmdcmdline% | find /i "%~0" >nul && pause
exit /b %RC%
