<#
.SYNOPSIS
    Stops the uvicorn API process listening on port 8080 (or a chosen port).

.DESCRIPTION
    Finds API processes by their uvicorn app.main:app command line and by the
    listening port. Processes on the port are only stopped when they are
    Python/uvicorn processes; unrelated services are left alone with a
    warning. Stops gracefully first, forces only as a last resort, and waits
    for the port to be released. Idempotent.

.PARAMETER Port
    Port to inspect. Defaults to API_PORT from .env (8080 when unset).

.EXAMPLE
    .\scripts\stop_api.ps1

.EXAMPLE
    .\scripts\stop_api.ps1 -Port 8081
#>

param([int]$Port = 0)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$effectivePort = if ($Port -gt 0) { $Port } else { Get-ApiPort }

Write-Step ("Stopping API on port {0}" -f $effectivePort)

$processIds = [System.Collections.Generic.List[int]]::new()
foreach ($process in @(Get-ApiProcesses)) { $processIds.Add([int]$process.ProcessId) }

foreach ($listener in @(Get-PortListeners -Port $effectivePort)) {
    $owner = [int]$listener.OwningProcess
    if ($owner -le 0) { continue }
    try {
        $ownedProcess = Get-Process -Id $owner -ErrorAction Stop
        if ($ownedProcess.ProcessName -match '^(python|pythonw|uvicorn)$') {
            $processIds.Add($owner)
        } else {
            Write-WarnMsg ("Port {0} is owned by {1} (PID {2}); leaving it running." -f $effectivePort, $ownedProcess.ProcessName, $owner)
        }
    } catch {
        # Process already exited between query and lookup.
    }
}

$uniqueIds = @($processIds | Select-Object -Unique)
if ($uniqueIds.Count -eq 0) {
    Write-Ok ("No API process found on port {0}; nothing to stop." -f $effectivePort)
    exit 0
}

foreach ($id in $uniqueIds) {
    try {
        Stop-Process -Id $id -ErrorAction Stop
        Write-Ok ("Stopped PID {0} gracefully." -f $id)
    } catch {
        Write-WarnMsg ("Could not stop PID {0} gracefully: {1}" -f $id, $_.Exception.Message)
    }
}

$deadline = (Get-Date).AddSeconds(10)
while ((Get-Date) -lt $deadline -and (Test-PortListening -Port $effectivePort)) {
    Start-Sleep -Milliseconds 500
}

if (Test-PortListening -Port $effectivePort) {
    foreach ($id in $uniqueIds) {
        try {
            $stillRunning = Get-Process -Id $id -ErrorAction Stop
            Stop-Process -Id $id -Force -ErrorAction Stop
            Write-WarnMsg ("Force-stopped PID {0} after graceful stop timed out." -f $id)
        } catch {
            # Already gone, or not accessible.
        }
    }
    Start-Sleep -Seconds 1
}

if (Test-PortListening -Port $effectivePort) {
    Write-WarnMsg ("Port {0} is still listening; check for another service." -f $effectivePort)
    exit 2
}

Write-Ok ("Port {0} is free." -f $effectivePort)
exit 0
