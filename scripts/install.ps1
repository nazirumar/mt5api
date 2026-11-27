<#
.SYNOPSIS
    Main installer for the Nazbeen MT5 Market Data API.

.DESCRIPTION
    Locates Python 3.12+, creates the virtual environment, installs
    requirements with retries, prepares data/logs directories, creates .env
    with a random 48-character API key only when it is missing, discovers the
    MetaTrader 5 executable (terminal64.exe or terminal.exe) and records it in
    MT5_PATH when that value is empty, configures the local domain, optionally
    configures Caddy, installs user-level autostart tasks and runs diagnostics.

    Existing configuration is never overwritten: an existing .env is left
    untouched except for filling an empty MT5_PATH with the discovered path.

.PARAMETER PythonPath
    Explicit path (or command) to use for Python. Example:
    "C:\Users\<name>\AppData\Local\Programs\Python\Python312\python.exe".

.PARAMETER SkipDomain
    Skip setup_domain.ps1 (useful without administrator rights).

.PARAMETER SkipAutostart
    Skip install_autostart.ps1.

.PARAMETER SkipCaddy
    Skip Caddy validation/startup even when it is enabled and discoverable.

.PARAMETER NoDiagnostics
    Skip check_status.ps1 and diagnose.ps1 at the end.

.PARAMETER Domain
    Override the local domain used for hosts setup and the final URL.

.EXAMPLE
    .\scripts\install.ps1

.EXAMPLE
    .\scripts\install.ps1 -PythonPath "C:\Python312\python.exe" -SkipDomain -NoDiagnostics
#>

param(
    [string]$PythonPath = '',
    [switch]$SkipDomain,
    [switch]$SkipAutostart,
    [switch]$SkipCaddy,
    [switch]$NoDiagnostics,
    [string]$Domain = ''
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

# Warnings (for example a missing MT5 terminal) are expected on partial setups;
# only the failures below make the installer exit non-zero.
$installFailed = $false

function Invoke-PreferredPython {
    param(
        [Parameter(Mandatory)][object]$Python,
        [Parameter(Mandatory)][string[]]$Arguments
    )
    $allArguments = @($Python.PrefixArgs) + @($Arguments)
    & $Python.Path @allArguments
}

Write-Step 'Nazbeen MT5 Market Data API - installer'
Write-Info ("Project root: {0}" -f $script:ProjectRoot)

# 1. Python
Write-Step '1/9 Locating Python 3.12+'
$python = Find-Python -Preferred $PythonPath
if ($null -eq $python) {
    Write-Fail 'No usable Python installation was found.'
    Write-Hint 'Install Python 3.12+, add it to PATH, or re-run with -PythonPath "C:\path\to\python.exe".'
    $installFailed = $true
} elseif (-not $python.MeetsMinimum) {
    Write-WarnMsg ("Found Python {0} at {1}; version 3.12+ is recommended." -f $python.Version, $python.Path)
} else {
    Write-Ok ("Python {0} at {1}" -f $python.Version, $python.Path)
}

# 2. Virtual environment
Write-Step '2/9 Preparing virtual environment'
if (Test-Path -LiteralPath $script:VenvPython -PathType Leaf) {
    Write-Ok ("Existing virtual environment reused: {0}" -f (Join-Path $script:ProjectRoot '.venv'))
} elseif ($null -eq $python) {
    Write-WarnMsg 'Skipped: no Python interpreter available.'
} else {
    try {
        Invoke-PreferredPython -Python $python -Arguments @('-m', 'venv', (Join-Path $script:ProjectRoot '.venv'))
        if ($LASTEXITCODE -ne 0) { throw ("python -m venv exited with code {0}" -f $LASTEXITCODE) }
        Write-Ok 'Created .venv.'
    } catch {
        Write-Fail ("Could not create virtual environment: {0}" -f $_.Exception.Message)
        Write-Hint 'Verify the Python installation includes venv, then re-run install.ps1.'
        $installFailed = $true
    }
}

# 3. Requirements (with retries)
Write-Step '3/9 Installing requirements'
if (-not (Test-Path -LiteralPath $script:VenvPython -PathType Leaf)) {
    Write-WarnMsg 'Skipped: virtual environment Python is unavailable.'
} else {
    $requirements = Join-Path $script:ProjectRoot 'requirements.txt'
    if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
        Write-WarnMsg ("Requirements file not found: {0}" -f $requirements)
    } else {
        $installed = $false
        for ($attempt = 1; $attempt -le 3 -and -not $installed; $attempt++) {
            try {
                Write-Info ("pip install attempt {0}/3" -f $attempt)
                & $script:VenvPython -m pip install --disable-pip-version-check -r $requirements
                if ($LASTEXITCODE -ne 0) { throw ("pip exited with code {0}" -f $LASTEXITCODE) }
                $installed = $true
            } catch {
                Write-WarnMsg ("Attempt {0} failed: {1}" -f $attempt, $_.Exception.Message)
                if ($attempt -lt 3) { Start-Sleep -Seconds 3 }
            }
        }
        if ($installed) {
            Write-Ok 'Requirements installed.'
        } else {
            Write-Fail 'Requirements could not be installed after 3 attempts.'
            $installFailed = $true
        }
    }
}

