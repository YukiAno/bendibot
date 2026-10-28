@echo off
cd /d "%~dp0"
title bendibot stop
"%~dp0venv\Scripts\python.exe" "%~dp0bot_manager.py" stop
pause