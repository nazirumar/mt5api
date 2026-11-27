<#
.SYNOPSIS
    Lightweight self-healing check for the local MT5 market data service.

.DESCRIPTION
    Intended to run every 5 minutes from a user-level Task Scheduler task. It
    checks the public health endpoint, terminal process, API listener and
    optional Caddy proxy. It starts missing components, restarts an API that
    is listening but unhealthy, and rate-limits restarts to avoid loops when
    a broker login problem persists. No secrets are printed or logged.

.PARAMETER Json
    Emit a structured JSON object instead of colored text.

.PARAMETER Port
    API port to check. Defaults to API_PORT from .env (8080 when unset).

.PARAMETER WaitSeconds
    Maximum wait for health after a start/restart. Default 20.

.EXAMPLE
    .\scripts\watchdog.ps1

.EXAMPLE
    .\scripts\watchdog.ps1 -Json | ConvertFrom-Json
#>

param(
    [switch]$Json,
    [int]$Port = 0,
    [int]$WaitSeconds = 20
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$effectivePort = if ($Port -gt 0) { $Port } else { Get-ApiPort }
$script:WatchdogStateFile = Join-Path $script:DataDir 'watchdog-state.json'
$restartCooldownMinutes = 10

function Get-WatchdogState {
    try {
        if (Test-Path -LiteralPath $script:WatchdogStateFile -PathType Leaf) {
            $state = Get-Content -LiteralPath $script:WatchdogStateFile -Raw -ErrorAction Stop | ConvertFrom-Json
            if ($state -and $state.PSObject.Properties.Name -contains 'LastRestartUtc') {
                return $state
            }
        }
    } catch {
        # A malformed state file is treated as a fresh state.
    }
    return [pscustomobject]@{ LastRestartUtc = $null }
}

function Set-WatchdogRestarted {
    if (-not (Test-Path -LiteralPath $script:DataDir -PathType Container)) {
        New-Item -ItemType Directory -Path $script:DataDir -Force | Out-Null
    }
    [pscustomobject]@{ LastRestartUtc = (Get-Date).ToUniversalTime().ToString('o') } |
        ConvertTo-Json -Compress |
        Set-Content -LiteralPath $script:WatchdogStateFile -Encoding UTF8
}

function Test-RestartAllowed {
    $state = Get-WatchdogState
    if ([string]::IsNullOrWhiteSpace([string]$state.LastRestartUtc)) { return $true }
    try {
        $last = [datetimeoffset]::Parse([string]$state.LastRestartUtc).ToUniversalTime()
        return ((Get-Date).ToUniversalTime() - $last).TotalMinutes -ge $restartCooldownMinutes
    } catch {
        return $true
    }
}

function Invoke-RepairScript {
    param(
        [Parameter(Mandatory)][string]$Name,
        [hashtable]$Arguments = @{}
    )
    $path = Join-Path $script:ScriptsDir $Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Write-AutostartLog ("ERROR: watchdog helper missing: {0}" -f $Name)
        return 1
    }
    # Helper scripts use Write-Host for progress. In JSON mode, keep stdout
    # machine-readable by redirecting every stream to the autostart log only.
    if ($Json) {
        & $path @Arguments *>$null
    } else {
        & $path @Arguments
    }
    $code = $LASTEXITCODE
    if ($code -gt 1) {
        Write-AutostartLog ("WARN: {0} exited with code {1}" -f $Name, $code)
    }
    return $code
}

function Start-WatchdogProxy {
    if (-not (Get-ConfigBool -Key 'ENABLE_CADDY' -Default $true)) { return $false }
    $caddy = Find-Caddy
    if ([string]::IsNullOrWhiteSpace($caddy)) { return $false }
    if (@(Get-CaddyProcesses).Count -gt 0 -or (Test-PortListening -Port 80)) { return $false }
    try {
        $arguments = @('run', '--config', ('"{0}"' -f $script:Caddyfile))
        Start-Process -FilePath $caddy -ArgumentList $arguments `
            -WorkingDirectory (Split-Path -Parent $script:Caddyfile) `
            -WindowStyle Hidden | Out-Null
        return $true
    } catch {
        Write-AutostartLog ("ERROR: watchdog could not start Caddy: {0}" -f $_.Exception.Message)
        return $false
    }
}

