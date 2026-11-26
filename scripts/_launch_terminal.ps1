<#
.SYNOPSIS
    Autostart helper: launches the configured MetaTrader 5 terminal if needed.

.DESCRIPTION
    Used by the NazbeenMT5Terminal logon task. Reads MT5_PATH from .env (or
    discovers terminal64.exe / terminal.exe), checks for an already running
    terminal process, and starts the executable only when necessary. Safe to
    run repeatedly; it never starts a duplicate terminal.

.NOTES
    Generated/managed by install_autostart.ps1. Edits are preserved when the
    file already exists.
#>

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$configured = Get-EnvValue -Path $script:EnvFile -Key 'MT5_PATH'
$terminalPath = Find-MT5Terminal -ConfiguredPath $configured

if ([string]::IsNullOrWhiteSpace($terminalPath)) {
    $message = 'No MetaTrader 5 executable found (checked terminal64.exe and terminal.exe). Skipping terminal autostart.'
    Write-WarnMsg $message
    Write-AutostartLog "WARN: $message"
    exit 0
}

$running = @(Get-MT5Processes)
if ($running.Count -gt 0) {
    Write-Ok ("MT5 terminal already running (PID(s): {0})." -f (($running.Id) -join ', '))
    Write-AutostartLog 'Terminal already running; no duplicate started.'
    exit 0
}

try {
    $process = Start-Process -FilePath $terminalPath -WorkingDirectory (Split-Path -Parent $terminalPath) -PassThru
    Write-Ok ("Started MT5 terminal: {0} (PID {1})." -f $terminalPath, $process.Id)
    Write-AutostartLog ("Started terminal {0} (PID {1})." -f $terminalPath, $process.Id)
} catch {
    Write-Fail ("Could not start MT5 terminal: {0}" -f $_.Exception.Message)
    Write-AutostartLog ("ERROR: could not start terminal: {0}" -f $_.Exception.Message)
    exit 1
}
