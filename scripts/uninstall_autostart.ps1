<#
.SYNOPSIS
    Removes the user-level NazbeenMT5 logon tasks.

.DESCRIPTION
    Deletes the NazbeenMT5Terminal, NazbeenMT5API and NazbeenMT5Proxy tasks
    using schtasks.exe /Delete /TN ... /F. Missing tasks are treated as
    success, so the script is safe to run repeatedly. No files or settings are
    removed.

.PARAMETER KeepTerminal
    Keep the NazbeenMT5Terminal task.

.PARAMETER KeepApi
    Keep the NazbeenMT5API task.

.PARAMETER KeepProxy
    Keep the NazbeenMT5Proxy (Caddy) task.

.EXAMPLE
    .\scripts\uninstall_autostart.ps1

.EXAMPLE
    .\scripts\uninstall_autostart.ps1 -KeepProxy
#>

param(
    [switch]$KeepTerminal,
    [switch]$KeepApi,
    [switch]$KeepProxy
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

function Remove-LogonTask {
    param([Parameter(Mandatory)][string]$Name)
    $output = & schtasks.exe /Delete /TN $Name /F 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Ok ("Removed task: {0}" -f $Name)
        return $true
    }
    $query = & schtasks.exe /Query /TN $Name 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Info ("Task was not present: {0}" -f $Name)
        return $true
    }
    Write-WarnMsg ("Could not remove task {0}: {1}" -f $Name, ($output | Out-String).Trim())
    return $false
}

Write-Step 'Removing user-level autostart tasks'

$ok = $true
if (-not $KeepTerminal) { $ok = (Remove-LogonTask -Name 'NazbeenMT5Terminal') -and $ok }
if (-not $KeepApi) { $ok = (Remove-LogonTask -Name 'NazbeenMT5API') -and $ok }
if (-not $KeepProxy) { $ok = (Remove-LogonTask -Name 'NazbeenMT5Proxy') -and $ok }

if ($ok) { Write-Ok 'Autostart cleanup complete.' } else { Write-WarnMsg 'Some tasks could not be removed.' }
exit 0
