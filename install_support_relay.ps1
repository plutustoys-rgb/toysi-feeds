# Installs the NARROW support-chat channel (owner decision 21.11.2026). ASCII only. Run by the OWNER (writes to the VPS authorized_keys).
# 1) creates a NEW ssh key ~/.ssh/site_chat_relay on this PC (if absent)   2) adds ONE line to /root/.ssh/authorized_keys on the VPS:
#    command="/opt/plutustoys/venv/bin/python3 /opt/plutustoys/site_chat_relay.py",restrict ssh-ed25519 <public key> support-relay
#    => that key can ONLY run site_chat_relay.py (list / digest / reply to known chats). No shell, no .env, no orders.db, no forwarding.
# Safety: the remote script is sent as BASE64 (no CRLF/BOM problems); a backup authorized_keys.bak_support_relay is made first; a missing trailing newline is repaired
#         BEFORE appending (so the owner's own root key line is never glued); the line count must grow by exactly 1.
# 3) self-test through the new key: `list` must work and `id` must be REJECTED.   Prints the public key only; the private key is never shown.
# UNDO: remove_support_relay.ps1.  Prerequisite: site_chat_relay.py must already be on the VPS (vps-code-sync, up to 15 min after the PR merge).
$out = Join-Path $PSScriptRoot "install_support_relay_out.txt"
$key = Join-Path $env:USERPROFILE ".ssh\site_chat_relay"
$rootKey = Join-Path $env:USERPROFILE ".ssh\plutustoys_vps"
$log = @()
if (-not (Test-Path $key)) {
    ssh-keygen -q -t ed25519 -N '""' -C support-relay -f $key
    $log += "key created: $key"
} else { $log += "key already exists: $key" }
$pub = (Get-Content "$key.pub" -Raw).Trim()
if ($pub -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+( .*)?$') { $log += "ERROR: unexpected public key format"; $log | Out-File $out -Encoding ascii; Write-Host ($log -join "`n"); exit 1 }
$blob = ($pub -split ' ')[1]
$line = 'command="/opt/plutustoys/venv/bin/python3 /opt/plutustoys/site_chat_relay.py",restrict ssh-ed25519 ' + $blob + ' support-relay'
$sh = @'
test -f /opt/plutustoys/site_chat_relay.py || { echo "NO_RELAY_SCRIPT_ON_VPS (wait for vps-code-sync, then rerun)"; exit 3; }
f=/root/.ssh/authorized_keys
mkdir -p /root/.ssh && chmod 700 /root/.ssh && touch $f && chmod 600 $f
cp -p $f $f.bak_support_relay
n0=$(grep -c . $f)
if grep -qF '__BLOB__' $f; then echo ALREADY_INSTALLED; else
  if [ -s $f ] && [ "$(tail -c1 $f | wc -l)" -eq 0 ]; then echo >> $f; echo "repaired missing trailing newline"; fi
  printf '%s\n' '__LINE__' >> $f && echo ADDED
fi
n1=$(grep -c . $f)
echo "lines before=$n0 after=$n1"
echo "exact_line_present=$(grep -cxF '__LINE__' $f)"
'@
$sh = ($sh -replace "`r", "").Replace('__BLOB__', $blob).Replace('__LINE__', $line)
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($sh))
$res = ssh -i $rootKey -o ConnectTimeout=20 root@45.94.157.4 "echo $b64 | base64 -d | sh"
$log += "install: " + ($res -join " | ")
$t1 = ssh -i $key -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20 root@45.94.157.4 "list"
$log += "selftest list (rc=$LASTEXITCODE): " + (($t1 | Select-Object -First 3) -join " | ")
$t2 = ssh -i $key -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20 root@45.94.157.4 "id"
$log += "selftest id (must be REJECTED, rc=$LASTEXITCODE): " + (($t2 | Select-Object -First 2) -join " | ")
$log += "public key: $pub"
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n" + ($log -join "`n") | Out-File $out -Encoding ascii
Write-Host ($log -join "`n")
Write-Host "Report written to $out"
