<#
.SYNOPSIS
    Autostart helper: starts the uvicorn API after a short logon delay.

.DESCRIPTION
    Used by the NazbeenMT5API logon task. Waits ~30 seconds, waits (with a
    bound) for a terminal64.exe/terminal.exe process, then starts the API from
    the project root using the virtual-environment Python. Checks both the
    listening port and the uvicorn command line so duplicates are never
    created. If the terminal is still absent, the API is started in degraded
    mode and the log explicitly states that no live MT5 connection is claimed.

.NOTES
    Generated/managed by install_autostart.ps1. Edits are preserved when the
    file already exists.
#>

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$startupDelaySeconds = 30
$terminalWaitSeconds = 120

$port = Get-ApiPort
$hostAddress = Get-ConfigValue -Key 'API_HOST' -Default '127.0.0.1'
Write-AutostartLog ("API autostart: waiting {0}s before launch (port {1})." -f $startupDelaySeconds, $port)
Start-Sleep -Seconds $startupDelaySeconds

$deadline = (Get-Date).AddSeconds($terminalWaitSeconds)
while ((Get-Date) -lt $deadline -and -not (Test-MT5ProcessRunning)) {
    Start-Sleep -Seconds 5
}

if (Test-MT5ProcessRunning) {
    Write-AutostartLog 'MT5 terminal process detected; starting API.'
} else {
    Write-AutostartLog ("WARN: no terminal64.exe/terminal.exe after {0}s; starting API in degraded mode. No live MT5 connection is claimed." -f $terminalWaitSeconds)
}

if ((Test-PortListening -Port $port) -or (@(Get-ApiProcesses).Count -gt 0)) {
    Write-AutostartLog ("API already listening/active on port {0}; skipping duplicate start." -f $port)
    exit 0
}

if (-not (Test-Path -LiteralPath $script:VenvPython -PathType Leaf)) {
    Write-AutostartLog ("ERROR: virtual environment Python missing: {0}" -f $script:VenvPython)
    exit 1
}

try {
    $arguments = @('-m', 'uvicorn', 'app.main:app', '--host', $hostAddress, '--port', [string]$port)
    $process = Start-Process -FilePath $script:VenvPython -ArgumentList $arguments `
        -WorkingDirectory $script:ProjectRoot -WindowStyle Hidden -PassThru
    Write-AutostartLog ("Started API PID {0} on {1}:{2} (hidden)." -f $process.Id, $hostAddress, $port)
} catch {
    Write-AutostartLog ("ERROR: could not start API: {0}" -f $_.Exception.Message)
    exit 1
}
