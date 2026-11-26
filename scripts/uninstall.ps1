<#
.SYNOPSIS
    Uninstalls the Nazbeen MT5 Market Data API automation.

.DESCRIPTION
    Removes the user-level scheduled tasks and the local hosts entry. Caddy's
    task can be kept with -KeepCaddyTask. By default this script NEVER deletes
    .env, data, logs, .venv or source files. Only the explicitly requested
    -DeleteData switch removes the generated data, logs and .venv directories;
    .env and source code are always preserved.

.PARAMETER KeepDomain
    Leave the hosts entry in place.

.PARAMETER KeepTasks
    Leave all scheduled tasks in place.

.PARAMETER KeepCaddyTask
    Keep the NazbeenMT5Proxy (Caddy) task while removing the other tasks.

.PARAMETER DeleteData
    Explicitly delete the generated data, logs and .venv directories. .env and
    source files are still preserved.

.PARAMETER Domain
    Domain whose hosts entry should be removed. Defaults to LOCAL_DOMAIN from
    .env (or mt5.nazbeen.test).

.EXAMPLE
    .\scripts\uninstall.ps1

.EXAMPLE
    .\scripts\uninstall.ps1 -KeepDomain -KeepCaddyTask

.EXAMPLE
    .\scripts\uninstall.ps1 -DeleteData
#>

param(
    [switch]$KeepDomain,
    [switch]$KeepTasks,
    [switch]$KeepCaddyTask,
    [switch]$DeleteData,
    [string]$Domain = ''
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

Write-Step 'Nazbeen MT5 Market Data API - uninstall'

# 1. Scheduled tasks
if ($KeepTasks) {
    Write-Info 'Keeping all scheduled tasks (-KeepTasks).'
} else {
    Write-Step 'Removing scheduled tasks'
    $arguments = @()
    if ($KeepCaddyTask) { $arguments += '-KeepProxy' }
    try {
        & (Join-Path $script:ScriptsDir 'uninstall_autostart.ps1') @arguments
        if ($LASTEXITCODE -ne 0) { Write-WarnMsg 'Task removal reported a problem.' }
    } catch {
        Write-WarnMsg ("Task removal failed: {0}" -f $_.Exception.Message)
    }
}

# 2. Hosts entry
if ($KeepDomain) {
    Write-Info 'Keeping the local domain hosts entry (-KeepDomain).'
} else {
    Write-Step 'Removing local domain hosts entry'
    $domainName = if ([string]::IsNullOrWhiteSpace($Domain)) { Get-Domain } else { $Domain.Trim() }
    if (Test-IsAdmin) {
        try {
            & (Join-Path $script:ScriptsDir 'remove_domain.ps1') -Domain $domainName
        } catch {
            Write-WarnMsg ("Domain removal failed: {0}" -f $_.Exception.Message)
        }
    } else {
        Write-WarnMsg 'Administrator rights are required to edit the hosts file.'
        $arguments = '-Domain "{0}"' -f $domainName
        Write-Info 'Run this exact command in an elevated PowerShell window:'
        Write-Host ("    {0}" -f (Get-ElevatedCommand -ScriptName 'remove_domain.ps1' -Arguments $arguments)) -ForegroundColor Yellow
    }
}

# 3. Optional generated data removal
if ($DeleteData) {
    Write-Step 'Deleting generated data, logs and virtual environment (-DeleteData)'
    foreach ($directory in @($script:DataDir, $script:LogDir, (Join-Path $script:ProjectRoot '.venv'))) {
        if (Test-Path -LiteralPath $directory) {
            try {
                Remove-Item -LiteralPath $directory -Recurse -Force
                Write-Ok ("Removed: {0}" -f $directory)
            } catch {
                Write-WarnMsg ("Could not remove {0}: {1}" -f $directory, $_.Exception.Message)
            }
        } else {
            Write-Info ("Already absent: {0}" -f $directory)
        }
    }
} else {
    Write-Info 'Preserved .env, data, logs, .venv and all source files (use -DeleteData to remove generated data).'
}

Write-Step 'Uninstall complete'
Write-Ok '.env and source files were not touched.'
Write-Info 'To fully remove the API, delete the project folder yourself after reviewing .env.'
exit 0
