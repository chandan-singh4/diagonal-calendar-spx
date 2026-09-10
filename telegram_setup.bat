@echo off
:: ============================================================
:: telegram_setup.bat
:: SPX Diagonal Calendar Dashboard - Telegram bot setup check
::
:: Run manually:  double-click this file, or
::                telegram_setup.bat --test   (to send a test message)
::
:: WHY THIS FILE EXISTS. `python -m scripts.telegram_setup` only works
:: when the current directory is the project root, because that is what
:: puts `scripts` on the import path. Run from anywhere else it fails
:: with "No module named 'scripts'", which reads like a broken install
:: and is really a wrong working directory. (2026-09-08)
::
:: PATHS: resolved relative to THIS FILE (%~dp0), never hardcoded --
:: the same rule start_collector.bat follows, and for the same reason.
:: ============================================================

title SPX Telegram Setup

cd /d "%~dp0"

set "VENV_ACTIVATE="
if exist "%~dp0.venv\Scripts\activate.bat" set "VENV_ACTIVATE=%~dp0.venv\Scripts\activate.bat"
if not defined VENV_ACTIVATE if exist "%~dp0..\.venv\Scripts\activate.bat" set "VENV_ACTIVATE=%~dp0..\.venv\Scripts\activate.bat"

if not defined VENV_ACTIVATE (
    echo ERROR: No virtual environment found.
    echo Looked in:
    echo   %~dp0.venv\Scripts\activate.bat
    echo   %~dp0..\.venv\Scripts\activate.bat
    pause
    exit /b 1
)

call "%VENV_ACTIVATE%"
if errorlevel 1 (
    echo ERROR: Could not activate virtual environment at:
    echo   %VENV_ACTIVATE%
    pause
    exit /b 1
)

:: %* passes through any arguments, so --test works from the shortcut too.
python -m scripts.telegram_setup %*
set "EXITCODE=%ERRORLEVEL%"

echo.
if "%SPX_UNATTENDED%"=="1" exit /b %EXITCODE%
echo Press any key to close.
pause >nul
exit /b %EXITCODE%
