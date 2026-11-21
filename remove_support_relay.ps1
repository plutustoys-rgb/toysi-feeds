# UNDO for install_support_relay.ps1: removes EXACTLY the support-relay line from /root/.ssh/authorized_keys on the VPS. ASCII only. Run by the OWNER.
# Safety: backup first; only a line that STARTS WITH command="/opt/plutustoys/venv/bin/python3 /opt/plutustoys/site_chat_relay.py",restrict ssh-ed25519 and ENDS with " support-relay" is removed;
#         if more than one line would disappear, nothing is changed (ABORT). The remote script is sent as BASE64.
$out = Join-Path $PSScriptRoot "remove_support_relay_out.txt"
$rootKey = Join-Path $env:USERPROFILE ".ssh\plutustoys_vps"
$sh = @'
f=/root/.ssh/authorized_keys
cp -p $f $f.bak_support_relay_remove
n0=$(grep -c . $f)
grep -v '^command="/opt/plutustoys/venv/bin/python3 /opt/plutustoys/site_chat_relay.py",restrict ssh-ed25519 [A-Za-z0-9+/=]* support-relay$' $f > $f.tmp_sr
n1=$(grep -c . $f.tmp_sr)
d=$((n0-n1))
if [ "$d" -le 1 ]; then cat $f.tmp_sr > $f; echo "REMOVED: lines $n0 -> $n1"; else echo "ABORT: would remove $d lines, nothing changed"; fi
rm -f $f.tmp_sr
if grep -q ' support-relay$' $f; then echo "support-relay line STILL PRESENT"; else echo "no support-relay line"; fi
'@
$sh = $sh -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($sh))
$res = ssh -i $rootKey -o ConnectTimeout=20 root@45.94.157.4 "echo $b64 | base64 -d | sh"
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') result:`n$($res -join "`n")" | Out-File $out -Encoding ascii
Write-Host ($res -join "`n")
Write-Host "Report written to $out"
