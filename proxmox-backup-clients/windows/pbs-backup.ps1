# ==============================================================================
# pbs-backup.ps1: runs the backup to Proxmox Backup Server inside WSL (the same
# pbs-backup script as on Ubuntu), and keeps a log in
# %LOCALAPPDATA%\pbs-backup\backup.log. Task Scheduler starts it (see
# register-task.ps1); it can also be run by hand:
#   powershell -ExecutionPolicy Bypass -File pbs-backup.ps1 [-Distro Ubuntu] [-DryRun]
# ==============================================================================
param(
    [string]$Distro = "Ubuntu",
    [switch]$DryRun
)

# A native program writing to stderr must not stop the script
$ErrorActionPreference = "Continue"

$logDir = Join-Path $env:LOCALAPPDATA "pbs-backup"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "backup.log"

# Keep the log from growing without end: start a new one above 5 MB
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 5MB)) {
    Move-Item -Force $log "$log.old"
}

function Write-Log([string]$line) {
    "$(Get-Date -Format 's') $line" | Out-File -FilePath $log -Append -Encoding utf8
}

$wslArgs = @("-d", $Distro, "-u", "root", "--", "/usr/local/sbin/pbs-backup")
if ($DryRun) { $wslArgs += "--dry-run" }

Write-Log "start ($Distro)"
# Shown and logged line by line (Tee-Object would write the log in another encoding)
& wsl.exe @wslArgs 2>&1 | ForEach-Object {
    $line = "$_"
    Write-Host $line
    $line | Out-File -FilePath $log -Append -Encoding utf8
}
$code = $LASTEXITCODE
Write-Log "end, exit code $code"
exit $code
