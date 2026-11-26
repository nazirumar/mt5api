<#
.SYNOPSIS
    Runs detailed diagnostics for the Nazbeen MT5 Market Data API.

.DESCRIPTION
    Checks Python, the virtual environment, installed packages, MT5 executable
    discovery (terminal64.exe and terminal.exe), terminal process, MetaTrader5
    import, API process, ports 8080/80, Caddy executable/service/task, domain
    resolution, hosts entry, HTTP health and docs responses, and prints a
    firewall note. Every check reports PASS/WARN/FAIL plus a suggested fix.
    A live MT5 connection is never claimed unless a terminal process is
    running AND the API reports terminal_connected=true.

    The exit code is non-zero only for critical failures: missing Python 3.12+,
    missing virtual environment, or missing required packages.

.PARAMETER Json
    Emit structured JSON instead of colored text.

.PARAMETER Port
    API port to check. Defaults to API_PORT from .env (8080 when unset).

.PARAMETER TimeoutSec
    HTTP timeout per request. Default 3 seconds.

.EXAMPLE
    .\scripts\diagnose.ps1

.EXAMPLE
    .\scripts\diagnose.ps1 -Json | ConvertFrom-Json
#>

param(
    [switch]$Json,
    [int]$Port = 0,
    [int]$TimeoutSec = 3
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$effectivePort = if ($Port -gt 0) { $Port } else { Get-ApiPort }
$results = [System.Collections.Generic.List[object]]::new()

function Add-Check {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Status,
        [Parameter(Mandatory)][string]$Detail,
        [string]$Fix = '',
        [bool]$Critical = $false
    )
    $results.Add([pscustomobject]@{
        name     = $Name
        status   = $Status
        detail   = $Detail
        fix      = $Fix
        critical = $Critical
    })
}

# 1. Python
$python = Find-Python
if ($python -and $python.MeetsMinimum) {
    Add-Check -Name 'Python 3.12+' -Status 'PASS' -Detail ("{0} at {1}" -f $python.Version, $python.Path)
} elseif ($python) {
    Add-Check -Name 'Python 3.12+' -Status 'WARN' -Critical $true `
        -Detail ("Found {0} at {1}; 3.12+ is required." -f $python.Version, $python.Path) `
        -Fix 'Install Python 3.12 or newer from python.org, then run scripts\install.ps1 -PythonPath "C:\path\to\python.exe".'
} else {
    Add-Check -Name 'Python 3.12+' -Status 'FAIL' -Critical $true -Detail 'No usable Python interpreter found.' `
        -Fix 'Install Python 3.12+, ensure it is on PATH, then run scripts\install.ps1.'
}

# 2. Virtual environment
$venvExists = (Test-Path -LiteralPath $script:VenvPython -PathType Leaf)
if ($venvExists) {
    Add-Check -Name 'Virtual environment' -Status 'PASS' -Detail $script:VenvPython
} else {
    Add-Check -Name 'Virtual environment' -Status 'FAIL' -Critical $true -Detail ("Missing: {0}" -f $script:VenvPython) `
        -Fix 'Run .\scripts\install.ps1 to create .venv and install requirements.'
}

