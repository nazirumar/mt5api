<#
.SYNOPSIS
    Internal helper library for the Nazbeen MT5 Market Data API scripts.

.DESCRIPTION
    Dot-sourced by the other scripts in this directory. Supplies shared path,
    .env, Python/MT5/Caddy discovery, process, port, DNS/hosts and HTTP health
    helpers. Not intended to be run directly.

.NOTES
    All paths are resolved relative to the project root (the parent of the
    scripts directory), so scripts work even when the path contains spaces.
#>

$ErrorActionPreference = 'Stop'

$script:ScriptsDir     = $PSScriptRoot
$script:ProjectRoot    = Split-Path -Parent $script:ScriptsDir
$script:EnvFile        = Join-Path $script:ProjectRoot '.env'
$script:EnvExampleFile = Join-Path $script:ProjectRoot '.env.example'
$script:VenvPython     = Join-Path $script:ProjectRoot '.venv\Scripts\python.exe'
$script:Caddyfile      = Join-Path $script:ProjectRoot 'caddy\Caddyfile'
$script:LogDir         = Join-Path $script:ProjectRoot 'logs'
$script:DataDir        = Join-Path $script:ProjectRoot 'data'
$script:ScriptsLog     = Join-Path $script:LogDir 'autostart.log'
$script:DefaultDomain  = 'mt5.nazbeen.test'

# MetaTrader 5 on 64-bit Windows installs terminal64.exe; older/32-bit
# installations use terminal.exe. Every discovery and process check handles both.
$script:TerminalExeNames = @('terminal64.exe', 'terminal.exe')
$script:TerminalProcNames = @('terminal64', 'terminal')

#region Console output helpers

function Write-Step {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host ("`n==> {0}" -f $Message) -ForegroundColor Cyan
}

function Write-Info {
    param([string]$Message)
    Write-Host ("    {0}" -f $Message) -ForegroundColor Gray
}

function Write-Ok {
    param([string]$Message)
    Write-Host ("  [OK]   {0}" -f $Message) -ForegroundColor Green
}

function Write-WarnMsg {
    param([string]$Message)
    Write-Host ("  [WARN] {0}" -f $Message) -ForegroundColor Yellow
}

function Write-Fail {
    param([string]$Message)
    Write-Host ("  [FAIL] {0}" -f $Message) -ForegroundColor Red
}

function Write-Hint {
    param([string]$Message)
    Write-Host ("         -> {0}" -f $Message) -ForegroundColor DarkYellow
}

function Write-KV {
    param([string]$Key, $Value)
    Write-Host ("  {0,-30} {1}" -f $Key, $Value) -ForegroundColor White
}

#endregion

#region Path helpers

function Get-ProjectRoot { return $script:ProjectRoot }
function Get-ScriptsDir { return $script:ScriptsDir }
function Get-VenvPythonPath { return $script:VenvPython }
function Get-EnvFilePath { return $script:EnvFile }
function Get-CaddyfilePath { return $script:Caddyfile }
function Get-DefaultDomain { return $script:DefaultDomain }

function Get-ElevatedCommand {
    param(
        [Parameter(Mandatory)][string]$ScriptName,
        [string]$Arguments = ''
    )
    $path = Join-Path $script:ScriptsDir $ScriptName
    $command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $path
    if (-not [string]::IsNullOrWhiteSpace($Arguments)) { $command += ' ' + $Arguments }
    return $command
}

function Write-AutostartLog {
    param([Parameter(Mandatory)][string]$Message)
    try {
        if (-not (Test-Path -LiteralPath $script:LogDir -PathType Container)) {
            New-Item -ItemType Directory -Path $script:LogDir -Force | Out-Null
        }
        $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
        Add-Content -LiteralPath $script:ScriptsLog -Value $line -Encoding UTF8
    } catch {
        # Logging must never break an autostart task.
    }
}

#endregion

#region .env helpers (never print secrets)

function Read-EnvFile {
    param([Parameter(Mandatory)][string]$Path)
    $values = [ordered]@{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $values }
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        if ($line -match '^\s*(#|;|\[)') { continue }
        if ($line -match '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $key = $Matches[1]
            $value = $Matches[2]
            if ($value.Length -ge 2 -and
                (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                 ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            $values[$key] = $value
        }
    }
    return $values
}

function Get-EnvValue {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Key,
        [string]$Default = ''
    )
    $values = Read-EnvFile -Path $Path
    if ($values.Contains($Key) -and -not [string]::IsNullOrWhiteSpace([string]$values[$Key])) {
        return [string]$values[$Key]
    }
    return $Default
}

