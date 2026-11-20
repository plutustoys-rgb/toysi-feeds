# temp_janitor_install.ps1 -- registers a DAILY task that removes old test scratch folders in %TEMP% (temp_janitor.py --apply).
# Run ONCE by the owner (persistent config). Same pattern as PlutusToys_HealthCheck (wscript + run_hidden.vbs, current user, no admin).
# Remove: Unregister-ScheduledTask -TaskName PlutusToys_TempJanitor -Confirm:$false
# ASCII only (Windows PowerShell 5.1).
$ErrorActionPreference = "Stop"
$vbs  = "C:\Users\smach\run_hidden.vbs"
$py   = "C:\Users\smach\rozetka_agent\temp_janitor.py"
if (-not (Test-Path $vbs)) { throw "run_hidden.vbs not found: $vbs" }
if (-not (Test-Path $py)) { throw "temp_janitor.py not found: $py (merge the PR first and update the desktop copy)" }
$act = New-ScheduledTaskAction -Execute "wscript.exe" -Argument ('"{0}" "{1}" --apply' -f $vbs, $py) -WorkingDirectory "C:\Users\smach\rozetka_agent"
$trg = New-ScheduledTaskTrigger -Daily -At 04:15
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "PlutusToys_TempJanitor" -Action $act -Trigger $trg -Settings $set -Description "Removes tmp* scratch folders older than 24h from %TEMP% (temp_janitor.py)" -Force | Out-Null
Get-ScheduledTask -TaskName "PlutusToys_TempJanitor" | Select-Object TaskName, State | Format-Table -AutoSize
Write-Host "OK: PlutusToys_TempJanitor registered (daily 04:15)."
