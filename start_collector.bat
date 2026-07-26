@echo off
:: ============================================================
:: start_collector.bat
:: SPX Diagonal Calendar Dashboard - Collector Launcher
::
:: Run manually:  double-click this file, or
::                python collector.py  (from a VS Code terminal)
::
:: Scheduled:     register via register_collector_task.ps1 so the
::                collector starts at logon. It sleeps outside
::                market hours and wakes at 9:30 AM ET on its own.
::
:: PATHS: resolved relative to THIS FILE (%~dp0), never hardcoded.
:: Task Scheduler launches with a working directory of
:: C:\Windows\System32, and the previous absolute-path version
:: would break if the project were ever moved. (M0.13, 2026-07-25)
:: ============================================================

title SPX Diagonal Collector

:: Change to the directory containing this script
cd /d "%~dp0"

:: Locate a virtual environment. Prefer one inside the project; fall back to
:: the shared parent venv this project historically used.
set "VENV_ACTIVATE="
if exist "%~dp0.venv\Scripts\activate.bat" set "VENV_ACTIVATE=%~dp0.venv\Scripts\activate.bat"
if not defined VENV_ACTIVATE if exist "%~dp0..\.venv\Scripts\activate.bat" set "VENV_ACTIVATE=%~dp0..\.venv\Scripts\activate.bat"

if not defined VENV_ACTIVATE (
    echo ERROR: No virtual environment found.
    echo Looked in:
    echo   %~dp0.venv\Scripts\activate.bat
    echo   %~dp0..\.venv\Scripts\activate.bat
    echo.
    echo Create one with:  python -m venv .venv
    echo Then:             .venv\Scripts\activate ^&^& pip install -r requirements.lock
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

echo.
echo [%DATE% %TIME%] SPX Diagonal Collector starting...
echo Project: %~dp0
echo Venv:    %VENV_ACTIVATE%
echo Logs:    collector.log (warnings and errors; rotates at 1 MB x 5)
echo Stop:    Ctrl+C
echo.

python collector.py
set "EXITCODE=%ERRORLEVEL%"

echo.
echo Collector stopped with exit code %EXITCODE%.

:: Only pause for a human. Under Task Scheduler there is no console to read,
:: and pausing would leave a zombie process holding the task "running" forever.
if "%SPX_UNATTENDED%"=="1" exit /b %EXITCODE%
echo Press any key to close.
pause >nul
exit /b %EXITCODE%