# 4. Data and logs
Write-Step '4/9 Ensuring data and logs directories'
foreach ($directory in @($script:DataDir, $script:LogDir)) {
    if (Test-Path -LiteralPath $directory -PathType Container) {
        Write-Ok ("Directory exists: {0}" -f $directory)
    } else {
        try {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
            Write-Ok ("Created directory: {0}" -f $directory)
        } catch {
            Write-WarnMsg ("Could not create {0}: {1}" -f $directory, $_.Exception.Message)
        }
    }
}

# 5. .env (never touch an existing file except MT5_PATH below)
Write-Step '5/9 Preparing .env'
if (Test-Path -LiteralPath $script:EnvFile -PathType Leaf) {
    Write-Ok '.env already exists; existing configuration was not overwritten.'
    $currentKey = Get-EnvValue -Path $script:EnvFile -Key 'API_KEY'
    if ($currentKey -match '^CHANGE_ME') {
        Write-WarnMsg 'API_KEY still looks like a placeholder. Rotate it manually if you want to use it.'
    }
} else {
    try {
        if (-not (Test-Path -LiteralPath $script:EnvExampleFile -PathType Leaf)) {
            throw ("Template missing: {0}" -f $script:EnvExampleFile)
        }
        Copy-Item -LiteralPath $script:EnvExampleFile -Destination $script:EnvFile
        Set-EnvValue -Path $script:EnvFile -Key 'API_KEY' -Value (New-RandomApiKey -Length 48)
        Write-Ok 'Created .env from .env.example with a new 48-character API_KEY (value hidden).'
    } catch {
        Write-Fail ("Could not create .env: {0}" -f $_.Exception.Message)
        $installFailed = $true
    }
}

# 6. MetaTrader 5 discovery (terminal64.exe AND terminal.exe)
Write-Step '6/9 Discovering MetaTrader 5 terminal'
$configuredTerminal = Get-EnvValue -Path $script:EnvFile -Key 'MT5_PATH'
$terminalPath = Find-MT5Terminal -ConfiguredPath $configuredTerminal
if ($terminalPath) {
    Write-Ok ("Found terminal executable: {0}" -f $terminalPath)
    if ([string]::IsNullOrWhiteSpace($configuredTerminal)) {
        try {
            Set-EnvValue -Path $script:EnvFile -Key 'MT5_PATH' -Value $terminalPath
            Write-Ok 'Wrote the discovered path to MT5_PATH in .env.'
        } catch {
            Write-WarnMsg ("Could not update MT5_PATH in .env: {0}" -f $_.Exception.Message)
        }
    } elseif ($configuredTerminal -ine $terminalPath) {
        Write-WarnMsg ("Keeping your MT5_PATH setting: {0}" -f $configuredTerminal)
    }
} else {
    Write-WarnMsg 'No terminal64.exe or terminal.exe was found in standard paths, registry or MetaQuotes profiles.'
    Write-Hint 'Install MT5 (usually C:\Program Files\MetaTrader 5\terminal64.exe) or set MT5_PATH in .env.'
}
if (Test-MT5ProcessRunning) {
    Write-Ok 'A MetaTrader terminal process is running (process presence only; connection is not verified here).'
} else {
    Write-WarnMsg 'No MetaTrader terminal process is running; no live MT5 connection is claimed.'
}

