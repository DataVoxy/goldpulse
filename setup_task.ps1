# Run this script as Administrator to register the GoldPulse scheduled task

$taskName   = "GoldPulse"
$scriptPath = "C:\Users\tommi\Downloads\GoldPulse\core\strategy.py"
$pyExe      = (Get-Command py).Source

# Delete old task if exists
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

# Action: run the batch file (handles cd and output capture)
$action = New-ScheduledTaskAction `
    -Execute "C:\Users\tommi\Downloads\GoldPulse\run_strategy.bat" `
    -WorkingDirectory "C:\Users\tommi\Downloads\GoldPulse"

# Trigger: every 30 minutes starting now
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 30)

# Settings
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 25) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

# Register under current user — Interactive logon (not elevated)
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force

Write-Host "Task registered. Starting now..."
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 5
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $taskName | Select-Object LastRunTime, NextRunTime, LastTaskResult
