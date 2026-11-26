<#
.SYNOPSIS
    Registers user-level logon tasks for the MT5 terminal, API and Caddy proxy.

.DESCRIPTION
    Creates three Task Scheduler tasks using Register-ScheduledTask with an
    interactive user principal and limited run level. No administrator rights
    are required. Existing tasks with the same names are replaced idempotently.

    NazbeenMT5Terminal -> _launch_terminal.ps1 (handles terminal64.exe and terminal.exe)
    NazbeenMT5API      -> _api_autostart.ps1  (~30s delay, bounded terminal wait,
                                                no duplicate uvicorn instance)
    NazbeenMT5Proxy    -> _proxy_autostart.ps1 (~45s delay, only when Caddy is
                                                enabled and discoverable)

.PARAMETER SkipTerminal
    Do not register the NazbeenMT5Terminal task.

.PARAMETER SkipApi
    Do not register the NazbeenMT5API task.

.PARAMETER SkipProxy
    Do not register the NazbeenMT5Proxy task.

.EXAMPLE
    .\scripts\install_autostart.ps1

.EXAMPLE
    .\scripts\install_autostart.ps1 -SkipProxy
#>

param(
    [switch]$SkipTerminal,
    [switch]$SkipApi,
    [switch]$SkipProxy
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

function New-LogonTask {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$HelperFile
    )

    $helperPath = Join-Path $script:ScriptsDir $HelperFile
    if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
        Write-Fail ("Helper script missing: {0}" -f $helperPath)
        Write-Hint 'Restore scripts/_launch_terminal.ps1, _api_autostart.ps1 and _proxy_autostart.ps1.'
        return $false
    }

    # Register-ScheduledTask creates a user-level task without elevation.
    # schtasks.exe /Create is unreliable with paths containing spaces.
    try {
        $argument = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}"' -f $helperPath
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument
        $userId = '{0}\{1}' -f $env:USERDOMAIN, $env:USERNAME
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
        $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null

        $registered = Get-ScheduledTask -TaskName $Name -ErrorAction Stop
        if ($registered.Actions[0].Arguments -like ('*{0}*' -f $HelperFile)) {
            Write-Ok ("Registered task: {0}" -f $Name)
            return $true
        }
        Write-Fail ("Task {0} was created but its command was unexpected." -f $Name)
        return $false
    } catch {
        Write-Fail ("Could not register task: {0}" -f $Name)
        Write-Hint $_.Exception.Message
        return $false
    }
}

Write-Step 'Installing user-level autostart tasks'

$results = [ordered]@{}

if (-not $SkipTerminal) {
    $results.Terminal = New-LogonTask -Name 'NazbeenMT5Terminal' -HelperFile '_launch_terminal.ps1'
} else {
    Write-Info 'Skipped NazbeenMT5Terminal (-SkipTerminal).'
}

if (-not $SkipApi) {
    $results.Api = New-LogonTask -Name 'NazbeenMT5API' -HelperFile '_api_autostart.ps1'
} else {
    Write-Info 'Skipped NazbeenMT5API (-SkipApi).'
}

if (-not $SkipProxy) {
    if (Get-ConfigBool -Key 'ENABLE_CADDY' -Default $true) {
        $caddy = Find-Caddy
        if ([string]::IsNullOrWhiteSpace($caddy)) {
            Write-WarnMsg 'Caddy executable not found; skipping NazbeenMT5Proxy.'
            Write-Hint 'Install Caddy or set CADDY_PATH, then re-run install_autostart.ps1.'
        } else {
            $results.Proxy = New-LogonTask -Name 'NazbeenMT5Proxy' -HelperFile '_proxy_autostart.ps1'
        }
    } else {
        Write-Info 'ENABLE_CADDY=false; skipping NazbeenMT5Proxy.'
    }
} else {
    Write-Info 'Skipped NazbeenMT5Proxy (-SkipProxy).'
}

Write-Info 'Tasks run for the current user at logon; no administrator rights are needed.'
if ($results.Values -contains $false) { exit 1 }
exit 0