# 7. Local domain
$domainName = if ([string]::IsNullOrWhiteSpace($Domain)) { Get-Domain } else { $Domain.Trim() }
if ($SkipDomain) {
    Write-Info '7/9 Skipped domain setup (-SkipDomain).'
} else {
    Write-Step '7/9 Configuring local domain'
    if (Test-IsAdmin) {
        & (Join-Path $script:ScriptsDir 'setup_domain.ps1') -Domain $domainName
    } else {
        Write-WarnMsg 'Administrator rights are required to edit the hosts file.'
        $arguments = '-Domain "{0}"' -f $domainName
        Write-Info 'Run this exact command in an elevated PowerShell window:'
        Write-Host ("    {0}" -f (Get-ElevatedCommand -ScriptName 'setup_domain.ps1' -Arguments $arguments)) -ForegroundColor Yellow
    }
}

# The shipped Caddyfile matches the default domain. Regenerate it from the
# resolved domain so -Domain/LOCAL_DOMAIN also controls the proxy route.
$caddyContent = (@(
    '# Local-only HTTP configuration. No public certificate is requested.'
    '{'
    "`tauto_https off"
    "`tadmin off"
    '}'
    ''
    ('http://{0} {{' -f $domainName)
    "`tbind 127.0.0.1"
    "`treverse_proxy 127.0.0.1:8080"
    '}'
    ''
) -join "`n")
$caddyDir = Split-Path -Parent $script:Caddyfile
if (-not (Test-Path -LiteralPath $caddyDir -PathType Container)) {
    New-Item -ItemType Directory -Path $caddyDir -Force | Out-Null
}
if (-not (Test-Path -LiteralPath $script:Caddyfile -PathType Leaf) -or
    [System.IO.File]::ReadAllText($script:Caddyfile) -cne $caddyContent) {
    [System.IO.File]::WriteAllText($script:Caddyfile, $caddyContent, (New-Object System.Text.UTF8Encoding($false)))
    Write-Ok ("Generated Caddyfile for http://{0}." -f $domainName)
}

# CORS_ORIGINS is a separate allow-list; the app does not derive it from
# LOCAL_DOMAIN. Point this out for a custom hostname without editing .env.
if ($domainName -ine $script:DefaultDomain) {
    $corsOrigins = Get-ConfigValue -Key 'CORS_ORIGINS'
    if ($corsOrigins -notmatch [regex]::Escape(('http://{0}' -f $domainName))) {
        Write-WarnMsg ("Browser clients on http://{0} also need CORS_ORIGINS to include that origin." -f $domainName)
        Write-Hint 'Add it to CORS_ORIGINS in .env and restart the API.'
    }
}

