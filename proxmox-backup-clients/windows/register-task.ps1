# ==============================================================================
# register-task.ps1: a daily Task Scheduler task "PBS backup" that runs
# pbs-backup.ps1 from this folder. It runs as you, while you are logged on (WSL
# belongs to your account), and catches up when the PC was off at that time.
#   powershell -ExecutionPolicy Bypass -File register-task.ps1 [-Time 12:30] [-Distro Ubuntu]
# Remove it again with: Unregister-ScheduledTask -TaskName "PBS backup"
# ==============================================================================
param(
    [string]$Time = "12:30",
    [string]$Distro = "Ubuntu"
)
$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "pbs-backup.ps1"
if (-not (Test-Path $script)) { throw "pbs-backup.ps1 is not next to this script" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Distro $Distro"
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 12) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "PBS backup" -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Back up folders to Proxmox Backup Server through WSL" -Force | Out-Null

Write-Host "Task 'PBS backup' runs daily at $Time. Start it now with: Start-ScheduledTask -TaskName 'PBS backup'"
