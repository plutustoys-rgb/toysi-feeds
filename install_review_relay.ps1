# Installs the NARROW review-moderation channel (owner decision 23.11.2026). ASCII only. Run by the OWNER (writes to the VPS authorized_keys).
# 1) creates a NEW ssh key ~/.ssh/review_relay on this PC (if absent; NOT the site_chat_relay key)   2) adds ONE line to /root/.ssh/authorized_keys on the VPS:
#    command="/opt/plutustoys/venv/bin/python3 /opt/plutustoys/review_relay.py",restrict ssh-ed25519 <public key> review-relay
#    => that key can ONLY run review_relay.py (list / show / approve / reject of reviews in the pending state). No shell, no .env, no orders.db, no forwarding.
# Safety: the remote script is sent as BASE64 (no CRLF/BOM problems); umask 077 (no wide-permission temp files); a TIMESTAMPED backup authorized_keys.bak_review_relay_<date>_<pid> is made first (mode 600; a rerun never overwrites an earlier backup);
#         a missing trailing newline is repaired BEFORE appending (so the owner's own root key line is never glued); the line count must grow by exactly 1, otherwise the backup is restored.
# 3) self-test through the new key: `list` must work and `id` must be REJECTED.   Prints the public key only; the private key is never shown.
# UNDO: remove_review_relay.ps1.  Prerequisite: review_relay.py must already be on the VPS (vps-code-sync, up to 15 min after the PR merge).
$out = Join-Path $PSScriptRoot "install_review_relay_out.txt"
$key = Join-Path $env:USERPROFILE ".ssh\review_relay"
$rootKey = Join-Path $env:USERPROFILE ".ssh\plutustoys_vps"
$log = @()
if (-not (Test-Path $key)) {
    ssh-keygen -q -t ed25519 -N '""' -C review-relay -f $key
    $log += "key created: $key"
} else { $log += "key already exists: $key" }
$pub = (Get-Content "$key.pub" -Raw).Trim()
if ($pub -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+( .*)?$') { $log += "ERROR: unexpected public key format"; $log | Out-File $out -Encoding ascii; Write-Host ($log -join "`n"); exit 1 }
$blob = ($pub -split ' ')[1]
$line = 'command="/opt/plutustoys/venv/bin/python3 /opt/plutustoys/review_relay.py",restrict ssh-ed25519 ' + $blob + ' review-relay'
$sh = @'
umask 077
test -f /opt/plutustoys/review_relay.py || { echo "NO_RELAY_SCRIPT_ON_VPS (wait for vps-code-sync, then rerun)"; exit 3; }
f=/root/.ssh/authorized_keys
mkdir -p /root/.ssh && chmod 700 /root/.ssh && touch $f && chmod 600 $f
bk=$f.bak_review_relay_$(date +%Y%m%d%H%M%S)_$$
cp -p $f $bk && chmod 600 $bk
n0=$(grep -c . $f)
added=0
if grep -qF '__BLOB__' $f; then echo ALREADY_INSTALLED; else
  if [ -s $f ] && [ "$(tail -c1 $f | wc -l)" -eq 0 ]; then echo >> $f; echo "repaired missing trailing newline"; fi
  printf '%s\n' '__LINE__' >> $f && echo ADDED && added=1
fi
n1=$(grep -c . $f)
echo "lines before=$n0 after=$n1"
echo "exact_line_present=$(grep -cxF '__LINE__' $f)"
if [ "$added" = 1 ] && [ "$n1" -ne "$((n0+1))" ]; then cp -p $bk $f; echo "ERROR: line count is not +1, backup RESTORED"; fi
'@
$sh = ($sh -replace "`r", "").Replace('__BLOB__', $blob).Replace('__LINE__', $line)
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($sh))
$res = ssh -i $rootKey -o ConnectTimeout=20 root@45.94.157.4 "echo $b64 | base64 -d | sh"
$log += "install: " + ($res -join " | ")
$t1 = ssh -i $key -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20 root@45.94.157.4 "list"
$rc1 = $LASTEXITCODE
$log += "selftest list (rc=$rc1, must be 0): " + (($t1 | Select-Object -First 3) -join " | ")
$t2 = ssh -i $key -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20 root@45.94.157.4 "id"
$rc2 = $LASTEXITCODE
$log += "selftest id (must be REJECTED, rc=$rc2): " + (($t2 | Select-Object -First 2) -join " | ")
if ($rc1 -eq 0) { $log += "SELFTEST list: PASS" } else { $log += "SELFTEST list: FAIL" }
if (($rc2 -eq 2) -and (("$t2").Length -gt 0) -and (("$t2") -notmatch 'uid=')) { $log += "SELFTEST id rejected: PASS" } else { $log += "SELFTEST id rejected: FAIL (rc must be exactly 2 = relay refusal with a message; rc 255 = key not installed/ssh error, other = unexpected; if uid= was printed a shell is exposed - run remove_review_relay.ps1)" }
$log += "public key: $pub"
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n" + ($log -join "`n") | Out-File $out -Encoding ascii
Write-Host ($log -join "`n")
Write-Host "Report written to $out"
