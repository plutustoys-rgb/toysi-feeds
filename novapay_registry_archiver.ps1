# novapay_registry_archiver.ps1 — обгортка Windows Task Scheduler для задачі
# PlutusToys-NovaPayRegistryArchiver. Запускає детермінований архіватор первинки КОДВ
# (kodv_mail_archiver.py): тягне з пошти plutustoys.novapay@gmail.com нові реєстри NovaPay
# І акти звірки Нової Пошти → локальна тека документи_КОДВ/YYYY-MM/{NovaPay,НоваПошта}/.
# READ-ONLY по пошті (BODY.PEEK, не ставить \Seen — не конфліктує з novapay_statement на VPS),
# книгу НЕ пише. Замінює дві скасовані LLM-рутини (kodv-novapay-archiver + kodv-novaposhta-akt-archiver).
#
# Задача власника 2026-08-29: механічні КОДВ-рутини — детермінованими скриптами, не LLM-сесіями.
$ErrorActionPreference = "Stop"
$py = "C:\Users\smach\AppData\Local\Python\pythoncore-3.14-64\python.exe"
Set-Location "C:\Users\smach\rozetka_agent"
& $py kodv_mail_archiver.py
exit $LASTEXITCODE
