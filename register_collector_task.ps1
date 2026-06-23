# ============================================================
# register_collector_task.ps1
# One-time setup: registers the SPX collector as a Windows
# Scheduled Task that starts automatically at every logon.
#
# HOW TO RUN (do this once):
#   1. Right-click PowerShell → "Run as Administrator"
#   2. Navigate to your project folder:
#        cd "C:\Users\chand\Python\spx-diagonal-dashboard"
#   3. Run this script:
#        .\register_collector_task.ps1
#
# TO VERIFY it registered correctly:
#   - Open Task Scheduler (search "Task Scheduler" in Start)
#   - Look for "SPX Diagonal Collector" under Task Scheduler Library
#
# TO REMOVE the task later:
#   Unregister-ScheduledTask -TaskName "SPX Diagonal Collector" -Confirm:$false
# ============================================================

$TaskName    = "SPX Diagonal Collector"
$ProjectDir  = "C:\Users\chand\Python\spx-diagonal-dashboard"
$BatchFile   = "$ProjectDir\start_collector.bat"

# ── Verify the batch file exists before registering ──────────────────────────
if (-not (Test-Path $BatchFile)) {
    Write-Error "start_collector.bat not found at: $BatchFile"
    Write-Error "Make sure you have deployed start_collector.bat to the project folder."
    exit 1
}

# ── Task Action: run the batch file in a minimized window ────────────────────
# Using cmd.exe /c so the window title and pause-on-error behavior work correctly.
$Action = New-ScheduledTaskAction `
    -Execute  "cmd.exe" `
    -Argument "/c `"$BatchFile`"" `
    -WorkingDirectory $ProjectDir

# ── Task Trigger: fire at every user logon ───────────────────────────────────
# The collector handles market hours itself — it sleeps until 9:30 AM ET
# regardless of when the computer boots or the user logs in.
$Trigger = New-ScheduledTaskTrigger -AtLogon

# ── Task Settings ─────────────────────────────────────────────────────────────
$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances    IgnoreNew `    # Never run two collectors simultaneously
    -ExecutionTimeLimit   (New-TimeSpan -Seconds 0) `  # No time limit (runs all day)
    -StartWhenAvailable   $true `        # Start even if the trigger was missed
    -RestartCount         3 `            # Auto-restart up to 3 times on failure
    -RestartInterval      (New-TimeSpan -Minutes 5) ` # Wait 5 min between restarts
    -RunOnlyIfNetworkAvailable $true     # Wait for network before starting

# ── Principal: run as the current user, not SYSTEM ───────────────────────────
# Running as current user means the Schwab token file and .env are accessible
# without any path or permission gymnastics.
$Principal = New-ScheduledTaskPrincipal `
    -UserId    $env:USERNAME `
    -LogonType Interactive `
    -RunLevel  Highest

# ── Register (or update if already exists) ───────────────────────────────────
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($existingTask) {
    Write-Host "Task '$TaskName' already exists — updating it..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName   $TaskName `
    -Action     $Action `
    -Trigger    $Trigger `
    -Settings   $Settings `
    -Principal  $Principal `
    -Description "Starts the SPX Diagonal Calendar data collector at logon. Runs until shutdown or manual stop."

if ($?) {
    Write-Host ""
    Write-Host "✓ Task registered successfully." -ForegroundColor Green
    Write-Host ""
    Write-Host "The collector will start automatically at your next logon."
    Write-Host "To start it right now without logging out:"
    Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
    Write-Host ""
    Write-Host "To check if it's running:"
    Write-Host "  Get-ScheduledTask -TaskName '$TaskName' | Select-Object TaskName, State"
    Write-Host ""
    Write-Host "To stop it:"
    Write-Host "  Stop-ScheduledTask -TaskName '$TaskName'"
    Write-Host ""
    Write-Host "To remove the task entirely:"
    Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
} else {
    Write-Error "Task registration failed. Make sure you ran PowerShell as Administrator."
}
