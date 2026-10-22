@echo off
rem Weekly full snapshot of the Cowork folder (mascot_new, KODV docs, media, all .md) to the VPS.
rem Usage:  cowork_snapshot_to_vps.cmd [--dry-run]
rem   --dry-run : only checks ssh access + VPS free space + local size; sends nothing.
rem Owner/scheduled task runs it. The tar stream goes straight into ssh (cmd does not corrupt binary data, PowerShell 5.1 does).
rem VPS side: deploy\cowork_snapshot_remote.sh -> /opt/plutustoys_backup/cowork_YYYY-MM-DD (mode 700, keeps 2 newest).
rem Exit code != 0 means the snapshot was NOT published (older snapshots stay intact).
setlocal
set KEY=C:\Users\smach\.ssh\plutustoys_vps
set HOST=root@45.94.157.4
set SRC=C:\Users\smach\Claude\Projects
set TAR=C:\Windows\System32\tar.exe

if /I "%~1"=="--dry-run" goto dryrun

scp -q -i %KEY% "%~dp0cowork_snapshot_remote.sh" %HOST%:/tmp/cowork_snapshot_remote.sh
if errorlevel 1 (echo SNAPSHOT_FAILED: scp of the remote script failed & exit /b 10)
%TAR% -cf - -C %SRC% PlutusToys_avtonomiya | ssh -i %KEY% %HOST% "tr -d '\r' < /tmp/cowork_snapshot_remote.sh > /tmp/csr.sh && bash /tmp/csr.sh"
exit /b %errorlevel%

:dryrun
echo [dry-run] ssh access and free space on the VPS:
ssh -i %KEY% %HOST% "df -h / | tail -1; ls -1d /opt/plutustoys_backup/cowork_* 2>/dev/null | tail -3"
if errorlevel 1 (echo [dry-run] ssh FAILED & exit /b 11)
echo [dry-run] local size of the folder to send:
powershell -NoProfile -Command "$m=Get-ChildItem -LiteralPath '%SRC%\PlutusToys_avtonomiya' -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum; 'files={0} GB={1:N2}' -f $m.Count, ($m.Sum/1GB)"
echo [dry-run] nothing was sent.
exit /b 0
