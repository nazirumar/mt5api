<#
.SYNOPSIS
    Registers user-level logon tasks for the MT5 terminal, API and Caddy proxy.

.DESCRIPTION
    Creates four user-level Task Scheduler tasks using Register-ScheduledTask
    with an interactive user principal and limited run level. No administrator
    rights are required. Existing tasks with the same names are replaced
    idempotently.

    NazbeenMT5Terminal -> _launch_terminal.ps1 (handles terminal64.exe and terminal.exe)
    NazbeenMT5API      -> _api_autostart.ps1  (~30s delay, bounded terminal wait,
                                                no duplicate uvicorn instance)
    NazbeenMT5Proxy    -> _proxy_autostart.ps1 (~45s delay, only when Caddy is
                                                enabled and discoverable)
    NazbeenMT5Watchdog -> watchdog.ps1 (every 5 minutes; repairs stopped API,
                                         terminal or proxy components)

.PARAMETER SkipTerminal
    Do not register the NazbeenMT5Terminal task.

.PARAMETER SkipApi
    Do not register the NazbeenMT5API task.

.PARAMETER SkipProxy
    Do not register the NazbeenMT5Proxy task.

.PARAMETER SkipWatchdog
    Do not register the NazbeenMT5Watchdog task.

.EXAMPLE
    .\scripts\install_autostart.ps1

.EXAMPLE
    .\scripts\install_autostart.ps1 -SkipProxy
#>

param(
    [switch]$SkipTerminal,
    [switch]$SkipApi,
    [switch]$SkipProxy,
    [switch]$SkipWatchdog
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
        Write-Hint 'Restore scripts/_launch_terminal.ps1, _api_autostart.ps1, _proxy_autostart.ps1 and watchdog.ps1.'
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

        # Laptops must still start the local services on battery. Task Scheduler
        # defaults otherwise silently skip these logon tasks and leave the API
        # unavailable after a reboot.
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -MultipleInstances IgnoreNew

        Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

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

function New-WatchdogTask {
    param([Parameter(Mandatory)][string]$Name)

    $helperPath = Join-Path $script:ScriptsDir 'watchdog.ps1'
    if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
        Write-Fail ("Helper script missing: {0}" -f $helperPath)
        return $false
    }

    try {
        $argument = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}"' -f $helperPath
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument
        $userId = '{0}\{1}' -f $env:USERDOMAIN, $env:USERNAME
        $trigger = New-ScheduledTaskTrigger `
            -Once -At (Get-Date).AddMinutes(1) `
            -RepetitionInterval (New-TimeSpan -Minutes 5) `
            -RepetitionDuration (New-TimeSpan -Days 3650)
        $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -MultipleInstances IgnoreNew

        Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
        $registered = Get-ScheduledTask -TaskName $Name -ErrorAction Stop
        if ($registered.Actions[0].Arguments -like '*watchdog.ps1*') {
            Write-Ok ("Registered task: {0}" -f $Name)
            return $true
        }
        Write-Fail ("Task {0} was created but its command was unexpected." -f $Name)
        return $false
    } catch {
        Write-Fail ("Could not register watchdog task: {0}" -f $Name)
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

if (-not $SkipWatchdog) {
    $results.Watchdog = New-WatchdogTask -Name 'NazbeenMT5Watchdog'
} else {
    Write-Info 'Skipped NazbeenMT5Watchdog (-SkipWatchdog).'
}

Write-Info 'Logon tasks run at logon and the watchdog repeats every 5 minutes; no administrator rights are needed.'
if ($results.Values -contains $false) { exit 1 }
exit 0
