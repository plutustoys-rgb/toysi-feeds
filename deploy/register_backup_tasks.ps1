# register_backup_tasks.ps1 - registers two local scheduled tasks for backups (idempotent).
#   PlutusToys_BackupTextSet       daily 03:10  - backup_text_set.py (local git copy of the critical TEXT set; push only when a private remote is configured)
#   PlutusToys_BackupCoworkToVps   weekly Sun 03:40 - cowork_snapshot_to_vps.cmd (full Cowork snapshot to the VPS, keeps 2). Created DISABLED
#                                  unless -EnableSnapshot (enable only after one successful manual run: `cowork_snapshot_to_vps.cmd --dry-run`, then real).
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\register_backup_tasks.ps1 [-DryRun] [-EnableSnapshot]
# Same style as the other PlutusToys_* tasks (current user, Interactive, StartWhenAvailable). Nothing here touches ~/.claude or the VPS.
param([switch]$DryRun, [switch]$EnableSnapshot)

$ErrorActionPreference = 'Stop'
$py     = 'C:\Users\smach\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$hidden = 'C:\Users\smach\run_hidden.vbs'
$repo   = 'C:\Users\smach\rozetka_agent'
$logdir = 'C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya\backups_logs'

foreach ($f in @($py, $hidden, "$repo\backup_text_set.py", "$repo\deploy\cowork_snapshot_to_vps.cmd")) {
    if (-not (Test-Path -LiteralPath $f)) { throw "missing file: $f" }
}

$defs = @(
    @{ Name = 'PlutusToys_BackupTextSet'
       Exec = 'wscript.exe'
       Args = "`"$hidden`" `"$repo\backup_text_set.py`""
       Trigger = (New-ScheduledTaskTrigger -Daily -At '03:10')
       Limit = (New-TimeSpan -Minutes 30)
       Enabled = $true },
    @{ Name = 'PlutusToys_BackupCoworkToVps'
       Exec = 'cmd.exe'
       Args = "/c `"`"$repo\deploy\cowork_snapshot_to_vps.cmd`" >> `"$logdir\cowork_snapshot.log`" 2>&1`""
       Trigger = (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '03:40')
       Limit = (New-TimeSpan -Hours 4)
       Enabled = [bool]$EnableSnapshot }
)

foreach ($d in $defs) {
    # Повторний запуск НЕ змінює стан за власника: якщо задача вже є, її Enabled/Disabled зберігається
    # (вмикати знімок - -EnableSnapshot або Enable-ScheduledTask).
    $existing0 = Get-ScheduledTask -TaskName $d.Name -ErrorAction SilentlyContinue
    if ($existing0 -and -not $EnableSnapshot) { $d.Enabled = ($existing0.State -ne 'Disabled') }
    $state = if ($d.Enabled) { 'ENABLED' } else { 'DISABLED' }
    if ($DryRun) { Write-Output "[dry-run] $($d.Name) -> $($d.Exec) $($d.Args) [$state]"; continue }
    New-Item -ItemType Directory -Force -Path $logdir | Out-Null
    $action    = New-ScheduledTaskAction -Execute $d.Exec -Argument $d.Args
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit $d.Limit
    $task = New-ScheduledTask -Action $action -Trigger $d.Trigger -Principal $principal -Settings $settings
    if (-not $d.Enabled) { $task.Settings.Enabled = $false }
    Register-ScheduledTask -TaskName $d.Name -InputObject $task -Force | Out-Null   # -Force замінює наявну БЕЗ вікна, коли Register падає - стара лишається
    Write-Output "[ok] $($d.Name) registered [$state]"
}
