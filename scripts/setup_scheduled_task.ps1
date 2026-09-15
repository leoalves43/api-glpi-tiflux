# Registers/updates the "GLPI-Tiflux-Sync" Windows Scheduled Task.
# Must run in an elevated (Administrator) PowerShell, because the task
# runs as SYSTEM so it works whether or not a user is logged on.
#
# Usage: powershell -ExecutionPolicy Bypass -File scripts\setup_scheduled_task.ps1

$ErrorActionPreference = "Stop"

$taskName = "GLPI-Tiflux-Sync"
$projectDir = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectDir "run_glpi_tiflux.bat"

if (-not (Test-Path $scriptPath)) {
    throw "Wrapper script not found at $scriptPath"
}

$action = New-ScheduledTaskAction -Execute $scriptPath -WorkingDirectory $projectDir
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force `
    -Description "Runs glpi_tiflux.py every 5 minutes to sync GLPI chamados with Tiflux."

Write-Host "Scheduled task '$taskName' registered: runs $scriptPath every 5 minutes as SYSTEM."
