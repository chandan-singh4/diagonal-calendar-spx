# ============================================================
# register_watchdog_task.ps1  (M3.4)
#
# Registers scripts/watchdog.py to run every 10 minutes, so that a stopped
# collector is noticed by something other than Chandan happening to look at
# the dashboard. On 2026-08-09 the Schwab token had expired and the dashboard's
# red TOKEN EXPIRED banner was working perfectly -- nobody was looking at it.
#
# ADMINISTRATOR IS NOT REQUIRED, and that is worth stating because
# register_collector_task.ps1 records the opposite experience. The difference
# is the trigger: ONLOGON triggers require elevation by design, which is what
# returned "Access is denied" on 2026-06-22 and again 2026-07-26. A repeating
# TIME trigger, which is what this uses, does not.
#
# HOW TO RUN (once):
#     powershell -ExecutionPolicy Bypass -File .\scripts\register_watchdog_task.ps1
#
# VERIFY:
#     Get-ScheduledTask -TaskName "SPX Collector Watchdog" |
#         Select-Object TaskName, State
#
# RUN IT NOW, without waiting for the next slot:
#     Start-ScheduledTask -TaskName "SPX Collector Watchdog"
#
# SEE WHAT IT LAST DID:
#     Get-ScheduledTaskInfo -TaskName "SPX Collector Watchdog"
#   LastTaskResult is the script's exit code: 0 all well (or market shut),
#   1 a problem was found AND REPORTED, 2 the check itself could not run.
#   A 1 is the watchdog working, not the watchdog failing.
#
# REMOVE COMPLETELY:
#     Unregister-ScheduledTask -TaskName "SPX Collector Watchdog" -Confirm:$false
#
# ------------------------------------------------------------
# WHY EVERY 10 MINUTES, ALL DAY, EVERY DAY -- including nights and weekends.
#
# The script already knows when the market is shut and stays silent; making
# the SCHEDULE market-aware as well would put a second copy of the market
# calendar somewhere that nobody updates in January. Better a dumb schedule
# and a smart script. Running overnight also means a collector that died at
# 02:00 is reported at 09:35 rather than discovered at lunchtime.
#
# 10 rather than 5: the midday alarm threshold is 12.5 minutes of silence, so
# a 10-minute check catches an outage inside ~20 minutes. Halving that is not
# worth doubling the wakeups.
#
# It does NOT restart the collector or re-authenticate. It observes and it
# tells you. A watchdog that takes action unattended is a second thing that
# can go wrong while nobody is watching.
# ============================================================

$ErrorActionPreference = "Stop"

$TaskName = "SPX Collector Watchdog"

# $PSScriptRoot is scripts/; the project is its parent. Derived rather than
# hardcoded so moving or renaming the project does not silently break the task.
# (Note for whoever reads register_collector_task.ps1 next: that script sets
# $ProjectDir = $PSScriptRoot and then looks for start_collector.bat beside
# itself. It now lives in scripts/ and the batch file is at the project root,
# so it would fail Test-Path. Harmless today -- that script is documented as
# not being the active mechanism -- but it is wrong and worth fixing when
# someone next needs it.)
$ProjectDir = Split-Path -Parent $PSScriptRoot
$Watchdog   = Join-Path $ProjectDir "scripts\watchdog.py"

# The venv interpreter, not whatever `python` happens to mean under Task
# Scheduler -- which starts with a different PATH and would otherwise find a
# system Python with none of the dependencies installed.
$PythonExe = Join-Path (Split-Path -Parent $ProjectDir) ".venv\Scripts\pythonw.exe"

Write-Host "Project : $ProjectDir"
Write-Host "Script  : $Watchdog"
Write-Host "Python  : $PythonExe"
Write-Host ""

if (-not (Test-Path $Watchdog))  { Write-Error "watchdog.py not found at: $Watchdog"; exit 1 }
if (-not (Test-Path $PythonExe)) { Write-Error "Python not found at: $PythonExe";     exit 1 }

# ── Action ───────────────────────────────────────────────────────────────────
# pythonw.exe, not python.exe: a console window flashing up every 10 minutes
# all day would be its own small torment, and would train you to dismiss
# anything the watchdog puts on screen.
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$Watchdog`"" `
    -WorkingDirectory $ProjectDir

# ── Trigger: every 10 minutes, forever ───────────────────────────────────────
# A duration is REQUIRED. Without one the repetition silently stops after a
# day and the watchdog goes quiet on day two -- which looks exactly like
# everything being fine, and is the failure this whole script exists to
# prevent.
#
# The documented idiom for "forever", [TimeSpan]::MaxValue, is rejected by
# this Windows 11 build: it serialises to P99999999DT23H59M59S and the task
# XML validator calls it out of range. 10 years is the practical stand-in.
# Note the expiry date in the comment below so it is a known horizon rather
# than a surprise: this repetition stops in August 2036.
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 10) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# ── Settings ─────────────────────────────────────────────────────────────────
# ExecutionTimeLimit 10 minutes: the check takes under a second, so anything
# approaching that is hung, and a hung instance must not block the next one.
$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd

# ── Principal: current user, no elevation ────────────────────────────────────
# As the logged-in user, so .env and data/token.json are readable with no
# permission gymnastics, and so desktop notifications have a desktop to appear
# on. A watchdog running as SYSTEM could check perfectly and have nowhere to
# put the pop-up.
$Principal = New-ScheduledTaskPrincipal `
    -UserId    "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel  Limited

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Task '$TaskName' already exists - replacing it." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName    $TaskName `
    -Action      $Action `
    -Trigger     $Trigger `
    -Settings    $Settings `
    -Principal   $Principal `
    -Description "Every 10 minutes, checks that the SPX collector is still recording prices during market hours, and raises a desktop notification and an email if it is not. Silent when all is well and when the market is shut. Never writes to the database and never restarts anything. Registered by scripts/register_watchdog_task.ps1 (M3.4)." | Out-Null

Write-Host ""
Write-Host "Task registered." -ForegroundColor Green
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
Write-Host ""
Write-Host "It is already running on its 10-minute cycle."
Write-Host "Prove it fires now:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Remove it:           Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
