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
if ($Action -eq 'On' -or $Action -eq 'Off') {
    $r = Invoke-VpsCmd "test -f $app/.env && cp -p $app/.env $app/.env.bak_reelguard_$stamp && echo BACKUP_OK"
    if ($vpsRc -ne 0 -or $r -notmatch 'BACKUP_OK') { Stop-Here "cannot back up .env (rc $vpsRc): $r" }
    $line = ''
    if ($Action -eq 'On') { $line = " && echo 'REEL_GUARD=1' >> $app/.env" }
    $r = Invoke-VpsCmd "sed -i '/^REEL_GUARD=/d' $app/.env$line && grep -c '^REEL_GUARD=1' $app/.env"
    $n = ($r -split "`n" | Select-Object -Last 1).Trim()
    if ($Action -eq 'On' -and $n -ne '1') { Stop-Here "REEL_GUARD=1 not set (count=$n); backup: $app/.env.bak_reelguard_$stamp" }
    if ($Action -eq 'Off' -and $n -ne '0') { Stop-Here "REEL_GUARD still present (count=$n); backup: $app/.env.bak_reelguard_$stamp" }
    Write-Output "OK: REEL_GUARD lines with value 1 in .env = $n (backup $app/.env.bak_reelguard_$stamp)"
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
