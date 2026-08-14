# Creates a Windows Scheduled Task to update all dashboards every 30 minutes
# Run once as admin: powershell -ExecutionPolicy Bypass -File setup_dashboard_task.ps1

$taskName = "DataVoxy_Dashboards"
$scriptPath = Join-Path $PSScriptRoot "update_all_dashboards.py"
$pythonPath = "py"

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument "`"$scriptPath`"" `
    -WorkingDirectory $PSScriptRoot

$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 30)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Updates GoldPulse, SilverPulse, CryptoPulse dashboards and uploads to R2" `
    -RunLevel Limited

Write-Host "Task '$taskName' created. Runs every 30 min."
Write-Host "To remove: Unregister-ScheduledTask -TaskName '$taskName'"
