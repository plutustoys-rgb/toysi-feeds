# reel_guard_switch.ps1 - ASCII only (PowerShell 5.1). Status / enable / disable / dry-run of reel_guard on the VPS.
# What it does NOT do: it never prints .env values, never touches reels_queue, never publishes anything.
# reel-guard.service/.timer are installed by vps-code-sync (deploy_systemd_units.sh copies ./*.service ./*.timer from the repo root and enables timers) ~15 min after the merge.
# Without REEL_GUARD=1 the guard only OBSERVES (state + log + alerts, no queue changes). REEL_GUARD=1 lets it build a replacement reel (max 2 per day) for a held reel built by reel_weekly.
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\smach\rozetka_agent\deploy\reel_guard_switch.ps1 -Action Status
#   ... -Action Install   (run deploy_systemd_units.sh now instead of waiting for vps-code-sync; then Status)
#   ... -Action DryRun    (python reel_guard.py --dry : prints the plan, writes nothing)
#   ... -Action On        (backup .env, set REEL_GUARD=1, run DryRun)
#   ... -Action Off       (backup .env, remove REEL_GUARD; guard returns to observe-only)
#   ... -Action RunNow    (systemctl start reel-guard.service, then last log lines)
param([Parameter(Mandatory = $true)][ValidateSet('Status', 'Install', 'DryRun', 'On', 'Off', 'RunNow')][string]$Action)
$ErrorActionPreference = 'Stop'
$key = 'C:\Users\smach\.ssh\plutustoys_vps'
$srv = 'root@45.94.157.4'
$app = '/opt/plutustoys'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
function Invoke-VpsCmd($cmd) {
    $o = ssh -n -i $key -o BatchMode=yes -o ConnectTimeout=20 $srv $cmd
    $script:vpsRc = $LASTEXITCODE
    return (($o | ForEach-Object { "$_" }) -join "`n")
}
function Stop-Here($msg) { Write-Output "STOP: $msg"; exit 1 }

if ($Action -eq 'Install') {
    $r = Invoke-VpsCmd "cd $app && bash deploy_systemd_units.sh 2>&1 | tail -n 5"
    Write-Output $r
}
# Remote script for On/Off (single-quoted here-string: nothing is expanded by PowerShell). Sent as base64 -> `base64 -d | bash`, so quotes/newlines survive ssh.
# On: drops old REEL_GUARD lines, GUARANTEES a trailing newline before appending (otherwise "A=1" + "REEL_GUARD=1" would glue into "A=1REEL_GUARD=1" and break key A),
# appends exactly one REEL_GUARD=1 (a line `export REEL_GUARD=...` or an indented one also counts as a REEL_GUARD line); temp copies are 0600 (umask 077, mktemp) and removed on exit;
# then compares .env (without REEL_GUARD lines) with the backup: every other key must be byte-identical.
$envScript = @'
set -u
umask 077
ENV=__APP__/.env
BAK=__BAK__
TA=$(mktemp) && TB=$(mktemp) || exit 1
trap 'rm -f "$TA" "$TB"' EXIT
RE='^[[:space:]]*(export[[:space:]]+)?REEL_GUARD='
sed -i -E "/$RE/d" "$ENV"
if [ "__ACTION__" = "On" ]; then
  if [ -s "$ENV" ] && [ -n "$(tail -c1 "$ENV")" ]; then printf '\n' >> "$ENV"; fi
  echo 'REEL_GUARD=1' >> "$ENV"
fi
grep -vE "$RE" "$BAK" > "$TA"
grep -vE "$RE" "$ENV" > "$TB"
if cmp -s "$TA" "$TB"; then echo OTHER_KEYS_INTACT; else echo OTHER_KEYS_CHANGED; fi
echo "REEL_GUARD_LINES=$(grep -cE "$RE" "$ENV")"
echo "REEL_GUARD_ONE=$(grep -c '^REEL_GUARD=1$' "$ENV")"
'@
function Invoke-VpsScript($script) {
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($script -replace "`r", '')))
    return (Invoke-VpsCmd "echo $b64 | base64 -d | bash")
}
if ($Action -eq 'On' -or $Action -eq 'Off') {
    $bak = "$app/.env.bak_reelguard_$stamp"
    $r = Invoke-VpsCmd "test -f $app/.env && cp -p $app/.env $bak && echo BACKUP_OK"
    if ($vpsRc -ne 0 -or $r -notmatch 'BACKUP_OK') { Stop-Here "cannot back up .env (rc $vpsRc): $r" }
    $r = Invoke-VpsScript ($envScript.Replace('__APP__', $app).Replace('__BAK__', $bak).Replace('__ACTION__', $Action))
    $intact = $r -match 'OTHER_KEYS_INTACT'
    $lines = if ($r -match 'REEL_GUARD_LINES=(\d+)') { [int]$Matches[1] } else { -1 }
    $ones = if ($r -match 'REEL_GUARD_ONE=(\d+)') { [int]$Matches[1] } else { -1 }
    if (-not $intact) { Stop-Here "other keys in .env changed or check failed - restore: cp $bak $app/.env ; output: $r" }
    if ($Action -eq 'On' -and ($lines -ne 1 -or $ones -ne 1)) { Stop-Here "expected exactly one REEL_GUARD=1 line (lines=$lines, ones=$ones); backup: $bak" }
    if ($Action -eq 'Off' -and $lines -ne 0) { Stop-Here "REEL_GUARD still present (lines=$lines); backup: $bak" }
    Write-Output "OK: REEL_GUARD lines=$lines (value 1: $ones), all other .env keys byte-identical to the backup $bak"
}
if ($Action -eq 'DryRun' -or $Action -eq 'On') {
    Write-Output '--- dry run (writes nothing)'
    Write-Output (Invoke-VpsCmd "cd $app && $app/venv/bin/python3 reel_guard.py --dry 2>&1 | tail -n 40")
}
if ($Action -eq 'RunNow') {
    Write-Output (Invoke-VpsCmd "systemctl start reel-guard.service; systemctl show reel-guard.service -p Result --value | tail -n1; journalctl -u reel-guard.service -n 15 --no-pager | cut -c1-220")
}
if ($Action -eq 'Status' -or $Action -eq 'Install' -or $Action -eq 'On' -or $Action -eq 'Off' -or $Action -eq 'RunNow') {
    Write-Output '--- status'
    Write-Output (Invoke-VpsCmd "systemctl is-enabled reel-guard.timer 2>&1; systemctl is-active reel-guard.timer 2>&1; echo -n 'REEL_GUARD=1 lines in .env: '; grep -c '^REEL_GUARD=1' $app/.env; echo '--- last log lines'; tail -n 5 $app/reports/reel_guard_log.jsonl 2>&1 | cut -c1-300")
}
