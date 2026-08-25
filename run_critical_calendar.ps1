# run_critical_calendar.ps1 — обгортка для задачі PlutusToys_CriticalCalendar.
# Вказує AUDIT_REPORT_DIR на Cowork-папку (там живі balance_history/heartbeat і туди
# ж лягає critical_calendar.html — «вікно на компі»), тоді запускає монітор.
# Запускається прихованою задачею Task Scheduler (щогодини + при вході).
$env:AUDIT_REPORT_DIR = 'C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya\reports'
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = 'C:\Users\smach\AppData\Local\Microsoft\WindowsApps\python.exe' }
& $py 'C:\Users\smach\rozetka_agent\critical_watch.py'
