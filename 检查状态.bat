@echo off
cd /d "%~dp0"
title bendibot status
"%~dp0venv\Scripts\python.exe" "%~dp0check_status.py"