# 3. Required packages (pip show)
$requiredPackages = @('fastapi', 'uvicorn', 'MetaTrader5', 'pandas', 'SQLAlchemy', 'pydantic', 'python-dotenv', 'websockets', 'httpx')
$missing = [System.Collections.Generic.List[string]]::new()
if (-not $venvExists) {
    Add-Check -Name 'Required packages' -Status 'FAIL' -Critical $true -Detail 'Cannot check packages without a virtual environment.' `
        -Fix 'Run .\scripts\install.ps1 first.'
} else {
    foreach ($package in $requiredPackages) {
        $code = 1
        try {
            $null = & $script:VenvPython -m pip show $package 2>&1
            $code = $LASTEXITCODE
        } catch {
            $code = 1
        }
        if ($code -ne 0) { $missing.Add($package) }
    }
    if ($missing.Count -eq 0) {
        Add-Check -Name 'Required packages' -Status 'PASS' -Detail ("All {0} packages found via pip show." -f $requiredPackages.Count)
    } else {
        Add-Check -Name 'Required packages' -Status 'FAIL' -Critical $true `
            -Detail ("Missing: {0}" -f ($missing -join ', ')) `
            -Fix 'Run .\scripts\install.ps1 (or pip install -r requirements.txt inside .venv).'
    }
}

# 4. MT5 executable discovery
$configuredTerminal = Get-EnvValue -Path $script:EnvFile -Key 'MT5_PATH'
$terminalPath = Find-MT5Terminal -ConfiguredPath $configuredTerminal
$profileFolders = @(Get-MT5ProfileFolders)
if ($terminalPath) {
    Add-Check -Name 'MT5 executable' -Status 'PASS' -Detail ("Found: {0}" -f $terminalPath)
} elseif ($profileFolders.Count -gt 0) {
    Add-Check -Name 'MT5 executable' -Status 'WARN' `
        -Detail ("No terminal64.exe/terminal.exe found, but {0} MetaQuotes Terminal profile folder(s) exist." -f $profileFolders.Count) `
        -Fix 'Install MetaTrader 5 (commonly C:\Program Files\MetaTrader 5\terminal64.exe) and set MT5_PATH in .env. Profile folders alone do not provide a terminal executable.'
} else {
    Add-Check -Name 'MT5 executable' -Status 'WARN' -Detail 'No terminal64.exe or terminal.exe found.' `
        -Fix 'Install MetaTrader 5 and set MT5_PATH in .env to the full executable path.'
}

