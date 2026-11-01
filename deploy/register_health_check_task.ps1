# register_health_check_task.ps1 - registers the local scheduled task PlutusToys_HealthCheck (idempotent).
#   PlutusToys_HealthCheck   hourly - health_check.py --post : reads VPS snapshot (private branch ops-health), published feeds, local tasks,
#                            local alert journal, critical calendar; appends NEW problems to EXECUTOR_CHANNEL.md ("[Avtomat -> Kod]"). Read-only otherwise.
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\register_health_check_task.ps1 [-DryRun]
# Same style as the other PlutusToys_* tasks (current user, Interactive, StartWhenAvailable). Nothing here touches ~/.claude or the VPS.
param([switch]$DryRun)

$ErrorActionPreference = 'Stop'
$hidden = 'C:\Users\smach\run_hidden.vbs'
$repo   = 'C:\Users\smach\rozetka_agent'
foreach ($f in @($hidden, "$repo\health_check.py")) {
    if (-not (Test-Path -LiteralPath $f)) { throw "missing file: $f" }
}
$name = 'PlutusToys_HealthCheck'
$args = "`"$hidden`" `"$repo\health_check.py`" --post"
if ($DryRun) { Write-Output "[dry-run] $name -> wscript.exe $args [hourly]"; exit 0 }
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
$enabled = if ($existing) { $existing.State -ne 'Disabled' } else { $true }   # re-run does not change owner's Enabled/Disabled choice
$action    = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument $args -WorkingDirectory $repo
$trigger   = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(5)) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings
if (-not $enabled) { $task.Settings.Enabled = $false }
Register-ScheduledTask -TaskName $name -InputObject $task -Force | Out-Null
Write-Output "[ok] $name registered [$(if ($enabled) { 'ENABLED' } else { 'DISABLED' })]"
