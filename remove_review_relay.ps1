# UNDO for install_review_relay.ps1: removes EXACTLY the review-relay line from /root/.ssh/authorized_keys on the VPS. ASCII only. Run by the OWNER.
# Safety: umask 077 (temp copy is mode 600); backup first (mode 600); only a line that STARTS WITH command="/opt/plutustoys/venv/bin/python3 /opt/plutustoys/review_relay.py",restrict ssh-ed25519
#         and ENDS with " review-relay" is removed; the file is rewritten ONLY if exactly 1 line disappears (0 = nothing to do, >1 = ABORT) and the count of foreign key lines
#         (support-relay, owner keys = every line that is not the review-relay line) is unchanged. The remote script is sent as BASE64.
$out = Join-Path $PSScriptRoot "remove_review_relay_out.txt"
$rootKey = Join-Path $env:USERPROFILE ".ssh\plutustoys_vps"
$sh = @'
umask 077
f=/root/.ssh/authorized_keys
cp -p $f $f.bak_review_relay_remove && chmod 600 $f.bak_review_relay_remove
n0=$(grep -c . $f)
s0=$(grep -c ' support-relay$' $f)
grep -v '^command="/opt/plutustoys/venv/bin/python3 /opt/plutustoys/review_relay.py",restrict ssh-ed25519 [A-Za-z0-9+/=]* review-relay$' $f > $f.tmp_rr
n1=$(grep -c . $f.tmp_rr)
s1=$(grep -c ' support-relay$' $f.tmp_rr)
d=$((n0-n1))
if [ "$d" -eq 1 ] && [ "$s0" -eq "$s1" ]; then cat $f.tmp_rr > $f; echo "REMOVED: lines $n0 -> $n1 (support-relay lines $s0 -> $s1)";
elif [ "$d" -eq 0 ]; then echo "NOTHING TO REMOVE: no review-relay line, nothing changed";
else echo "ABORT: would remove $d lines (support-relay $s0 -> $s1), nothing changed"; fi
rm -f $f.tmp_rr
if grep -q ' review-relay$' $f; then echo "review-relay line STILL PRESENT"; else echo "no review-relay line"; fi
echo "lines now: $(grep -c . $f), support-relay lines now: $(grep -c ' support-relay$' $f)"
'@
$sh = $sh -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($sh))
$res = ssh -i $rootKey -o ConnectTimeout=20 root@45.94.157.4 "echo $b64 | base64 -d | sh"
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') result:`n$($res -join "`n")" | Out-File $out -Encoding ascii
Write-Host ($res -join "`n")
Write-Host "Report written to $out"
