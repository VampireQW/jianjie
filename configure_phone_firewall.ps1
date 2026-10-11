$ErrorActionPreference = 'Stop'
$programPath = Join-Path $PSScriptRoot 'dist\简截.exe'
$resultPath = Join-Path $PSScriptRoot 'build\phone-firewall-status.json'
try {
    if (-not (Test-Path -LiteralPath $programPath -PathType Leaf)) { throw '找不到正式版简截' }
    $network = Get-NetConnectionProfile | Where-Object IPv4Connectivity -eq Internet | Select-Object -First 1
    if (-not $network) { throw '未找到当前局域网连接' }
    $ruleName = 'JianJiePhoneReceiveLan'
    $existing = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
    if ($existing) {
        Set-NetFirewallRule -Name $ruleName -Enabled True -Direction Inbound -Action Allow -Profile Any -InterfaceAlias $network.InterfaceAlias -Program $programPath -Protocol TCP -LocalPort 5034 -RemoteAddress LocalSubnet | Out-Null
    } else {
        New-NetFirewallRule -Name $ruleName -DisplayName '简截手机传图（仅局域网）' -Enabled True -Direction Inbound -Action Allow -Profile Any -InterfaceAlias $network.InterfaceAlias -Program $programPath -Protocol TCP -LocalPort 5034 -RemoteAddress LocalSubnet | Out-Null
    }
    @{ok=$true; message='已允许本程序通过当前网卡接收局域网 TCP 5034 连接'} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
} catch {
    @{ok=$false; message=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    exit 1
}
