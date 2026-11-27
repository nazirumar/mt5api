<#
.SYNOPSIS
    Prints a compact status table for the Nazbeen MT5 Market Data API.

.DESCRIPTION
    Checks Python/venv, the MetaTrader terminal executable and process
    (terminal64.exe or terminal.exe), the API port listener, Caddy process and
    port 80, domain resolution, hosts entry, the /api/v1/health endpoint and
    recent log errors. The API key is read from .env for the request but is
    never printed. A live MT5 connection is only claimed when a terminal
    process exists AND the API reports terminal_connected=true.

.PARAMETER Json
    Emit a JSON object instead of the colored table.

.PARAMETER Port
    API port to check. Defaults to API_PORT from .env (8080 when unset).

.PARAMETER TimeoutSec
    HTTP timeout for the health request. Default 3 seconds.

.EXAMPLE
    .\scripts\check_status.ps1

.EXAMPLE
    .\scripts\check_status.ps1 -Json | ConvertFrom-Json
#>

param(
    [switch]$Json,
    [int]$Port = 0,
    [int]$TimeoutSec = 3
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$effectivePort = if ($Port -gt 0) { $Port } else { Get-ApiPort }

# Collect everything silently first so -Json output stays clean.
$python = Find-Python
$venvExists = (Test-Path -LiteralPath $script:VenvPython -PathType Leaf)
$configuredTerminal = Get-EnvValue -Path $script:EnvFile -Key 'MT5_PATH'
$terminalPath = Find-MT5Terminal -ConfiguredPath $configuredTerminal
$mt5Processes = @(Get-MT5Processes)
$mt5Running = ($mt5Processes.Count -gt 0)
$listeners = @(Get-PortListeners -Port $effectivePort)
$portListening = ($listeners.Count -gt 0)
$caddyEnabled = Get-ConfigBool -Key 'ENABLE_CADDY' -Default $true
$caddyPath = Find-Caddy
$caddyProcesses = @(Get-CaddyProcesses)
$port80Listening = (Test-PortListening -Port 80)
$domainName = Get-Domain
$resolution = Get-DomainResolution -Domain $domainName -Expected '127.0.0.1'
$hostsEntry = Test-HostsEntry -Domain $domainName
$health = Invoke-ApiHealth -Port $effectivePort -TimeoutSec $TimeoutSec
$watchdogTask = $null
try { $watchdogTask = Get-ScheduledTask -TaskName 'NazbeenMT5Watchdog' -ErrorAction Stop } catch { }
$recentErrors = @(Get-RecentLogErrors -Max 5)
$degraded = (-not $health.Reachable) -or (-not $health.TerminalConnected) -or (-not $mt5Running)
$connectionClaimed = ($health.Reachable -and $health.TerminalConnected -and $mt5Running)

if ($Json) {
    $payload = [ordered]@{
        generated_at = (Get-Date).ToString('o')
        project_root = $script:ProjectRoot
        api_port     = $effectivePort
        python       = [ordered]@{
            path           = if ($python) { $python.Path } else { $null }
            version        = if ($python) { [string]$python.Version } else { $null }
            meets_minimum  = if ($python) { [bool]$python.MeetsMinimum } else { $false }
        }
        venv         = [ordered]@{ exists = $venvExists; python = $script:VenvPython }
        mt5          = [ordered]@{
            executable        = $terminalPath
            process_running   = $mt5Running
            process_ids       = @($mt5Processes.Id)
            connection_claimed = $connectionClaimed
        }
        api_listener = [ordered]@{ listening = $portListening; owning_pids = @($listeners.OwningProcess) }
        caddy        = [ordered]@{
            enabled          = $caddyEnabled
            executable       = $caddyPath
            process_running  = (@($caddyProcesses).Count -gt 0)
            port_80_listening = $port80Listening
        }
        domain       = [ordered]@{
            name           = $domainName
            addresses      = @($resolution.Addresses)
            expected_found = $resolution.ExpectedFound
            hosts_entry    = $hostsEntry
        }
        health       = [ordered]@{
            reachable         = $health.Reachable
            http_status       = $health.HttpStatus
            status            = $health.Status
            terminal_connected = $health.TerminalConnected
            data_freshness    = $health.DataFreshness
            degraded          = $degraded
            error             = $health.Error
        }
        watchdog     = [ordered]@{ present = ($null -ne $watchdogTask) }
        recent_errors = @($recentErrors)
    }
    $payload | ConvertTo-Json -Depth 8
    exit 0
}

function Shorten {
    param([string]$Text, [int]$Length = 78)
    if ([string]::IsNullOrWhiteSpace($Text)) { return '-' }
    if ($Text.Length -le $Length) { return $Text }
    return $Text.Substring(0, $Length) + '...'
}

$rows = @(
    [pscustomobject]@{ Check = 'Python 3.12+'; Status = if ($python -and $python.MeetsMinimum) { 'OK' } elseif ($python) { 'WARN' } else { 'FAIL' }; Detail = Shorten $(if ($python) { '{0} @ {1}' -f $python.Version, $python.Path } else { 'not found' }) },
    [pscustomobject]@{ Check = 'Virtual env'; Status = if ($venvExists) { 'OK' } else { 'FAIL' }; Detail = Shorten $script:VenvPython },
    [pscustomobject]@{ Check = 'MT5 executable'; Status = if ($terminalPath) { 'OK' } else { 'WARN' }; Detail = Shorten $(if ($terminalPath) { $terminalPath } else { 'terminal64.exe / terminal.exe not found' }) },
    [pscustomobject]@{ Check = 'MT5 process'; Status = if ($mt5Running) { 'OK' } else { 'WARN' }; Detail = Shorten $(if ($mt5Running) { 'PID(s): ' + (($mt5Processes.Id) -join ', ') } else { 'not running - no live MT5 connection' }) },
    [pscustomobject]@{ Check = ("API port {0}" -f $effectivePort); Status = if ($portListening) { 'OK' } else { 'WARN' }; Detail = Shorten $(if ($portListening) { 'PID(s): ' + (($listeners.OwningProcess) -join ', ') } else { 'no listener' }) },
    [pscustomobject]@{ Check = 'Caddy executable'; Status = if (-not $caddyEnabled) { 'N/A' } elseif ($caddyPath) { 'OK' } else { 'WARN' }; Detail = Shorten $(if (-not $caddyEnabled) { 'ENABLE_CADDY=false' } elseif ($caddyPath) { $caddyPath } else { 'not found' }) },
    [pscustomobject]@{ Check = 'Caddy process/80'; Status = if (-not $caddyEnabled) { 'N/A' } elseif (@($caddyProcesses).Count -gt 0 -and $port80Listening) { 'OK' } else { 'WARN' }; Detail = Shorten $(if (-not $caddyEnabled) { 'not required' } else { ('process={0}; port80={1}' -f (@($caddyProcesses).Count -gt 0), $port80Listening) }) },
    [pscustomobject]@{ Check = 'Domain'; Status = if ($resolution.ExpectedFound) { 'OK' } else { 'WARN' }; Detail = Shorten $(if ($resolution.Addresses.Count -gt 0) { ('{0} -> {1}' -f $domainName, (($resolution.Addresses) -join ', ')) } else { ('{0} unresolved' -f $domainName) }) },
    [pscustomobject]@{ Check = 'Hosts entry'; Status = if ($hostsEntry) { 'OK' } else { 'WARN' }; Detail = Shorten $(if ($hostsEntry) { 'present' } else { 'missing' }) },
    [pscustomobject]@{ Check = 'API health'; Status = if ($health.Reachable -and -not $degraded) { 'OK' } elseif ($health.Reachable) { 'WARN' } else { 'FAIL' }; Detail = Shorten $(if ($health.Reachable) { $health.Detail } else { $health.Error }) },
    [pscustomobject]@{ Check = 'Watchdog task'; Status = if ($watchdogTask) { 'OK' } else { 'WARN' }; Detail = if ($watchdogTask) { 'NazbeenMT5Watchdog registered' } else { 'not registered - run install_autostart.ps1' } },
    [pscustomobject]@{ Check = 'Recent errors'; Status = if ($recentErrors.Count -eq 0) { 'OK' } else { 'WARN' }; Detail = Shorten $(if ($recentErrors.Count -eq 0) { 'none in logs' } else { ('{0} line(s): {1}' -f $recentErrors.Count, $recentErrors[0]) }) }
)

Write-Host "`nNazbeen MT5 Market Data API - status" -ForegroundColor Cyan
$rows | Format-Table -AutoSize -Wrap | Out-Host

if ($connectionClaimed) {
    Write-Ok 'MT5 connection reported by API and a terminal process is present.'
} else {
    Write-WarnMsg 'No live MT5 connection is claimed (terminal missing or API reports degraded).'
}
Write-Info ("API URL: http://127.0.0.1:{0}/docs" -f $effectivePort)
exit 0
