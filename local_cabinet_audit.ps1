# local_cabinet_audit.ps1 - LOCAL daily catalog/cabinet audit writing reports
# straight into the shared Cowork folder, WITHOUT Telegram and WITHOUT the VPS.
# Runs via Windows Task Scheduler (replaces the broken cabinet_audit_daily.ps1
# that used `claude --print`).
#
# Owner decision 2026-08-04: auditors run locally (no VPS->Cowork-folder sync
# exists), write to the folder via AUDIT_REPORT_DIR, no Telegram via
# AUDIT_NO_TELEGRAM. Catalog auditors removed from the VPS pipeline (#216 reverted)
# so they do not run twice.
#
# NOTE: ASCII-only on purpose. Windows PowerShell 5.1 reads .ps1 as the system ANSI
# codepage unless there is a UTF-8 BOM, which mangles Cyrillic and breaks parsing.
# Report CONTENT stays Ukrainian (produced by the Python scripts) - only this
# launcher's own text is English to stay encoding-safe.

$ErrorActionPreference = "Continue"
$repo = "C:\Users\smach\rozetka_agent"
$py = "C:\Users\smach\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$reportDir = "C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya\reports"

Set-Location $repo
$env:AUDIT_REPORT_DIR = $reportDir      # reports + balance_history -> shared folder
$env:AUDIT_NO_TELEGRAM = "1"            # no Telegram - deliver to folder instead

if (-not (Test-Path $reportDir)) { New-Item -ItemType Directory -Force -Path $reportDir | Out-Null }

# 1. Fresh eva_feed.xml - the EVA catalog auditor reads the local file; pull the
#    latest published one (feed-data). If it fails, the auditor uses whatever exists.
$evaFeedUrl = "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/eva_feed.xml"
try {
    Invoke-WebRequest -Uri $evaFeedUrl -OutFile "$repo\feeds\eva_feed.xml" -UseBasicParsing -TimeoutSec 120
} catch {
    Write-Output "[local-audit] eva_feed.xml download failed (auditor uses existing): $_"
}

# 2. Catalog auditors (feed/API-derivable: eva reads feed+Toysi; prom - Toysi+Prom API).
& $py eva_catalog_auditor.py
& $py prom_catalog_auditor.py

# 3. EVA balance (Playwright + storageState). If the session is missing the script
#    prints that you must run `python eva_cabinet_scraper.py --login` once.
& $py eva_cabinet_scraper.py

# 3b. Toysi deposit (same pattern). One-time: `python toysi_cabinet_scraper.py --login`.
& $py toysi_cabinet_scraper.py

# 3c. Rozetka catalog health (counts + block reasons). One-time: `--login`.
& $py rozetka_cabinet_scraper.py

# 3d. Prom notifications (top of /cms/notifications, money signals). One-time: `--login`.
& $py prom_notifications_scraper.py

# 4. Weekly balance trend digest (rewrites today's - cheap to run daily).
& $py weekly_balance_digest.py

Write-Output "[local-audit] Done. Reports in $reportDir"
