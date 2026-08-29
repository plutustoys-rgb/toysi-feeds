# checkbox_registry_sync.ps1 — обгортка Windows Task Scheduler для задачі
# PlutusToys-ChecboxRegistrySync. Запускає детермінований збирач чеків Checkbox
# (checkbox_registry_sync.py): нові фіскальні чеки нашої каси → кандидати доходу у
# документи_КОДВ/YYYY-MM/Checkbox/. READ-ONLY по касі (лише GET /receipts/search),
# книгу НЕ пише. Замінює скасовану LLM-рутину kodv-checkbox-частину doc-collector.
#
# Задача власника 2026-08-29: механічні КОДВ-рутини — детермінованими скриптами, не LLM.
$ErrorActionPreference = "Stop"
$py = "C:\Users\smach\AppData\Local\Python\pythoncore-3.14-64\python.exe"
Set-Location "C:\Users\smach\rozetka_agent"
& $py checkbox_registry_sync.py
exit $LASTEXITCODE
