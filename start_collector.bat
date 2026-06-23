@echo off
:: ============================================================
:: start_collector.bat
:: SPX Diagonal Calendar Dashboard — Collector Launcher
::
:: This script activates the virtual environment and starts
:: the background data collector.
::
:: Run manually:  double-click this file, or
::                python collector.py  (from VS Code terminal)
::
:: Scheduled:     Task Scheduler calls this at every logon.
::                The collector sleeps outside market hours and
::                wakes automatically at 9:30 AM ET — no manual
::                intervention needed.
:: ============================================================

title SPX Diagonal Collector

:: Change to the project directory
cd /d "C:\Users\chand\Python\spx-diagonal-dashboard"

:: Activate the virtual environment (located in parent folder)
call "..\.venv\Scripts\activate.bat"

if errorlevel 1 (
    echo ERROR: Could not activate virtual environment.
    echo Expected path: C:\Users\chand\Python\.venv
    echo Press any key to exit.
    pause >nul
    exit /b 1
)

echo.
echo [%DATE% %TIME%] SPX Diagonal Collector starting...
echo Logs: collector.log (warnings and errors)
echo Stop:  Ctrl+C
echo.

:: Run the collector — loops indefinitely until Ctrl+C or system shutdown
python collector.py

:: If collector exits for any reason, pause so the error is visible
echo.
echo Collector stopped. Press any key to close.
pause >nul
