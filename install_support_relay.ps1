# Installs the NARROW support-chat channel (owner decision 21.11.2026). ASCII only. Run by the OWNER (writes to the VPS authorized_keys).
# 1) creates a NEW ssh key ~/.ssh/site_chat_relay on this PC (if absent)   2) adds ONE line to /root/.ssh/authorized_keys on the VPS:
#    command="/opt/plutustoys/venv/bin/python3 /opt/plutustoys/site_chat_relay.py",restrict ssh-ed25519 <public key> support-relay
#    => that key can ONLY run site_chat_relay.py (list / digest / reply to known chats). No shell, no .env, no orders.db, no forwarding.
# 3) self-test through the new key: `list` must work and `id` must be REJECTED.   Prints the public key only; the private key is never shown.
# UNDO: remove_support_relay.ps1.  Prerequisite: site_chat_relay.py must already be on the VPS (vps-code-sync, up to 15 min after the PR merge).
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
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
$script = @"
test -f /opt/plutustoys/site_chat_relay.py || { echo "NO_RELAY_SCRIPT_ON_VPS (wait for vps-code-sync, then rerun)"; exit 3; }
mkdir -p /root/.ssh && chmod 700 /root/.ssh && touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
if grep -qF '$blob' /root/.ssh/authorized_keys; then echo ALREADY_INSTALLED; else printf '%s\n' '$line' >> /root/.ssh/authorized_keys && echo ADDED; fi
grep -c 'support-relay' /root/.ssh/authorized_keys
"@
$script = $script -replace "`r", ""
$res = $script | ssh -i $rootKey -o ConnectTimeout=20 root@45.94.157.4 "sh -s"
$log += "install: " + ($res -join " | ")
$t1 = ssh -i $key -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20 root@45.94.157.4 "list"
$log += "selftest list (rc=$LASTEXITCODE): " + (($t1 | Select-Object -First 3) -join " | ")
$t2 = ssh -i $key -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20 root@45.94.157.4 "id"
$log += "selftest id (must be rejected, rc=$LASTEXITCODE): " + (($t2 | Select-Object -First 2) -join " | ")
$log += "public key: $pub"
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n" + ($log -join "`n") | Out-File $out -Encoding ascii
Write-Host ($log -join "`n")
Write-Host "Report written to $out"
