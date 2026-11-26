<#
.SYNOPSIS
    Restarts the uvicorn API on port 8080 (or a chosen port).

.DESCRIPTION
    Calls stop_api.ps1 then start_api.ps1 with the same resolved port. Stops
    gracefully, avoids duplicate starts, waits for the health endpoint and
    reports degraded mode when no MetaTrader terminal is running.

.PARAMETER Port
    Port to restart. Defaults to API_PORT from .env (8080 when unset).

.PARAMETER WaitSeconds
    Maximum time to wait for the health endpoint after starting. Default 30.

.EXAMPLE
    .\scripts\restart_api.ps1

.EXAMPLE
    .\scripts\restart_api.ps1 -Port 8081 -WaitSeconds 45
#>

param(
    [int]$Port = 0,
    [int]$WaitSeconds = 30
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$effectivePort = if ($Port -gt 0) { $Port } else { Get-ApiPort }

Write-Step ("Restarting API on port {0}" -f $effectivePort)

& (Join-Path $script:ScriptsDir 'stop_api.ps1') -Port $effectivePort
$stopCode = $LASTEXITCODE
if ($stopCode -gt 1) {
    Write-WarnMsg ("Stop step reported code {0}; continuing with start." -f $stopCode)
}

& (Join-Path $script:ScriptsDir 'start_api.ps1') -Port $effectivePort -WaitSeconds $WaitSeconds
$startCode = $LASTEXITCODE

if ($startCode -eq 0) { Write-Ok 'API restart complete.' } else { Write-WarnMsg ("API restart finished with code {0}." -f $startCode) }
exit $startCode
