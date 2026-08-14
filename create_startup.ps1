# Creates a shortcut to run_loop.bat in the Windows Startup folder
$startupPath = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Startup")
$shortcutPath = Join-Path $startupPath "GoldPulse.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "C:\Users\tommi\Downloads\GoldPulse\run_loop.bat"
$shortcut.WorkingDirectory = "C:\Users\tommi\Downloads\GoldPulse"
$shortcut.WindowStyle = 7  # Minimized
$shortcut.Save()

Write-Host "Startup shortcut created at: $shortcutPath"
Write-Host "GoldPulse will run automatically (minimized) when you log in."
