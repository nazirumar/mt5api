<#
.SYNOPSIS
    One-command bootstrap for a fresh clone of the MT5 Market Data API.

.DESCRIPTION
    Wraps install.ps1 with conservative defaults, then optionally runs the
    test suite and prints the authenticated readiness endpoint. It never
    overwrites an existing .env; the installer generates a random API key only
    when .env is absent. Domain/hosts setup still requires administrator rights.

.PARAMETER PythonPath
    Explicit Python 3.12+ executable. Auto-detected when omitted.

.PARAMETER SkipDomain
    Skip hosts-file setup (useful in an unelevated shell).

.PARAMETER SkipAutostart
    Skip Task Scheduler task registration.

.PARAMETER SkipCaddy
    Skip Caddy validation/startup.

.PARAMETER NoDiagnostics
    Skip the installer's status/diagnostic checks.

.PARAMETER Domain
    Override the local domain used for hosts setup.

.PARAMETER RunTests
    Run pytest after the installer finishes.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 -SkipDomain -RunTests
#>

param(
    [string]$PythonPath = '',
    [switch]$SkipDomain,
    [switch]$SkipAutostart,
    [switch]$SkipCaddy,
    [switch]$NoDiagnostics,
    [string]$Domain = '',
    [switch]$RunTests
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

Write-Step 'Fresh-clone bootstrap'
Write-Info ("Project root: {0}" -f $script:ProjectRoot)

if (Test-Path -LiteralPath (Join-Path $script:ProjectRoot '.git') -PathType Container) {
    Write-Ok 'Git checkout detected.'
} else {
    Write-Info 'No .git directory; continuing with the source tree as-is.'
}

$installer = Join-Path $script:ScriptsDir 'install.ps1'
$installArgs = [System.Collections.Generic.List[string]]::new()
if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
    $installArgs.Add('-PythonPath')
    $installArgs.Add(('"{0}"' -f $PythonPath))
}
if ($SkipDomain) { $installArgs.Add('-SkipDomain') }
if ($SkipAutostart) { $installArgs.Add('-SkipAutostart') }
if ($SkipCaddy) { $installArgs.Add('-SkipCaddy') }
if ($NoDiagnostics) { $installArgs.Add('-NoDiagnostics') }
if (-not [string]::IsNullOrWhiteSpace($Domain)) {
    $installArgs.Add('-Domain')
    $installArgs.Add(('"{0}"' -f $Domain))
}

& $installer @installArgs
$installCode = $LASTEXITCODE
if ($installCode -ne 0) {
    Write-WarnMsg ("Installer finished with code {0}." -f $installCode)
}

if ($RunTests) {
    Write-Step 'Running pytest'
    if (-not (Test-Path -LiteralPath $script:VenvPython -PathType Leaf)) {
        Write-WarnMsg 'Skipped tests: virtual environment Python is unavailable.'
    } else {
        & $script:VenvPython -m pytest -q
        $testCode = $LASTEXITCODE
        if ($testCode -eq 0) {
            Write-Ok 'All tests passed.'
        } else {
            Write-Fail ("Tests failed with code {0}." -f $testCode)
            $installCode = $testCode
        }
    }
}

$port = Get-ApiPort
Write-Step 'Bootstrap complete'
Write-KV 'Health' ("http://127.0.0.1:{0}/api/v1/health" -f $port)
Write-KV 'Readiness' ("http://127.0.0.1:{0}/api/v1/ready (X-API-Key required)" -f $port)
Write-KV 'Docs' ("http://127.0.0.1:{0}/docs" -f $port)
Write-Info 'Set MT5_SERVER_TIMEZONE or MT5_SERVER_UTC_OFFSET_MINUTES in .env for UTC-correct history.'
Write-Info 'The API key is stored in .env. Never commit or share it.'

exit $installCode
