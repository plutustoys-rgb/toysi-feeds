# Копіює свіжу сесію кабінету EVA з ПК на VPS (окрема копія для eva-cabinet-keepalive) і перевіряє, що VPS її прийняв.
# Коли запускати: після `python eva_cabinet_scraper.py --login` (сесія EVA живе рівно 14 діб від входу, keepalive її не подовжує).
# Безпечно: стара копія на VPS лишається як .bak_<дата>; кожен крок перевіряється (код виходу + маркер у виводі), інакше стоп/відкат.
# Відкат — лише якщо keepalive на новій сесії не пройшов двічі (одна повторна спроба через 20 с: збій мережі EVA не має відкочувати робочу сесію).
# Секретів у вивід не друкує. Контроль: health_check читає дату спливання сесії З VPS (знімок ops-health) і попередить за 3 дні.
# Запуск: powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\smach\rozetka_agent\deploy\eva_session_to_vps.ps1
$ErrorActionPreference = 'Stop'
$key = 'C:\Users\smach\.ssh\plutustoys_vps'
$srv = 'root@45.94.157.4'
$src = 'C:\Users\smach\rozetka_agent\.local_secrets\eva_cabinet_state.json'
$dst = '/opt/plutustoys/.secrets/eva_cabinet_state.json'
$svc = 'eva-cabinet-keepalive.service'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
function Invoke-VpsCmd($cmd) {
    $o = ssh -n -i $key -o BatchMode=yes -o ConnectTimeout=20 $srv $cmd
    $script:vpsRc = $LASTEXITCODE
    return (($o | ForEach-Object { "$_" }) -join "`n")
}
function Stop-Here($msg) { Write-Output "СТОП: $msg"; exit 1 }

if (-not (Test-Path $src)) { Stop-Here "нема $src - спершу python eva_cabinet_scraper.py --login" }
# python на цьому ПК пише RuntimeWarning у stderr: з Stop це була б термінальна помилка, тому на цей виклик Continue і фільтр по формату дати
$ErrorActionPreference = 'Continue'
$pyOut = python -c "import json,datetime,sys; s=json.load(open(sys.argv[1],encoding='utf-8')); c=[x for x in s['cookies'] if x['name']=='sessionid']; print(datetime.datetime.fromtimestamp(c[0]['expires']).strftime('%Y-%m-%d %H:%M') if c else 'NONE')" $src 2>&1
$ErrorActionPreference = 'Stop'
$exp = $pyOut | ForEach-Object { "$_" } | Where-Object { $_ -match '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}|NONE)$' } | Select-Object -First 1
Write-Output "сесія на ПК дійсна до: $exp"
if (-not $exp -or $exp -eq 'NONE') { Stop-Here "у файлі нема sessionid або дати - вхід не завершено" }
if ([datetime]::ParseExact($exp, 'yyyy-MM-dd HH:mm', $null) -lt (Get-Date).AddDays(1)) { Stop-Here "сесія на ПК спливає менш ніж за добу - зробіть свіжий --login" }

Write-Output "--- 1. заливка копії (.new)"
scp -i $key -o BatchMode=yes -o ConnectTimeout=20 $src "${srv}:$dst.new"
if ($LASTEXITCODE -ne 0) { Stop-Here "scp не вдався (код $LASTEXITCODE)" }
$r = Invoke-VpsCmd "chmod 600 $dst.new && test -s $dst.new && echo STAGED_OK"
if ($vpsRc -ne 0 -or $r -notmatch 'STAGED_OK') { Invoke-VpsCmd "rm -f $dst.new" | Out-Null; Stop-Here "chmod/перевірка .new на VPS не пройшла (код $vpsRc): $r" }

Write-Output "--- 2. підміна (стара -> .bak_$stamp)"
$r = Invoke-VpsCmd "if [ -f $dst ]; then cp -p $dst $dst.bak_$stamp; fi && mv $dst.new $dst && echo SWAPPED"
if ($vpsRc -ne 0 -or $r -notmatch 'SWAPPED') { Invoke-VpsCmd "rm -f $dst.new" | Out-Null; Stop-Here "підміна не відбулась (код $vpsRc): $r. Нова копія прибрана, стара не чіпана." }

Write-Output "--- 3. перевірка VPS: keepalive на новій сесії"
$res = ''
foreach ($attempt in 1, 2) {
    $r = Invoke-VpsCmd "systemctl start $svc; systemctl show $svc -p Result --value | tail -n1"
    $res = ($r -split "`n" | Select-Object -Last 1).Trim()
    Write-Output "спроба ${attempt}: Result=$res"
    if ($res -eq 'success') { break }
    if ($attempt -eq 1) { Start-Sleep -Seconds 20 }
}
if ($res -ne 'success') {
    Write-Output "ВІДКАТ: keepalive на VPS = '$res' після двох спроб"
    $r = Invoke-VpsCmd "if [ -f $dst.bak_$stamp ]; then mv $dst.bak_$stamp $dst && echo ROLLED_BACK; else echo NO_BACKUP; fi"
    Write-Output $r
    if ($r -notmatch 'ROLLED_BACK') { Write-Output "УВАГА: відкат не підтверджено - повідомте Код, стан файлу сесії на VPS невідомий" }
    exit 1
}
Write-Output "ГОТОВО: VPS прийняв сесію EVA (дійсна до $exp). Наступний вхід потрібен до цієї дати; за 3 дні попередить health_check."
