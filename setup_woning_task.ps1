# Creates Windows Scheduled Tasks to run woning_scraper.py twice daily
# Run once as admin: powershell -ExecutionPolicy Bypass -File setup_woning_task.ps1

$taskName1 = "WoningScraper_Ochtend"
$taskName2 = "WoningScraper_Middag"
$scriptPath = Join-Path $PSScriptRoot "woning_scraper.py"
$pythonPath = "py"

# Remove existing tasks
Unregister-ScheduledTask -TaskName $taskName1 -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $taskName2 -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument "`"$scriptPath`"" `
    -WorkingDirectory $PSScriptRoot

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

# Ochtend: 10:00
$trigger1 = New-ScheduledTaskTrigger -Daily -At 10:00

Register-ScheduledTask `
    -TaskName $taskName1 `
    -Action $action `
    -Trigger $trigger1 `
    -Settings $settings `
    -Description "Woning scraper - ochtend check (10:00)" `
    -RunLevel Limited

# Middag: 14:00
$trigger2 = New-ScheduledTaskTrigger -Daily -At 14:00

Register-ScheduledTask `
    -TaskName $taskName2 `
    -Action $action `
    -Trigger $trigger2 `
    -Settings $settings `
    -Description "Woning scraper - middag check (14:00)" `
    -RunLevel Limited

Write-Host ""
Write-Host "Done! Twee tasks aangemaakt:"
Write-Host "  - $taskName1 (10:00)"
Write-Host "  - $taskName2 (14:00)"
Write-Host ""
Write-Host "Verwijderen:"
Write-Host "  Unregister-ScheduledTask -TaskName '$taskName1'"
Write-Host "  Unregister-ScheduledTask -TaskName '$taskName2'"