# 8. Caddy (only when enabled and discoverable)
$caddyEnabled = Get-ConfigBool -Key 'ENABLE_CADDY' -Default $true
$caddyPath = $null
if ($SkipCaddy) {
    Write-Info '8/9 Skipped Caddy configuration (-SkipCaddy).'
} elseif (-not $caddyEnabled) {
    Write-Info '8/9 ENABLE_CADDY=false; skipping Caddy.'
} else {
    Write-Step '8/9 Configuring Caddy'
    $caddyPath = Find-Caddy
    if ([string]::IsNullOrWhiteSpace($caddyPath)) {
        Write-WarnMsg 'Caddy executable not found; skipping configuration.'
        Write-Hint 'Install Caddy or set CADDY_PATH to caddy.exe, then re-run install.ps1.'
    } else {
        Write-Ok ("Caddy executable: {0}" -f $caddyPath)
        try {
            $validation = (& $caddyPath validate --config $script:Caddyfile 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -eq 0) {
                Write-Ok 'Caddyfile validated.'
            } else {
                Write-WarnMsg ("Caddyfile validation failed: {0}" -f $validation)
            }
        } catch {
            Write-WarnMsg ("Caddyfile validation could not run: {0}" -f $_.Exception.Message)
        }
        if (@(Get-CaddyProcesses).Count -eq 0 -and -not (Test-PortListening -Port 80)) {
            try {
                $arguments = @('run', '--config', ('"{0}"' -f $script:Caddyfile))
                $caddyProcess = Start-Process -FilePath $caddyPath -ArgumentList $arguments `
                    -WorkingDirectory (Split-Path -Parent $script:Caddyfile) `
                    -WindowStyle Hidden -PassThru
                Write-Ok ("Started Caddy (PID {0})." -f $caddyProcess.Id)
            } catch {
                Write-WarnMsg ("Could not start Caddy: {0}" -f $_.Exception.Message)
            }
        } else {
            Write-Ok 'Caddy is already running or port 80 is in use; no duplicate started.'
        }
    }
}

# 9. Autostart tasks
if ($SkipAutostart) {
    Write-Info '9/9 Skipped autostart task installation (-SkipAutostart).'
} else {
    Write-Step '9/9 Installing autostart tasks'
    try {
        & (Join-Path $script:ScriptsDir 'install_autostart.ps1')
        if ($LASTEXITCODE -ne 0) {
            Write-WarnMsg 'Some autostart tasks could not be registered.'
            $installFailed = $true
        }
    } catch {
        Write-WarnMsg ("Autostart installation failed: {0}" -f $_.Exception.Message)
        $installFailed = $true
    }
}

# Diagnostics
if (-not $NoDiagnostics) {
    Write-Step 'Running check_status.ps1'
    try { & (Join-Path $script:ScriptsDir 'check_status.ps1') | Out-Host }
    catch { Write-WarnMsg ("Status check failed: {0}" -f $_.Exception.Message) }

    Write-Step 'Running diagnose.ps1'
    try {
        & (Join-Path $script:ScriptsDir 'diagnose.ps1') | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-WarnMsg 'Diagnostics reported critical failures; review the suggestions above.'
            $installFailed = $true
        }
    } catch {
        Write-WarnMsg ("Diagnostics failed: {0}" -f $_.Exception.Message)
        $installFailed = $true
    }
}

# Final URLs
$apiPort = Get-ApiPort
Write-Step 'Installation finished'
Write-KV 'API URL' ("http://127.0.0.1:{0}" -f $apiPort)
Write-KV 'Docs URL' ("http://127.0.0.1:{0}/docs" -f $apiPort)
Write-KV 'Health URL' ("http://127.0.0.1:{0}/api/v1/health" -f $apiPort)
Write-KV 'Local domain URL' ("http://{0}" -f $domainName)
Write-KV 'MT5 executable' $(if ($terminalPath) { $terminalPath } else { 'not found - set MT5_PATH in .env' })
Write-KV 'MT5 live connection' $(if (Test-MT5ProcessRunning) { 'not verified - check /api/v1/health' } else { 'no - terminal not running' })
Write-Info 'The API key is stored in .env. Never commit or share it.'
if ($caddyEnabled -and [string]::IsNullOrWhiteSpace($caddyPath)) {
    Write-Hint 'Domain URL requires Caddy; install it and set CADDY_PATH to enable the proxy.'
}
if ($installFailed) { Write-WarnMsg 'Installation finished with one or more failures; review the messages above.' }
if ($installFailed) { exit 1 }
exit 0
