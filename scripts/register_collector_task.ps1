# ============================================================
# register_collector_task.ps1
#
# *** NOT THE ACTIVE AUTO-START MECHANISM. OPTIONAL. ***
#
# Auto-start on this machine is handled by a STARTUP FOLDER SHORTCUT,
# created 2026-06-22 and working continuously since:
#
#     shell:startup  ->  "SPX Diagonal Collector.lnk"
#     Target : <venv>\Scripts\python.exe
#     Args   : "<project>\collector.py"
#     Isn    : <project>
#
# Task Scheduler was deliberately rejected (see DEV_JOURNAL 2026-06-22):
# start_collector.bat was blocked by Windows Smart App Control, and this
# script cannot register without Administrator rights. Re-confirmed
# 2026-07-26: both Register-ScheduledTask and `schtasks /sc onlogon` return
# "Access is denied" for a non-elevated shell, because ONLOGON triggers
# require elevation by design.
#
# Keep this script only for a future machine where an elevated shell is
# available and Task Scheduler's extras are wanted (notably automatic
# restart-on-crash, which the Startup shortcut does not provide -- see
# backlog OPS-001b). To use it, run from an ADMINISTRATOR PowerShell.
#
# ------------------------------------------------------------
# One-time setup: registers the SPX collector as a Windows
# Scheduled Task that starts automatically at every logon.
#
# HOW TO RUN (once). Administrator is NOT required:
#     cd "<project folder>"
#     .\register_collector_task.ps1
#
# If PowerShell blocks the script:
#     powershell -ExecutionPolicy Bypass -File .\register_collector_task.ps1
#
# VERIFY:
#     Get-ScheduledTask -TaskName "SPX Diagonal Collector" |
#         Select-Object TaskName, State
#
# START NOW (without logging out):
#     Start-ScheduledTask -TaskName "SPX Diagonal Collector"
#
# STOP:
#     Stop-ScheduledTask -TaskName "SPX Diagonal Collector"
#
# REMOVE COMPLETELY:
#     Unregister-ScheduledTask -TaskName "SPX Diagonal Collector" -Confirm:$false
#
# ------------------------------------------------------------
# Revised 2026-07-26 (M0.13). Three fixes:
#   1. $ProjectDir is derived from $PSScriptRoot instead of being hardcoded,
#      so moving or renaming the project no longer silently breaks the task.
#   2. RunLevel changed Highest -> Limited. A per-user logon task does not
#      need elevation, and requiring admin was a barrier to it ever being run.
#   3. Sets SPX_UNATTENDED=1. start_collector.bat ends with `pause` so a human
#      can read errors; under Task Scheduler there is no console to read and
#      the pause would leave a zombie process holding the task "running"
#      forever. The batch file skips the pause when this variable is set.
# ============================================================

$ErrorActionPreference = "Stop"

$TaskName   = "SPX Diagonal Collector"
$ProjectDir = $PSScriptRoot
$BatchFile  = Join-Path $ProjectDir "start_collector.bat"

Write-Host "Project : $ProjectDir"
Write-Host "Launcher: $BatchFile"
Write-Host ""

if (-not (Test-Path $BatchFile)) {
    Write-Error "start_collector.bat not found at: $BatchFile"
    exit 1
}

# ── Action ───────────────────────────────────────────────────────────────────
# `set SPX_UNATTENDED=1 && ...` runs inside the same cmd instance, so the
# variable is visible to the batch file. Quoting matters: the outer /c argument
# is one string, and the path is quoted inside it to survive spaces.
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c set SPX_UNATTENDED=1 && `"$BatchFile`"" `
    -WorkingDirectory $ProjectDir

# ── Trigger: every user logon ────────────────────────────────────────────────
# The collector handles market hours itself. It sleeps until 09:30 ET
# regardless of when the machine boots, and sleeps through weekends/holidays.
$Trigger = New-ScheduledTaskTrigger -AtLogon

# ── Settings ─────────────────────────────────────────────────────────────────
$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -RunOnlyIfNetworkAvailable `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

# ── Principal: current user, no elevation ────────────────────────────────────
# Running as the current user keeps .env and data/token.json accessible with
# no path or permission gymnastics.
$Principal = New-ScheduledTaskPrincipal `
    -UserId    "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel  Limited

# ── Register (replacing any existing registration) ───────────────────────────
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
    -Description "Starts the SPX Diagonal Calendar data collector at logon. The collector sleeps outside market hours and wakes at 09:30 ET on its own. Registered by register_collector_task.ps1." | Out-Null

Write-Host ""
Write-Host "Task registered." -ForegroundColor Green
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
Write-Host ""
Write-Host "Starts automatically at your next logon."
Write-Host "Start it now without logging out:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Remove it:                         Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
