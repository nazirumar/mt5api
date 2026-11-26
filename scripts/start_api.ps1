<#
.SYNOPSIS
    Starts the uvicorn API on port 8080 (or a chosen port).

.DESCRIPTION
    Checks the listening port and the uvicorn command line before starting, so
    an existing API instance is never duplicated. Launches the API from the
    project root with the virtual-environment Python and hidden window, then
    waits for the /api/v1/health endpoint. When no terminal64.exe/terminal.exe
    process is running, a degraded-mode warning is printed and no live MT5
    connection is claimed.

.PARAMETER Port
    Port to use. Defaults to API_PORT from .env (8080 when unset).

.PARAMETER WaitSeconds
    Maximum time to wait for the health endpoint. Default 30.

.EXAMPLE
    .\scripts\start_api.ps1

.EXAMPLE
    .\scripts\start_api.ps1 -Port 8081 -WaitSeconds 45
#>

param(
    [int]$Port = 0,
    [int]$WaitSeconds = 30
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$effectivePort = if ($Port -gt 0) { $Port } else { Get-ApiPort }

Write-Step ("Starting API on port {0}" -f $effectivePort)

$listeners = @(Get-PortListeners -Port $effectivePort)
$apiProcesses = @(Get-ApiProcesses)
if ($listeners.Count -gt 0 -or $apiProcesses.Count -gt 0) {
    Write-Ok ("API already active (listener PID(s): {0}; API PID(s): {1}). No duplicate started." -f `
        (($listeners.OwningProcess) -join ', '), (($apiProcesses.ProcessId) -join ', '))
    exit 0
}

if (-not (Test-Path -LiteralPath $script:VenvPython -PathType Leaf)) {
    Write-Fail ("Virtual environment Python not found: {0}" -f $script:VenvPython)
    Write-Hint 'Run .\scripts\install.ps1 first.'
    exit 1
}

$hostAddress = Get-ConfigValue -Key 'API_HOST' -Default '127.0.0.1'
try {
    $arguments = @('-m', 'uvicorn', 'app.main:app', '--host', $hostAddress, '--port', [string]$effectivePort)
    $process = Start-Process -FilePath $script:VenvPython -ArgumentList $arguments `
        -WorkingDirectory $script:ProjectRoot -WindowStyle Hidden -PassThru
    Write-Ok ("Started API process (PID {0})." -f $process.Id)
} catch {
    Write-Fail ("Failed to start API: {0}" -f $_.Exception.Message)
    exit 1
}

$deadline = (Get-Date).AddSeconds($WaitSeconds)
$health = $null
do {
    $health = Invoke-ApiHealth -Port $effectivePort -TimeoutSec 2
    if ($health.Reachable) { break }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)

if (-not $health.Reachable) {
    Write-WarnMsg ("API process started but health endpoint is not responding yet on port {0}." -f $effectivePort)
    if ($health.Error) { Write-Hint $health.Error }
    Write-Hint 'Check logs\ for startup errors (for example an invalid API_KEY or missing dependencies).'
    exit 2
}

Write-Ok ("Health endpoint reachable: {0}" -f $health.Detail)
if (-not (Test-MT5ProcessRunning)) {
    Write-WarnMsg 'MT5 terminal is not running (terminal64.exe / terminal.exe). API is in degraded mode; no live MT5 connection is claimed.'
} elseif (-not $health.TerminalConnected) {
    Write-WarnMsg 'Terminal process exists, but the API reports terminal_connected=false. Check MT5_LOGIN/MT5_PASSWORD/MT5_SERVER in .env.'
} else {
    Write-Ok 'API reports a connected MT5 terminal.'
}

Write-KV 'API URL' ("http://127.0.0.1:{0}" -f $effectivePort)
Write-KV 'Docs URL' ("http://127.0.0.1:{0}/docs" -f $effectivePort)
exit 0
