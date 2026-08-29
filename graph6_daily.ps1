# graph6_daily.ps1 — обгортка Windows Task Scheduler для задачі PlutusToys-Graph6Daily.
# Запускає детермінований збирач собівартості Toysi (graph6_daily.py): реалізовані замовлення
# з кабінету Toysi «Історія замовлень» → кандидати графи 6 у документи_КОДВ/YYYY-MM/Toysi/.
# READ-ONLY по кабінету, книгу НЕ пише. Замінює скасовану LLM-частину doc-collector (Toysi-gap).
#
# Задача власника 2026-08-29: «закупівлі Toysi треба зробити»; механічні КОДВ-рутини — скриптами.
$ErrorActionPreference = "Stop"
$py = "C:\Users\smach\AppData\Local\Python\pythoncore-3.14-64\python.exe"
Set-Location "C:\Users\smach\rozetka_agent"
& $py graph6_daily.py
exit $LASTEXITCODE