$health = Invoke-ApiHealth -Port $effectivePort -TimeoutSec 3
$result = [pscustomobject]@{
    TimeUtc           = (Get-Date).ToUniversalTime().ToString('o')
    Port              = $effectivePort
    ApiReachable      = [bool]$health.Reachable
    ApiStatus         = [string]$health.Status
    TerminalConnected = [bool]$health.TerminalConnected
    DataFreshness     = [string]$health.DataFreshness
    Action            = 'ok'
    TerminalStarted   = $false
    ApiRestarted      = $false
    ProxyStarted      = $false
    RestartSkipped    = $false
    Error             = $null
}

$terminalProcesses = @(Get-MT5Processes)
$terminalStarted = $false
if ($terminalProcesses.Count -eq 0 -and (Get-ConfigBool -Key 'MT5_START_TERMINAL' -Default $true)) {
    $code = Invoke-RepairScript -Name '_launch_terminal.ps1'
    if ($code -eq 0) {
        $terminalStarted = $true
        $result.TerminalStarted = $true
        $result.Action = 'terminal-started'
        Write-AutostartLog 'Watchdog started the MT5 terminal.'
    } else {
        $result.Error = 'terminal-start-failed'
    }
}

# A terminal started above can change the API's health before the repair
# decision is made. Refresh it so a stale pre-repair reading cannot force an
# immediate API restart.
if ($terminalStarted) {
    $health = Invoke-ApiHealth -Port $effectivePort -TimeoutSec 3
    $result.ApiReachable = [bool]$health.Reachable
    $result.ApiStatus = [string]$health.Status
    $result.TerminalConnected = [bool]$health.TerminalConnected
    $result.DataFreshness = [string]$health.DataFreshness
}

if (-not $health.Reachable) {
    if (Test-PortListening -Port $effectivePort) {
        # A listener is present, so this is a restart: respect the same
        # cooldown used for the reachable-but-unhealthy branch.
        if (Test-RestartAllowed) {
            $code = Invoke-RepairScript -Name 'restart_api.ps1' -Arguments @{ Port = $effectivePort; WaitSeconds = $WaitSeconds }
            if ($code -eq 0) {
                $result.Action = 'api-restarted'
                $result.ApiRestarted = $true
                Set-WatchdogRestarted
                Write-AutostartLog ("Watchdog {0}." -f $result.Action)
            } else {
                $result.Error = 'api-repair-failed'
            }
        } else {
            $result.Action = 'waiting-for-reconnect'
            $result.RestartSkipped = $true
        }
    } else {
        $code = Invoke-RepairScript -Name 'start_api.ps1' -Arguments @{ Port = $effectivePort; WaitSeconds = $WaitSeconds }
        if ($code -eq 0) {
            $result.Action = 'api-started'
            $result.ApiRestarted = $true
            Set-WatchdogRestarted
            Write-AutostartLog ("Watchdog {0}." -f $result.Action)
        } else {
            $result.Error = 'api-repair-failed'
        }
    }
} elseif ($terminalStarted -or -not $health.TerminalConnected) {
    if (@(Get-MT5Processes).Count -gt 0 -and (Test-RestartAllowed)) {
        $code = Invoke-RepairScript -Name 'restart_api.ps1' -Arguments @{ Port = $effectivePort; WaitSeconds = $WaitSeconds }
        if ($code -eq 0) {
            $result.Action = 'api-restarted'
            $result.ApiRestarted = $true
            Set-WatchdogRestarted
            Write-AutostartLog 'Watchdog restarted an unhealthy API process.'
        } else {
            $result.Error = 'api-restart-failed'
        }
    } else {
        $result.Action = 'waiting-for-reconnect'
        $result.RestartSkipped = $true
    }
}

if (Start-WatchdogProxy) {
    $result.ProxyStarted = $true
    Write-AutostartLog 'Watchdog started Caddy.'
}

$detail = ('health={0}; terminal_connected={1}; action={2}' -f `
    $result.ApiStatus, $result.TerminalConnected, $result.Action)
if ($Json) {
    $result | ConvertTo-Json -Compress
} else {
    Write-Info $detail
    if (-not [string]::IsNullOrWhiteSpace($result.Error)) {
        Write-WarnMsg ("Watchdog issue: {0}" -f $result.Error)
    }
}

# Watchdog tasks use retry settings; expose failures for Task Scheduler history.
if (-not [string]::IsNullOrWhiteSpace($result.Error)) { exit 1 }
exit 0
