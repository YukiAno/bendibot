@echo off
cd /d "%~dp0"
title bendibot

REM ---- clean up any leftover process holding port 8080 ----
"%~dp0venv\Scripts\python.exe" "%~dp0bot_manager.py" clean

echo.
echo Starting bendibot...
echo.

"%~dp0venv\Scripts\python.exe" "%~dp0bot.py"

REM ---- clean up again in case the process lingered after exit ----
"%~dp0venv\Scripts\python.exe" "%~dp0bot_manager.py" clean

echo.
echo [bendibot has exited]
pause