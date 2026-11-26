<#
.SYNOPSIS
    Removes local hosts mappings for a domain.

.DESCRIPTION
    Removes every host token matching the domain from the Windows hosts file,
    including duplicates and mappings to IPs other than 127.0.0.1. All other
    entries and comments are preserved. When a line no longer has any hosts,
    only that line is dropped. Writes through a temporary file with the
    original ACL preserved, flushes DNS and verifies removal. Idempotent.

.PARAMETER Domain
    Hostname to remove. Defaults to LOCAL_DOMAIN from .env (or
    mt5.nazbeen.test).

.PARAMETER NoFlush
    Skip ipconfig /flushdns after editing the hosts file.

.EXAMPLE
    .\scripts\remove_domain.ps1

.EXAMPLE
    .\scripts\remove_domain.ps1 -Domain mt5.nazbeen.test

.NOTES
    Requires administrator rights. If they are missing, the exact elevated
    command is printed and the script exits without changes.
#>

param(
    [string]$Domain = '',
    [switch]$NoFlush
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$domainName = if ([string]::IsNullOrWhiteSpace($Domain)) { Get-Domain } else { $Domain.Trim() }

Write-Step ("Removing local domain mapping: {0}" -f $domainName)

if (-not (Test-IsAdmin)) {
    Write-WarnMsg 'Administrator rights are required to edit C:\Windows\System32\drivers\etc\hosts.'
    $arguments = '-Domain "{0}"' -f $domainName
    if ($NoFlush) { $arguments += ' -NoFlush' }
    $command = Get-ElevatedCommand -ScriptName 'remove_domain.ps1' -Arguments $arguments
    Write-Info 'Run this exact command in an elevated PowerShell window:'
    Write-Host ("    {0}" -f $command) -ForegroundColor Yellow
    Write-Info 'No changes were made.'
    exit 0
}

$hostsPath = Join-Path $env:windir 'System32\drivers\etc\hosts'
try {
    $mapping = Set-HostsDomainMapping -Domain $domainName -Add:$false
} catch {
    Write-Fail ("Could not prepare hosts file update: {0}" -f $_.Exception.Message)
    exit 1
}

if (-not $mapping.Changed) {
    Write-Ok ("No mapping for {0} was present; nothing to change." -f $domainName)
    exit 0
}

try {
    Write-HostsFile -Path $hostsPath -Content $mapping.NewText
    Write-Ok ("Removed {0} mapping(s) for {1}; all other entries were preserved." -f $mapping.RemovedCount, $domainName)
} catch {
    Write-Fail ("Could not write hosts file: {0}" -f $_.Exception.Message)
    Write-Hint 'Check that PowerShell is elevated and that no security product is locking the file.'
    exit 1
}

if (-not $NoFlush) { Invoke-DnsFlush }

if (Test-HostsEntry -Domain $domainName) {
    Write-WarnMsg 'A matching hosts entry is still present; please inspect the hosts file manually.'
    exit 1
}
Write-Ok ("Verified: no hosts entry for {0} remains." -f $domainName)
exit 0
