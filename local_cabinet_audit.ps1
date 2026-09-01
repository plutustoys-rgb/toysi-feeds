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

# 3a-2. EVA unbooked-orders candidates for KODV (owner request 2026-08-29, same automation as
#       rozetka_commission_ledger.py above). Reuses eva_cabinet_scraper.py's session - no
#       separate login. Writes candidates into документи_КОДВ, never into the book itself.
& $py eva_orders_ledger.py

# 3a-3. EVA actual per-order commission for KODV graph 9 (bookkeeper gap 2026-08-29). Reads the
#       «Сума комісії» block on each seller.eva.ua/merchant/orders/{id} card (fact, not the 15%
#       estimate; §довідника, verified live). Reuses eva session - no separate login. Candidates
#       into документи_КОДВ/EVA, never the book.
& $py eva_commission_ledger.py

# 3b. Toysi deposit (same pattern). One-time: `python toysi_cabinet_scraper.py --login`.
& $py toysi_cabinet_scraper.py

# 3c. Rozetka catalog health (counts + block reasons). One-time: `--login`.
& $py rozetka_cabinet_scraper.py

# 3c-2. Rozetka royalty+logistics commission ledger for KODV graph 9 (owner request 2026-08-29:
#       accountant was manually re-checking every order's commission). Reuses the same session
#       as rozetka_cabinet_scraper.py above - no separate login. Writes candidates straight into
#       the shared документи_КОДВ folder, never into the book itself.
& $py rozetka_commission_ledger.py

# 3c-3. FC/RozetkaPay registry STORNO detector for KODV (accountant request 2026-08-31: a bank
#       storno sat unnoticed in the book for 17 days). Reads the archived RozetkaPay registry
#       (kodv_mail_archiver routes it to документи_КОДВ/*/RozetkaPay/), flags storno + acquiring
#       graph-9 candidates cross-referenced with the book (READ-ONLY). Candidates only, never the book.
& $py rozetkapay_registry_kandydaty.py

# 3d. Prom notifications (top of /cms/notifications, money signals). One-time: `--login`.
& $py prom_notifications_scraper.py

# 3d-2. Prom per-order commission for KODV graph 9 (owner request 2026-08-29). Reads actual
#       cpa_commission via Prom Orders API (methodology КОДВ_норми_довідник §3, verified 12/12),
#       writes candidates into документи_КОДВ/Prom - never the book. Needs an order-scope
#       PROM_API_KEY in .env; with a products-only token it soft-exits (no spam).
& $py prom_commission_ledger.py

# 3e. ALLO cabinet (balances + subscription-balance warning + orders). One-time: `--login`.
& $py allo_cabinet_scraper.py

# 4. Weekly balance trend digest (rewrites today's - cheap to run daily).
& $py weekly_balance_digest.py

# 4b. NovaPay IMAP heartbeat (login-only, no orders.db) - lets critical_watch show the
#     reconciliation tile self-healing locally. Reconciliation itself runs on VPS; this is
#     just an auth-liveness check catching the AUTHENTICATIONFAILED class (app-password revoked).
& $py novapay_imap_heartbeat.py

# 5. Marketplace requirements GATE - structural enforcement (owner 2026-09-01): every feed that
#    needs an authoritative marketplace reference (category tree/attributes) must keep it SAVED in
#    the repo + a passing auto-verify. Runs WITH Telegram ON (unset AUDIT_NO_TELEGRAM only for this
#    step) so a real violation actually reaches the owner - discipline that does not depend on any
#    session remembering. Restores the silent flag right after.
$env:AUDIT_NO_TELEGRAM = ""
& $py marketplace_requirements_gate.py
$env:AUDIT_NO_TELEGRAM = "1"

Write-Output "[local-audit] Done. Reports in $reportDir"
