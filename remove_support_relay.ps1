# UNDO for install_support_relay.ps1: removes the single support-relay line from /root/.ssh/authorized_keys on the VPS. ASCII only. Run by the OWNER.
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$out = Join-Path $PSScriptRoot "remove_support_relay_out.txt"
$rootKey = Join-Path $env:USERPROFILE ".ssh\plutustoys_vps"
$script = @'
n0=$(grep -c . /root/.ssh/authorized_keys)
sed -i '/ support-relay$/d' /root/.ssh/authorized_keys
n1=$(grep -c . /root/.ssh/authorized_keys)
echo "lines before=$n0 after=$n1"
if grep -q ' support-relay$' /root/.ssh/authorized_keys; then echo STILL_PRESENT; else echo REMOVED; fi
'@
$script = $script -replace "`r", ""
$res = $script | ssh -i $rootKey -o ConnectTimeout=20 root@45.94.157.4 "sh -s"
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') result:`n$($res -join "`n")" | Out-File $out -Encoding ascii
Write-Host ($res -join "`n")
Write-Host "Report written to $out"
