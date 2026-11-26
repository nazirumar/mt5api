<#
.SYNOPSIS
    Autostart helper: starts Caddy with the project Caddyfile after logon.

.DESCRIPTION
    Used by the NazbeenMT5Proxy logon task. Runs only when ENABLE_CADDY is
    true and a Caddy executable can be discovered (CADDY_PATH, PATH, where.exe
    or common install locations). Waits ~45 seconds and skips if Caddy is
    already running or port 80 is already occupied.

.NOTES
    Generated/managed by install_autostart.ps1. Edits are preserved when the
    file already exists.
#>

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$startupDelaySeconds = 45

if (-not (Get-ConfigBool -Key 'ENABLE_CADDY' -Default $true)) {
    Write-AutostartLog 'ENABLE_CADDY=false; skipping Caddy autostart.'
    exit 0
}

Write-AutostartLog ("Caddy autostart: waiting {0}s before launch." -f $startupDelaySeconds)
Start-Sleep -Seconds $startupDelaySeconds

$caddy = Find-Caddy
if ([string]::IsNullOrWhiteSpace($caddy)) {
    Write-AutostartLog 'WARN: Caddy executable not found; skipping proxy autostart. Set CADDY_PATH or install Caddy.'
    exit 0
}

if (@(Get-CaddyProcesses).Count -gt 0 -or (Test-PortListening -Port 80)) {
    Write-AutostartLog 'Caddy already running or port 80 in use; skipping duplicate start.'
    exit 0
}

if (-not (Test-Path -LiteralPath $script:Caddyfile -PathType Leaf)) {
    Write-AutostartLog ("ERROR: Caddyfile missing: {0}" -f $script:Caddyfile)
    exit 1
}

try {
    $arguments = @('run', '--config', ('"{0}"' -f $script:Caddyfile))
    $process = Start-Process -FilePath $caddy -ArgumentList $arguments `
        -WorkingDirectory (Split-Path -Parent $script:Caddyfile) `
        -WindowStyle Hidden -PassThru
    Write-AutostartLog ("Started Caddy PID {0} with {1} (hidden)." -f $process.Id, $script:Caddyfile)
} catch {
    Write-AutostartLog ("ERROR: could not start Caddy: {0}" -f $_.Exception.Message)
    exit 1
}
