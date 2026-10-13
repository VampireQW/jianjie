param([Parameter(Mandatory=$true)][string]$Program, [string]$ResultPath)
$ErrorActionPreference = 'Stop'
$resolved = (Resolve-Path -LiteralPath $Program).ProviderPath
if ([IO.Path]::GetFileName($resolved) -ne 'receiver.exe') { throw 'Invalid receiver path.' }
$ruleName = 'JianJie-AirPlay-Local'
$policy = New-Object -ComObject HNetCfg.FwPolicy2
if ($policy.DefaultInboundAction(4) -ne 0) { throw 'Public network must block other inbound connections by default.' }
# Only adjust the Windows prompt rules for this exact receiver executable.
$adjusted = @()
Get-NetFirewallApplicationFilter -Program $resolved -ErrorAction SilentlyContinue |
    Get-NetFirewallRule | Where-Object {
        $_.Direction -eq 'Inbound' -and $_.DisplayName -eq 'receiver.exe' -and
        $_.Name -match '^(TCP|UDP) Query User' -and
        ([int]$_.Profile -band 4) -ne 0 -and $_.Enabled -eq 'True'
    } | ForEach-Object {
        if ($_.Action -eq 'Allow') {
            $_ | Set-NetFirewallRule -EdgeTraversalPolicy Block
            $_ | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress LocalSubnet
        } elseif ($_.Action -eq 'Block') {
            $_ | Disable-NetFirewallRule
        }
        $adjusted += $_.Name
    }
$existing = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    $existing | Set-NetFirewallRule -Enabled True -Direction Inbound -Action Allow -Profile Any -EdgeTraversalPolicy Block -RemoteAddress LocalSubnet
    $existing | Get-NetFirewallApplicationFilter | Set-NetFirewallApplicationFilter -Program $resolved
} else {
    $rule = @{
        Name = $ruleName
        DisplayName = '简截 iPhone 投屏（局域网）'
        Direction = 'Inbound'
        Action = 'Allow'
        Program = $resolved
        Profile = 'Any'
        RemoteAddress = 'LocalSubnet'
        Protocol = 'Any'
        EdgeTraversalPolicy = 'Block'
    }
    New-NetFirewallRule @rule | Out-Null
}
if ($ResultPath) {
    [pscustomobject]@{Success=$true;Program=$resolved;Scope='LocalSubnet';Adjusted=$adjusted;
                     PublicDefaultInboundBlocked=$true;Time=(Get-Date).ToString('o')} |
        ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $ResultPath -Encoding UTF8
}
