@echo off
rem Pull the latest Account Switcher from GitHub. Options: -Branch NAME, -Watch, -Launch native^|web, -Simulator, -Force
rem Dev loop example:  Update.cmd -Watch -Launch native
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update.ps1" %*
