@echo off
REM ---- encoding: console AND python must both be UTF-8 ----
REM chcp alone is NOT enough: it switches the console to UTF-8 while
REM python keeps writing GBK, and the chinese text comes out garbled.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title bendibot - send guard

:menu
cls
echo.
"%~dp0venv\Scripts\python.exe" "%~dp0toggle_silent.py"
echo.
echo   ------------------------------------------------------------
echo    [1] Turn ON  silent mode
echo        Bot stays online but sends NOTHING at all.
echo        Use this while the QQ account is resting after
echo        a risk-control warning.
echo.
echo    [2] Turn OFF silent mode
echo        Normal sending. The rate guard is still active.
echo.
echo    [0] Exit
echo   ------------------------------------------------------------
echo.
set /p choice=Type 1 / 2 / 0 then press Enter: 

if "%choice%"=="1" goto silent_on
if "%choice%"=="2" goto silent_off
goto end

:silent_on
echo.
"%~dp0venv\Scripts\python.exe" "%~dp0toggle_silent.py" on
echo.
echo   Restart the bot for this to take effect.
echo.
pause
goto menu

:silent_off
echo.
"%~dp0venv\Scripts\python.exe" "%~dp0toggle_silent.py" off
echo.
echo   Restart the bot for this to take effect.
echo.
pause
goto menu

:end