function Get-ConfigValue {
    param(
        [Parameter(Mandatory)][string]$Key,
        [string]$Default = ''
    )
    # Mirrors app.config: real process environment wins over .env.
    $processValue = [Environment]::GetEnvironmentVariable($Key, 'Process')
    if (-not [string]::IsNullOrWhiteSpace($processValue)) { return $processValue }
    $fromEnv = Get-EnvValue -Path $script:EnvFile -Key $Key
    if (-not [string]::IsNullOrWhiteSpace($fromEnv)) { return $fromEnv }
    $fromExample = Get-EnvValue -Path $script:EnvExampleFile -Key $Key
    if (-not [string]::IsNullOrWhiteSpace($fromExample)) { return $fromExample }
    return $Default
}

function Get-ConfigBool {
    param(
        [Parameter(Mandatory)][string]$Key,
        [bool]$Default = $false
    )
    $value = (Get-ConfigValue -Key $Key).Trim()
    if ($value -match '^(?i:1|true|yes|on)$') { return $true }
    if ($value -match '^(?i:0|false|no|off)$') { return $false }
    return $Default
}

function Get-ConfigInt {
    param(
        [Parameter(Mandatory)][string]$Key,
        [int]$Default = 0
    )
    $value = (Get-ConfigValue -Key $Key).Trim()
    $parsed = 0
    if ([int]::TryParse($value, [ref]$parsed)) { return $parsed }
    return $Default
}

function Set-EnvValue {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Value
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Cannot update missing file: $Path"
    }
    $text = [System.IO.File]::ReadAllText($Path)
    $newline = if ($text -match "`r`n") { "`r`n" } else { "`n" }
    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($line in ($text -split "`r?`n")) { $lines.Add($line) }
    while ($lines.Count -gt 0 -and [string]::IsNullOrWhiteSpace($lines[$lines.Count - 1])) {
        $lines.RemoveAt($lines.Count - 1)
    }

    $pattern = '^\s*(?:export\s+)?' + [regex]::Escape($Key) + '\s*='
    $replaced = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $pattern) {
            $lines[$i] = '{0}={1}' -f $Key, $Value
            $replaced = $true
            break
        }
    }
    if (-not $replaced) { $lines.Add('{0}={1}' -f $Key, $Value) }

    $content = ($lines -join $newline) + $newline
    [System.IO.File]::WriteAllText($Path, $content, (New-Object System.Text.UTF8Encoding($false)))
}

function New-RandomApiKey {
    param([int]$Length = 48)
    $alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $bytes = New-Object byte[] $Length
        $rng.GetBytes($bytes)
        $builder = New-Object System.Text.StringBuilder
        foreach ($byte in $bytes) { [void]$builder.Append($alphabet[$byte % $alphabet.Length]) }
        return $builder.ToString()
    } finally {
        $rng.Dispose()
    }
}

function Get-ApiPort { return (Get-ConfigInt -Key 'API_PORT' -Default 8080) }
function Get-Domain { return (Get-ConfigValue -Key 'LOCAL_DOMAIN' -Default $script:DefaultDomain) }

#endregion

#region Privilege helpers

function Test-IsAdmin {
    $principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

#endregion

#region Python discovery

function Get-PythonVersion {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$PrefixArgs = @()
    )
    try {
        $arguments = @($PrefixArgs) + @('-c', "import sys; print('%d.%d.%d' % sys.version_info[:3])")
        $output = & $Exe @arguments 2>&1
        if ($LASTEXITCODE -ne 0) { return $null }
        $text = ($output | Out-String).Trim()
        if ($text -match '(\d+)\.(\d+)\.(\d+)') {
            return [version]('{0}.{1}.{2}' -f $Matches[1], $Matches[2], $Matches[3])
        }
    } catch {
        return $null
    }
    return $null
}

