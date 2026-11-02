# Копіює свіжу сесію кабінету EVA з ПК на VPS (окрема копія для eva-cabinet-keepalive) і перевіряє, що VPS її прийняв.
# Коли запускати: після `python eva_cabinet_scraper.py --login` (сесія EVA живе рівно 14 діб від входу, keepalive її не подовжує).
# Безпечно: стара копія на VPS лишається як .bak_<дата>; якщо перевірка на VPS не пройшла - стара копія повертається. Секретів у вивід не друкує.
# Запуск: powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\smach\rozetka_agent\deploy\eva_session_to_vps.ps1
$ErrorActionPreference = 'Stop'
$key = 'C:\Users\smach\.ssh\plutustoys_vps'
$srv = 'root@45.94.157.4'
$src = 'C:\Users\smach\rozetka_agent\.local_secrets\eva_cabinet_state.json'
$dst = '/opt/plutustoys/.secrets/eva_cabinet_state.json'
$stamp = Get-Date -Format 'yyyyMMdd_HHmm'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
function Invoke-VpsCmd($cmd) { ssh -n -i $key -o BatchMode=yes -o ConnectTimeout=20 $srv $cmd }
function Stop-Here($msg) { Write-Output "СТОП: $msg"; exit 1 }

if (-not (Test-Path $src)) { Stop-Here "нема $src - спершу python eva_cabinet_scraper.py --login" }
$exp = python -c "import json,datetime,sys; s=json.load(open(sys.argv[1],encoding='utf-8')); c=[x for x in s['cookies'] if x['name']=='sessionid']; print(datetime.datetime.fromtimestamp(c[0]['expires']).strftime('%Y-%m-%d %H:%M') if c else 'NONE')" $src 2>$null
Write-Output "сесія на ПК дійсна до: $exp"
if ($exp -eq 'NONE' -or -not $exp) { Stop-Here "у файлі нема sessionid - вхід не завершено" }

Write-Output "--- 1. заливка копії (.new, права 600)"
scp -i $key -o BatchMode=yes -o ConnectTimeout=20 $src "${srv}:$dst.new"
if ($LASTEXITCODE -ne 0) { Stop-Here "scp не вдався" }
Invoke-VpsCmd "chmod 600 $dst.new"

Write-Output "--- 2. підміна (стара -> .bak_$stamp)"
Invoke-VpsCmd "cp -p $dst $dst.bak_$stamp && mv $dst.new $dst && echo SWAPPED"

Write-Output "--- 3. перевірка VPS: keepalive на новій сесії"
Invoke-VpsCmd "systemctl start eva-cabinet-keepalive.service; echo RESULT=`$(systemctl show eva-cabinet-keepalive.service -p Result --value)"
$res = Invoke-VpsCmd "systemctl show eva-cabinet-keepalive.service -p Result --value"
if ($res -ne 'success') {
    Write-Output "ВІДКАТ: keepalive на VPS = $res"
    Invoke-VpsCmd "mv $dst.bak_$stamp $dst && echo ROLLED_BACK"
    exit 1
}
Write-Output "ГОТОВО: VPS прийняв сесію EVA (дійсна до $exp). Наступний вхід потрібен до цієї дати."
