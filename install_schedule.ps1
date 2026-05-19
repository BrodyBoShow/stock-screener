# Registers a Windows Scheduled Task to run the screener every weekday at 6:00 PM
# (after US market close). Run this script ONCE — right-click > "Run with PowerShell".

$TaskName = "StockScreener_Weekly"
$ScriptDir = $PSScriptRoot
$BatFile = Join-Path $ScriptDir "run_screener.bat"

if (-not (Test-Path $BatFile)) {
    Write-Error "Could not find run_screener.bat in $ScriptDir"
    exit 1
}

# Run weekdays at 6:00 PM — after market close, fresh closing-price fundamentals
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 6:00PM

# Run silently — suppress the auto-open of Excel for scheduled runs
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c set SCREENER_NO_OPEN=1 && `"$BatFile`"" `
    -WorkingDirectory $ScriptDir

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

# Remove old version if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $Trigger `
    -Action $Action `
    -Settings $Settings `
    -Description "Auto-runs the stock screener every weekday at 6 PM. Output: screener_data.db + Excel + winners_latest.txt"

Write-Host ""
Write-Host "Scheduled task '$TaskName' installed." -ForegroundColor Green
Write-Host "It will run weekdays at 6:00 PM."
Write-Host "Manage it any time in Task Scheduler, or remove with:"
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