function Find-Python {
    param([string]$Preferred = '')

    $candidates = [System.Collections.Generic.List[object]]::new()
    if (-not [string]::IsNullOrWhiteSpace($Preferred)) {
        $candidates.Add([pscustomobject]@{ Path = $Preferred; PrefixArgs = @() })
    }
    $candidates.Add([pscustomobject]@{ Path = 'python'; PrefixArgs = @() })
    $candidates.Add([pscustomobject]@{ Path = 'py'; PrefixArgs = @('-3.12') })
    $candidates.Add([pscustomobject]@{ Path = 'py'; PrefixArgs = @('-3') })

    $roots = @($env:LOCALAPPDATA, $env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:ProgramData)
    foreach ($root in $roots) {
        if ([string]::IsNullOrWhiteSpace($root)) { continue }
        foreach ($name in @('Python313', 'Python312', 'Python311')) {
            $candidates.Add([pscustomobject]@{ Path = (Join-Path $root "Programs\$name\python.exe"); PrefixArgs = @() })
            $candidates.Add([pscustomobject]@{ Path = (Join-Path $root "$name\python.exe"); PrefixArgs = @() })
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        # WindowsApps Python 3.13 stub/shim: last resort, still checked.
        $candidates.Add([pscustomobject]@{ Path = (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\python.exe'); PrefixArgs = @() })
    }

    $seen = @{}
    $fallback = $null
    foreach ($candidate in $candidates) {
        $path = $candidate.Path
        if ([string]::IsNullOrWhiteSpace($path)) { continue }
        $resolved = $path
        $prefix = @($candidate.PrefixArgs)
        if (-not (Test-Path -LiteralPath $path)) {
            $command = Get-Command -Name $path -CommandType Application -ErrorAction SilentlyContinue
            if ($null -eq $command) { continue }
            $resolved = $command.Source
        }
        $key = $resolved.ToLowerInvariant() + '|' + ($prefix -join ',')
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true

        $version = Get-PythonVersion -Exe $resolved -PrefixArgs $prefix
        $item = [pscustomobject]@{
            Path         = $resolved
            PrefixArgs   = $prefix
            Version      = $version
            MeetsMinimum = ($null -ne $version -and $version -ge [version]'3.12.0')
        }
        if ($item.MeetsMinimum) { return $item }
        if ($null -eq $fallback -and $null -ne $version) { $fallback = $item }
    }
    return $fallback
}

#endregion

#region MetaTrader 5 discovery (terminal64.exe AND terminal.exe)

function Get-MT5ProfileFolders {
    if ([string]::IsNullOrWhiteSpace($env:APPDATA)) { return @() }
    $root = Join-Path $env:APPDATA 'MetaQuotes\Terminal'
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { return @() }
    try {
        return @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction Stop | ForEach-Object { $_.FullName })
    } catch {
        return @()
    }
}

function Find-MT5Terminal {
    param([string]$ConfiguredPath = '')

    $directories = [System.Collections.Generic.List[string]]::new()
    $addDirectory = {
        param($Directory)
        if (-not [string]::IsNullOrWhiteSpace($Directory) -and
            (Test-Path -LiteralPath $Directory -PathType Container)) {
            $directories.Add($Directory)
        }
    }

    # Configured path may be the executable, a directory, or even terminal.ico
    # from a registry DisplayIcon. In all cases we inspect its directory.
    foreach ($raw in @($ConfiguredPath, $env:MT5_PATH)) {
        if ([string]::IsNullOrWhiteSpace($raw)) { continue }
        $path = $raw.Trim().Trim('"').Trim("'")
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $name = [System.IO.Path]::GetFileName($path)
            if ($script:TerminalExeNames -contains $name) {
                return (Resolve-Path -LiteralPath $path).Path
            }
            & $addDirectory (Split-Path -Parent $path)
        } elseif (Test-Path -LiteralPath $path -PathType Container) {
            & $addDirectory $path
        }
    }

    $fixedRoots = @()
    if ($env:ProgramFiles) { $fixedRoots += (Join-Path $env:ProgramFiles 'MetaTrader 5') }
    if (${env:ProgramFiles(x86)}) { $fixedRoots += (Join-Path ${env:ProgramFiles(x86)} 'MetaTrader 5') }
    if ($env:LOCALAPPDATA) {
        $fixedRoots += (Join-Path $env:LOCALAPPDATA 'Programs\MetaTrader 5')
        $fixedRoots += (Join-Path $env:LOCALAPPDATA 'MetaTrader 5')
    }
    foreach ($root in $fixedRoots) { & $addDirectory $root }
    foreach ($profile in (Get-MT5ProfileFolders)) { & $addDirectory $profile }

    $registryRoots = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    foreach ($registryRoot in $registryRoots) {
        foreach ($item in (Get-ItemProperty -Path $registryRoot -ErrorAction SilentlyContinue)) {
            if ($null -eq $item -or $item.DisplayName -notmatch 'MetaTrader|MetaQuotes') { continue }
            if ($item.InstallLocation) { & $addDirectory ([string]$item.InstallLocation) }
            if ($item.DisplayIcon) {
                # DisplayIcon often points at terminal.ico, e.g.
                # "C:\Program Files\MetaTrader 5\terminal.ico",0 -- use its directory.
                $icon = [string]$item.DisplayIcon
                $icon = $icon -replace '^\s*"', ''
                $icon = $icon -replace '"\s*,\s*\d+\s*$', ''
                $icon = $icon -replace '\s*,\s*\d+\s*$', ''
                $icon = $icon -replace '"\s*$', ''
                if (-not [string]::IsNullOrWhiteSpace($icon)) { & $addDirectory (Split-Path -Parent $icon) }
            }
        }
    }

    $checked = @{}
    foreach ($directory in $directories) {
        $normalized = $directory.TrimEnd('\').ToLowerInvariant()
        if ($checked.ContainsKey($normalized)) { continue }
        $checked[$normalized] = $true
        foreach ($exeName in $script:TerminalExeNames) {
            $candidate = Join-Path $directory $exeName
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                return (Resolve-Path -LiteralPath $candidate).Path
            }
        }
    }

    # Last resort: shallow recursive search in the two usual installation areas.
    $searchRoots = @($fixedRoots)
    if (-not [string]::IsNullOrWhiteSpace($env:APPDATA)) {
        $searchRoots += (Join-Path $env:APPDATA 'MetaQuotes\Terminal')
    }
    foreach ($searchRoot in $searchRoots | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $searchRoot -PathType Container)) { continue }
        try {
            $found = Get-ChildItem -LiteralPath $searchRoot -File -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $script:TerminalExeNames -contains $_.Name } |
                Select-Object -First 1
            if ($found) { return $found.FullName }
        } catch {
            # Access denied in a profile folder is non-fatal.
        }
    }
    return $null
}

