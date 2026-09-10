@echo off
:: ============================================================
:: start_briefings.bat
:: SPX Diagonal Calendar Dashboard - AI briefing daemon launcher
::
:: Run manually:  double-click this file, or
::                start_briefings.bat --show-schedule
::                start_briefings.bat --dry-run
::
:: WHAT IT RUNS. scripts/briefing_daemon.py, which sleeps until each
:: briefing time, reads the Gamma Exposure figures from the snapshot
:: taken at that moment, asks a model to explain them, logs the
:: forecast, and posts the result to Telegram if it is configured.
::
:: IT DECIDES ITS OWN TIMES. Like collector.py, it wakes on schedule
:: rather than being scheduled by Windows -- see the note in
:: register_collector_task.ps1 on why Task Scheduler was rejected on
:: this machine. Start it once and leave it; it sleeps through
:: weekends and holidays.
::
:: IT DOES NOT COLLECT. The collector must be running separately, or
:: there are no snapshots to read and every slot reports as much.
::
:: PATHS: resolved relative to THIS FILE (%~dp0), never hardcoded.
:: ============================================================

title SPX Briefing Daemon

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

echo.
echo [%DATE% %TIME%] SPX Briefing Daemon starting...
echo Project:   %~dp0
echo Venv:      %VENV_ACTIVATE%
echo Forecasts: data\briefing_forecasts.jsonl
echo Score:     python -m scripts.score_briefings
echo Stop:      Ctrl+C
echo.

python -m scripts.briefing_daemon %*
set "EXITCODE=%ERRORLEVEL%"

echo.
echo Briefing daemon stopped with exit code %EXITCODE%.

:: Only pause for a human -- see the same note in start_collector.bat.
if "%SPX_UNATTENDED%"=="1" exit /b %EXITCODE%
echo Press any key to close.
pause >nul
exit /b %EXITCODE%