# 5. Terminal process
$mt5Processes = @(Get-MT5Processes)
$mt5Running = ($mt5Processes.Count -gt 0)
if ($mt5Running) {
    Add-Check -Name 'MT5 terminal process' -Status 'PASS' -Detail ("PID(s): {0}" -f (($mt5Processes.Id) -join ', '))
} else {
    Add-Check -Name 'MT5 terminal process' -Status 'WARN' -Detail 'No terminal64.exe or terminal.exe process is running; no live MT5 connection is possible.' `
        -Fix 'Start the MT5 terminal manually (terminal64.exe), or enable the NazbeenMT5Terminal task via .\scripts\install_autostart.ps1.'
}

# 6. MetaTrader5 Python import (import is not proof of a live connection)
if ($venvExists) {
    $importCode = 1
    $importOutput = ''
    try {
        $importOutput = (& $script:VenvPython -c 'import MetaTrader5' 2>&1 | Out-String).Trim()
        $importCode = $LASTEXITCODE
    } catch {
        $importCode = 1
    }
    if ($importCode -eq 0) {
        Add-Check -Name 'MetaTrader5 import' -Status 'PASS' -Detail 'Python module imports successfully (this does not prove a live MT5 connection).'
    } else {
        Add-Check -Name 'MetaTrader5 import' -Status 'WARN' `
            -Detail ("Import failed (exit {0}): {1}" -f $importCode, $(if ($importOutput) { $importOutput } else { 'no output' })) `
            -Fix 'Run .\scripts\install.ps1 to reinstall MetaTrader5 into .venv; use the same Python bitness as the terminal.'
    }
} else {
    Add-Check -Name 'MetaTrader5 import' -Status 'WARN' -Detail 'Skipped: no virtual environment.' -Fix 'Run .\scripts\install.ps1 first.'
}

# 7. API process and 8. API port
$apiProcesses = @(Get-ApiProcesses)
$listeners = @(Get-PortListeners -Port $effectivePort)
if ($apiProcesses.Count -gt 0) {
    Add-Check -Name ("API process (port {0})" -f $effectivePort) -Status 'PASS' -Detail ("PID(s): {0}" -f (($apiProcesses.ProcessId) -join ', '))
} else {
    Add-Check -Name ("API process (port {0})" -f $effectivePort) -Status 'WARN' -Detail 'No uvicorn app.main:app process found.' `
        -Fix 'Run .\scripts\start_api.ps1 (or install_autostart.ps1 for logon start).'
}
if ($listeners.Count -gt 0) {
    Add-Check -Name ("Port {0} listener" -f $effectivePort) -Status 'PASS' -Detail ("Listening; owner PID(s): {0}" -f (($listeners.OwningProcess) -join ', '))
} else {
    Add-Check -Name ("Port {0} listener" -f $effectivePort) -Status 'WARN' -Detail ("Nothing listening on port {0}." -f $effectivePort) `
        -Fix 'Run .\scripts\start_api.ps1 and check logs\ for startup errors.'
}

# 9. Port 80 and 10. Caddy executable/service/task
$caddyEnabled = Get-ConfigBool -Key 'ENABLE_CADDY' -Default $true
$port80Listening = Test-PortListening -Port 80
if (-not $caddyEnabled) {
    Add-Check -Name 'Port 80 listener' -Status 'PASS' -Detail 'Not required (ENABLE_CADDY=false).'
    Add-Check -Name 'Caddy executable/service/task' -Status 'PASS' -Detail 'Not required (ENABLE_CADDY=false).'
} else {
    if ($port80Listening) {
        Add-Check -Name 'Port 80 listener' -Status 'PASS' -Detail 'Port 80 is listening.'
    } else {
        Add-Check -Name 'Port 80 listener' -Status 'WARN' -Detail 'Port 80 is free; the local domain will not resolve to a running proxy.' `
            -Fix 'Install Caddy and run .\scripts\install.ps1 (or _proxy_autostart.ps1) to start it.'
    }

    $caddyPath = Find-Caddy
    $caddyService = $null
    try { $caddyService = Get-Service -Name 'caddy' -ErrorAction SilentlyContinue } catch { $caddyService = $null }
    $caddyTasks = @(Get-CaddyTasks)
    $caddyProcesses = @(Get-CaddyProcesses)
    $caddyDetails = 'executable={0}; service={1}; proxy_task={2}; process={3}' -f `
        $(if ($caddyPath) { 'yes' } else { 'no' }), `
        $(if ($caddyService) { $caddyService.Status } else { 'none' }), `
        $(if ($caddyTasks.Count -gt 0) { 'present' } else { 'absent' }), `
        $(if ($caddyProcesses.Count -gt 0) { 'running' } else { 'not running' })
    if ($caddyPath -and ($caddyService -or $caddyTasks.Count -gt 0 -or $caddyProcesses.Count -gt 0)) {
        Add-Check -Name 'Caddy executable/service/task' -Status 'PASS' -Detail $caddyDetails
    } else {
        Add-Check -Name 'Caddy executable/service/task' -Status 'WARN' -Detail $caddyDetails `
            -Fix 'Download Caddy, set CADDY_PATH to caddy.exe, then run .\scripts\install.ps1 and install_autostart.ps1.'
    }
}

# 11. Domain resolution and 12. hosts entry
$domainName = Get-Domain
$resolution = Get-DomainResolution -Domain $domainName -Expected '127.0.0.1'
if ($resolution.ExpectedFound) {
    Add-Check -Name 'Domain resolution' -Status 'PASS' -Detail ("{0} -> {1}" -f $domainName, (($resolution.Addresses) -join ', '))
} else {
    Add-Check -Name 'Domain resolution' -Status 'WARN' `
        -Detail ("{0} does not resolve to 127.0.0.1 ({1})." -f $domainName, $(if ($resolution.Addresses.Count -gt 0) { ($resolution.Addresses -join ', ') } else { 'unresolved' })) `
        -Fix 'Run the elevated setup_domain.ps1 command printed by install.ps1.'
}
if (Test-HostsEntry -Domain $domainName) {
    Add-Check -Name 'Hosts entry' -Status 'PASS' -Detail ("127.0.0.1 {0} present." -f $domainName)
} else {
    Add-Check -Name 'Hosts entry' -Status 'WARN' -Detail ("No hosts entry for {0}." -f $domainName) `
        -Fix ('Run elevated: {0}' -f (Get-ElevatedCommand -ScriptName 'setup_domain.ps1' -Arguments ('-Domain "{0}"' -f $domainName)))
}

