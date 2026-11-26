<#
.SYNOPSIS
    Adds 127.0.0.1 <domain> to the Windows hosts file.

.DESCRIPTION
    Ensures exactly one local mapping for the chosen domain, removing any
    duplicate or incorrect mappings while preserving every other line and
    comment. Changes are written through a temporary file (UTF-8, no BOM) and
    copied back with the original ACL preserved. DNS is flushed and the result
    is verified with Resolve-DnsName. Idempotent.

.PARAMETER Domain
    Hostname to map to 127.0.0.1. Defaults to LOCAL_DOMAIN from .env (or
    mt5.nazbeen.test).

.PARAMETER NoFlush
    Skip ipconfig /flushdns after editing the hosts file.

.EXAMPLE
    .\scripts\setup_domain.ps1

.EXAMPLE
    .\scripts\setup_domain.ps1 -Domain mt5.nazbeen.test

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

Write-Step ("Configuring local domain mapping: 127.0.0.1 {0}" -f $domainName)

if (-not (Test-IsAdmin)) {
    Write-WarnMsg 'Administrator rights are required to edit C:\Windows\System32\drivers\etc\hosts.'
    $arguments = '-Domain "{0}"' -f $domainName
    if ($NoFlush) { $arguments += ' -NoFlush' }
    $command = Get-ElevatedCommand -ScriptName 'setup_domain.ps1' -Arguments $arguments
    Write-Info 'Run this exact command in an elevated PowerShell window:'
    Write-Host ("    {0}" -f $command) -ForegroundColor Yellow
    Write-Info 'No changes were made.'
    exit 0
}

$hostsPath = Join-Path $env:windir 'System32\drivers\etc\hosts'
try {
    $mapping = Set-HostsDomainMapping -Domain $domainName -Add
} catch {
    Write-Fail ("Could not prepare hosts file update: {0}" -f $_.Exception.Message)
    exit 1
}

if ($mapping.RemovedCount -gt 0) {
    Write-Info ("Removing {0} existing mapping(s) for {1} (IP(s): {2})." -f `
        $mapping.RemovedCount, $domainName, (($mapping.PreviousIPs) -join ', '))
}

if (-not $mapping.Changed) {
    Write-Ok ("Hosts entry already correct: 127.0.0.1 {0}" -f $domainName)
} else {
    try {
        Write-HostsFile -Path $hostsPath -Content $mapping.NewText
        Write-Ok ("Added canonical entry to {0}" -f $hostsPath)
    } catch {
        Write-Fail ("Could not write hosts file: {0}" -f $_.Exception.Message)
        Write-Hint 'Check that PowerShell is elevated and that no security product is locking the file.'
        exit 1
    }
    if (-not $NoFlush) { Invoke-DnsFlush }
}

$verification = Get-DomainResolution -Domain $domainName -Expected '127.0.0.1'
if ($verification.ExpectedFound) {
    Write-Ok ("Resolve-DnsName confirms {0} -> {1}" -f $domainName, (($verification.Addresses) -join ', '))
} else {
    Write-WarnMsg ("Resolution check did not return 127.0.0.1 for {0}." -f $domainName)
    Write-Hint 'Verify the hosts file line and run ipconfig /flushdns in an elevated shell.'
}

if (-not (Test-HostsEntry -Domain $domainName)) {
    Write-WarnMsg 'Hosts entry could not be confirmed; please inspect the hosts file manually.'
    exit 1
}
exit 0
