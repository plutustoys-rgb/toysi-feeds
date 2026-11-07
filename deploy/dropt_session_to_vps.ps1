# Копіює сесію кабінету Dropt з ПК на VPS і перевіряє, що VPS її прийняла (python dropt_cabinet.py --check на VPS: сесія жива, баланс читається). Нічого не замовляє.
# Коли запускати: після `python dropt_cabinet.py --login` на ПК (вхід власника через Google/Telegram у вікні Playwright). Стара копія на VPS лишається як .bak_<дата>; при невдалій перевірці - відкат.
# Секретів у вивід не друкує. Запуск: powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\smach\rozetka_agent\deploy\dropt_session_to_vps.ps1
$ErrorActionPreference = 'Stop'
$key = 'C:\Users\smach\.ssh\plutustoys_vps'
$srv = 'root@45.94.157.4'
$src = 'C:\Users\smach\rozetka_agent\.secrets\dropt_cabinet_state.json'
$dst = '/opt/plutustoys/.secrets/dropt_cabinet_state.json'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
function Invoke-VpsCmd($cmd) {
    $o = ssh -n -i $key -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 $srv $cmd
    $script:vpsRc = $LASTEXITCODE
    return (($o | ForEach-Object { "$_" }) -join "`n")
}
function Stop-Here($msg) { Write-Output "СТОП: $msg"; exit 1 }

if (-not (Test-Path $src)) { Stop-Here "нема $src - спершу на ПК: python dropt_cabinet.py --login" }
Write-Output "--- 1. заливка копії (.new)"
Invoke-VpsCmd "mkdir -p /opt/plutustoys/.secrets && chmod 700 /opt/plutustoys/.secrets" | Out-Null
scp -i $key -o BatchMode=yes -o ConnectTimeout=20 $src "${srv}:$dst.new"
if ($LASTEXITCODE -ne 0) { Stop-Here "scp не вдався (код $LASTEXITCODE)" }
$r = Invoke-VpsCmd "chmod 600 $dst.new && test -s $dst.new && echo STAGED_OK"
if ($vpsRc -ne 0 -or $r -notmatch 'STAGED_OK') { Invoke-VpsCmd "rm -f $dst.new" | Out-Null; Stop-Here "chmod/перевірка .new на VPS не пройшла (код $vpsRc): $r" }
Write-Output "--- 2. підміна (стара -> .bak_$stamp)"
$r = Invoke-VpsCmd "if [ -f $dst ]; then cp -p $dst $dst.bak_$stamp; fi && mv $dst.new $dst && echo SWAPPED"
if ($vpsRc -ne 0 -or $r -notmatch 'SWAPPED') { Invoke-VpsCmd "rm -f $dst.new" | Out-Null; Stop-Here "підміна не відбулась (код $vpsRc): $r. Нова копія прибрана, стара на місці." }
Write-Output "--- 3. перевірка на VPS: сесія жива?"
$r = Invoke-VpsCmd "cd /opt/plutustoys && venv/bin/python3 dropt_cabinet.py --check 2>&1 | tail -n 3"
Write-Output $r
if ($r -notmatch 'сесія жива') {
    Write-Output "ВІДКАТ: VPS не прийняла сесію Dropt"
    $r2 = Invoke-VpsCmd "if [ -f $dst.bak_$stamp ]; then mv $dst.bak_$stamp $dst && echo ROLLED_BACK; else rm -f $dst; echo NO_BACKUP_REMOVED_NEW; fi"
    Write-Output $r2
    exit 1
}
Write-Output "ГОТОВО: VPS прийняла сесію кабінету Dropt."