# 13. HTTP health and 14. docs response
$health = Invoke-ApiHealth -Port $effectivePort -TimeoutSec $TimeoutSec
if (-not $health.Reachable) {
    Add-Check -Name 'HTTP health' -Status 'WARN' -Detail ("Health request failed: {0}" -f $health.Error) `
        -Fix 'Start the API with .\scripts\start_api.ps1 and check logs\ for errors.'
} elseif ($health.Status -eq 'ok' -and $health.TerminalConnected -and $mt5Running) {
    Add-Check -Name 'HTTP health' -Status 'PASS' -Detail $health.Detail
} else {
    $why = if (-not $mt5Running) { 'no terminal process is running' } else { 'the API reports a degraded state' }
    Add-Check -Name 'HTTP health' -Status 'WARN' -Detail ("Reachable but degraded ({0}): {1}" -f $why, $health.Detail) `
        -Fix 'Start MetaTrader 5 and verify MT5_LOGIN/MT5_PASSWORD/MT5_SERVER in .env. Do not treat this as a live MT5 connection.'
}

try {
    $docs = Invoke-WebRequest -Uri ('http://127.0.0.1:{0}/docs' -f $effectivePort) -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
    Add-Check -Name 'Docs endpoint' -Status 'PASS' -Detail ("HTTP {0} from /docs" -f [int]$docs.StatusCode)
} catch {
    Add-Check -Name 'Docs endpoint' -Status 'WARN' -Detail ("Docs request failed: {0}" -f $_.Exception.Message) `
        -Fix 'Confirm the API is running and that /docs is in PUBLIC_ENDPOINTS.'
}

# 15. Firewall note
Add-Check -Name 'Firewall note' -Status 'INFO' `
    -Detail ("API binds {0}:{1} locally; no inbound rule is needed. LAN access requires ALLOW_LAN_ACCESS=true plus an administrator-created firewall rule." -f `
        (Get-ConfigValue -Key 'API_HOST' -Default '127.0.0.1'), $effectivePort)

$criticalFailure = @($results | Where-Object { $_.critical -and $_.status -eq 'FAIL' }).Count -gt 0
$passCount = @($results | Where-Object { $_.status -eq 'PASS' }).Count
$warnCount = @($results | Where-Object { $_.status -eq 'WARN' }).Count
$failCount = @($results | Where-Object { $_.status -eq 'FAIL' }).Count

if ($Json) {
    $payload = [ordered]@{
        generated_at     = (Get-Date).ToString('o')
        project_root     = $script:ProjectRoot
        api_port         = $effectivePort
        summary          = [ordered]@{ pass = $passCount; warn = $warnCount; fail = $failCount; critical_failure = $criticalFailure }
        mt5_process      = $mt5Running
        api_terminal_connected = $health.TerminalConnected
        live_mt5_connection_claimed = ($mt5Running -and $health.TerminalConnected -and $health.Reachable)
        results          = @($results)
    }
    $payload | ConvertTo-Json -Depth 8
    if ($criticalFailure) { exit 1 }
    exit 0
}

Write-Host "`nNazbeen MT5 Market Data API - diagnostics" -ForegroundColor Cyan
foreach ($check in $results) {
    $color = switch ($check.status) {
        'PASS' { 'Green' }
        'WARN' { 'Yellow' }
        'FAIL' { 'Red' }
        default { 'Gray' }
    }
    Write-Host ("[{0,-4}] {1}: {2}" -f $check.status, $check.name, $check.detail) -ForegroundColor $color
    if ($check.fix) { Write-Host ("         -> Fix: {0}" -f $check.fix) -ForegroundColor DarkYellow }
}
Write-Host ("`nSummary: {0} PASS, {1} WARN, {2} FAIL" -f $passCount, $warnCount, $failCount) -ForegroundColor Cyan
if ($criticalFailure) {
    Write-Fail 'Critical failures detected. See the suggested fixes above.'
    exit 1
}
Write-Ok 'No critical failures. Warnings are non-blocking; see suggested fixes above.'
exit 0