function Get-MT5Processes {
    try {
        return @(Get-Process -Name $script:TerminalProcNames -ErrorAction SilentlyContinue)
    } catch {
        return @()
    }
}

function Test-MT5ProcessRunning {
    return (@(Get-MT5Processes).Count -gt 0)
}

#endregion

#region Caddy discovery

function Find-Caddy {
    $paths = [System.Collections.Generic.List[string]]::new()
    $configured = Get-ConfigValue -Key 'CADDY_PATH'
    if (-not [string]::IsNullOrWhiteSpace($configured)) { $paths.Add($configured) }
    if (-not [string]::IsNullOrWhiteSpace($env:CADDY_PATH)) { $paths.Add($env:CADDY_PATH) }
    $paths.Add((Join-Path $script:ProjectRoot 'tools\caddy.exe'))
    $command = Get-Command -Name 'caddy' -CommandType Application -ErrorAction SilentlyContinue
    if ($command) { $paths.Add($command.Source) }
    try {
        foreach ($line in (& where.exe caddy 2>$null)) {
            if (-not [string]::IsNullOrWhiteSpace($line)) { $paths.Add($line.Trim()) }
        }
    } catch {
        # where.exe returns 1 when nothing is found; that is expected.
    }
    if ($env:ProgramFiles) { $paths.Add((Join-Path $env:ProgramFiles 'Caddy\caddy.exe')) }
    if (${env:ProgramFiles(x86)}) { $paths.Add((Join-Path ${env:ProgramFiles(x86)} 'Caddy\caddy.exe')) }
    if ($env:LOCALAPPDATA) {
        $paths.Add((Join-Path $env:LOCALAPPDATA 'Caddy\caddy.exe'))
        $paths.Add((Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\caddy.exe'))
        $paths.Add((Join-Path $env:LOCALAPPDATA 'Scoop\shims\caddy.exe'))
    }
    if ($env:ProgramData) { $paths.Add((Join-Path $env:ProgramData 'chocolatey\bin\caddy.exe')) }

    foreach ($path in $paths) {
        if (-not [string]::IsNullOrWhiteSpace($path) -and (Test-Path -LiteralPath $path -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $path).Path
        }
    }
    return $null
}

function Get-CaddyTasks {
    try {
        $output = & schtasks.exe /Query /TN 'NazbeenMT5Proxy' /FO CSV /NH 2>&1
        if ($LASTEXITCODE -eq 0) { return @($output | Out-String) }
    } catch {
        # Query is best-effort.
    }
    return @()
}

#endregion

#region Process and port helpers

function Get-PortListeners {
    param([Parameter(Mandatory)][int]$Port)
    try {
        return @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
    } catch {
        return @()
    }
}

function Test-PortListening {
    param([Parameter(Mandatory)][int]$Port)
    # Functions enumerate arrays on return, so re-wrap before counting. A bare
    # CimInstance can have a null Count property even when the port listens.
    return (@(Get-PortListeners -Port $Port).Count -gt 0)
}

function Get-ApiProcesses {
    try {
        $filter = "Name = 'python.exe' OR Name = 'pythonw.exe'"
        return @(Get-CimInstance -ClassName Win32_Process -Filter $filter -ErrorAction Stop |
            Where-Object {
                $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'app\.main:app'
            })
    } catch {
        return @()
    }
}

function Get-CaddyProcesses {
    try {
        return @(Get-Process -Name 'caddy' -ErrorAction SilentlyContinue)
    } catch {
        return @()
    }
}

function Invoke-ApiHealth {
    param(
        [int]$Port = 0,
        [int]$TimeoutSec = 3
    )
    $effectivePort = if ($Port -gt 0) { $Port } else { Get-ApiPort }
    $key = Get-ConfigValue -Key 'API_KEY'
    $headerName = Get-ConfigValue -Key 'API_KEY_HEADER' -Default 'X-API-Key'

    $result = [pscustomobject]@{
        Reachable        = $false
        HttpStatus       = $null
        Status           = 'unknown'
        TerminalConnected = $false
        DataFreshness    = 'unknown'
        Detail           = ''
        Error            = $null
    }

    try {
        $headers = @{}
        if (-not [string]::IsNullOrWhiteSpace($key) -and $key -notmatch '^CHANGE_ME') {
            $headers[$headerName] = $key
        }
        $uri = 'http://127.0.0.1:{0}/api/v1/health' -f $effectivePort
        $response = Invoke-WebRequest -Uri $uri -Headers $headers -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
        $result.Reachable = $true
        $result.HttpStatus = [int]$response.StatusCode
        $payload = $response.Content | ConvertFrom-Json
        $data = if ($payload.PSObject.Properties.Name -contains 'data') { $payload.data } else { $payload }
        if ($null -ne $data) {
            $result.Status = [string]$data.status
            $result.TerminalConnected = [bool]$data.terminal_connected
            $result.DataFreshness = [string]$data.data_freshness
            $result.Detail = 'status={0}; terminal_connected={1}; freshness={2}' -f `
                $data.status, $data.terminal_connected, $data.data_freshness
        }
    } catch {
        $message = $_.Exception.Message
        if (-not [string]::IsNullOrWhiteSpace($key)) { $message = $message.Replace($key, '***REDACTED***') }
        $result.Error = $message
    }
    return $result
}

function Get-RecentLogErrors {
    param([int]$Max = 5, [int]$MaxLength = 200)
    $items = [System.Collections.Generic.List[string]]::new()
    if (-not (Test-Path -LiteralPath $script:LogDir -PathType Container)) { return @() }
    $key = Get-ConfigValue -Key 'API_KEY'
    try {
        $files = @(Get-ChildItem -LiteralPath $script:LogDir -File -Filter '*.log' -ErrorAction Stop |
            Sort-Object LastWriteTime -Descending)
        foreach ($file in $files) {
            $lines = @(Get-Content -LiteralPath $file.FullName -Tail 200 -ErrorAction SilentlyContinue)
            for ($i = $lines.Count - 1; $i -ge 0 -and $items.Count -lt $Max; $i--) {
                $line = [string]$lines[$i]
                if ($line -match 'ERROR|CRITICAL|Traceback|Exception') {
                    if ($line.Length -gt $MaxLength) { $line = $line.Substring(0, $MaxLength) + '...' }
                    if (-not [string]::IsNullOrWhiteSpace($key)) { $line = $line.Replace($key, '***REDACTED***') }
                    $items.Add(('{0}: {1}' -f $file.Name, $line))
                }
            }
            if ($items.Count -ge $Max) { break }
        }
    } catch {
        # Unreadable logs are reported as "none" rather than failing a check.
    }
    return @($items)
}

#endregion

#region DNS / hosts helpers

function Test-HostsEntry {
    param([Parameter(Mandatory)][string]$Domain)
    $hostsPath = Join-Path $env:windir 'System32\drivers\etc\hosts'
    if (-not (Test-Path -LiteralPath $hostsPath -PathType Leaf)) { return $false }
    $pattern = '(?i)(?:^|\s)' + [regex]::Escape($Domain) + '(?:\s|$)'
    foreach ($line in [System.IO.File]::ReadAllLines($hostsPath)) {
        if ($line -match '^\s*#') { continue }
        $body = $line
        $hashIndex = $body.IndexOf('#')
        if ($hashIndex -ge 0) { $body = $body.Substring(0, $hashIndex) }
        if ($body -match $pattern) { return $true }
    }
    return $false
}

function Get-DomainResolution {
    param(
        [Parameter(Mandatory)][string]$Domain,
        [string]$Expected = '127.0.0.1'
    )
    try {
        $records = @(Resolve-DnsName -Name $Domain -Type A -ErrorAction Stop | Where-Object { $_.IPAddress })
        $addresses = @($records | ForEach-Object { [string]$_.IPAddress })
        return [pscustomobject]@{
            Resolved      = ($addresses.Count -gt 0)
            Addresses     = $addresses
            ExpectedFound = (@($addresses | Where-Object { $_ -eq $Expected }).Count -gt 0)
        }
    } catch {
        return [pscustomobject]@{ Resolved = $false; Addresses = @(); ExpectedFound = $false }
    }
}

function Set-HostsDomainMapping {
    param(
        [Parameter(Mandatory)][string]$Domain,
        [switch]$Add
    )
    $hostsPath = Join-Path $env:windir 'System32\drivers\etc\hosts'
    if (-not (Test-Path -LiteralPath $hostsPath -PathType Leaf)) {
        throw "Hosts file not found: $hostsPath"
    }
    $text = [System.IO.File]::ReadAllText($hostsPath)
    $newline = if ($text -match "`r`n") { "`r`n" } else { "`n" }
    $lines = @($text -split "`r?`n")
    $output = [System.Collections.Generic.List[string]]::new()
    $removed = 0
    $previousIPs = [System.Collections.Generic.List[string]]::new()

    foreach ($line in $lines) {
        $hashIndex = $line.IndexOf('#')
        $comment = if ($hashIndex -ge 0) { $line.Substring($hashIndex) } else { '' }
        $body = if ($hashIndex -ge 0) { $line.Substring(0, $hashIndex) } else { $line }

        if ($body -match '^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+(.+?)\s*$') {
            $ip = $Matches[1]
            $tokens = @($Matches[2] -split '\s+' | Where-Object { $_ -ne '' })
            $matchingTokens = @($tokens | Where-Object { $_ -ieq $Domain })
            if ($matchingTokens.Count -gt 0) {
                $removed += $matchingTokens.Count
                if (-not $previousIPs.Contains($ip)) { $previousIPs.Add($ip) }
            }
            $kept = @($tokens | Where-Object { $_ -ine $Domain })
            if ($kept.Count -eq 0) {
                # Remove only the host token(s); keep any trailing comment text.
                if ($comment) { $output.Add($comment) }
                continue
            }
            $newLine = '{0} {1}' -f $ip, ($kept -join ' ')
            if ($comment) { $newLine = $newLine + ' ' + $comment }
            $output.Add($newLine)
            continue
        }
        $output.Add($line)
    }

    if ($Add) {
        while ($output.Count -gt 0 -and [string]::IsNullOrWhiteSpace($output[$output.Count - 1])) {
            $output.RemoveAt($output.Count - 1)
        }
        $output.Add('127.0.0.1 {0}' -f $Domain)
    }

    $newText = ($output -join $newline) + $newline
    return [pscustomobject]@{
        Path         = $hostsPath
        NewText      = $newText
        Changed      = ($newText -cne $text)
        RemovedCount = $removed
        PreviousIPs  = @($previousIPs)
    }
}

function Write-HostsFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Content
    )
    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ('hosts.nazbeen.' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [System.IO.File]::WriteAllText($temp, $Content, (New-Object System.Text.UTF8Encoding($false)))
        $acl = Get-Acl -LiteralPath $Path
        Copy-Item -LiteralPath $temp -Destination $Path -Force
        Set-Acl -LiteralPath $Path -AclObject $acl
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
    }
}

function Invoke-DnsFlush {
    try {
        $output = & ipconfig /flushdns 2>&1
        if ($output) { Write-Info (($output | Out-String).Trim()) }
        return $true
    } catch {
        Write-WarnMsg 'Could not flush DNS cache. It will refresh on its own.'
        return $false
    }
}

#endregion
