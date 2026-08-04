# local_cabinet_audit.ps1 — ЛОКАЛЬНИЙ щоденний аудит каталогу/кабінетів, що пише
# звіти ПРЯМО у спільну Cowork-папку, БЕЗ Telegram і БЕЗ VPS. Запускається Windows
# Task Scheduler (замінює зламаний cabinet_audit_daily.ps1 на claude --print).
#
# Рішення власника 2026-08-04: аудитори бігають локально (VPS→Cowork-папка синхронізації
# не існує), пишуть у папку через AUDIT_REPORT_DIR, без Telegram через AUDIT_NO_TELEGRAM.
# Каталог-аудитори прибрані з VPS-пайплайна (#216 відкочено) — щоб не дублювались.

$ErrorActionPreference = "Continue"
$repo = "C:\Users\smach\rozetka_agent"
$py = "C:\Users\smach\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$reportDir = "C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya\reports"

Set-Location $repo
$env:AUDIT_REPORT_DIR = $reportDir      # звіти + balance_history → у спільну папку
$env:AUDIT_NO_TELEGRAM = "1"            # без Telegram — доставка у папку

if (-not (Test-Path $reportDir)) { New-Item -ItemType Directory -Force -Path $reportDir | Out-Null }

# 1. Свіжий eva_feed.xml — EVA-каталог-аудитор читає локальний файл; тягнемо останній
#    опублікований (feed-data). Якщо не вийшло — аудитор скористається наявним.
$evaFeedUrl = "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/eva_feed.xml"
try {
    Invoke-WebRequest -Uri $evaFeedUrl -OutFile "$repo\feeds\eva_feed.xml" -UseBasicParsing -TimeoutSec 120
} catch {
    Write-Output "[local-audit] eva_feed.xml не завантажено (аудитор візьме наявний): $_"
}

# 2. Каталог-аудитори (feed/API-derivable: eva читає фід+Toysi; prom — Toysi+Prom API).
& $py eva_catalog_auditor.py
& $py prom_catalog_auditor.py

# 3. Баланс EVA (Playwright + storageState). Якщо сесії ще нема — скрипт сам напише,
#    що треба один раз `python eva_cabinet_scraper.py --login`.
& $py eva_cabinet_scraper.py

# 4. Тижневий дайджест тенденції балансів (перезаписує сьогоднішній — дешево щодня).
& $py weekly_balance_digest.py

Write-Output "[local-audit] Готово. Звіти у $reportDir"
