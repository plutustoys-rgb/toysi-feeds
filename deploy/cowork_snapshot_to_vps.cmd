@echo off
rem Weekly full snapshot of the Cowork folder (mascot_new, KODV docs, media, all .md) + the text-backup repo to the VPS.
rem Usage:  cowork_snapshot_to_vps.cmd [--dry-run]
rem   --dry-run : only checks ssh access + VPS free space + local size; sends nothing.
rem Owner/scheduled task runs it. Steps: (1) tar -> temp file, its exit code is CHECKED (a tar|ssh pipe in cmd returns only ssh's code, so a
rem failed/partial tar used to look like success); (2) count of files packed; (3) stream the file to ssh; the VPS side compares the count.
rem VPS side: deploy\cowork_snapshot_remote.sh -> /opt/plutustoys_backup/cowork_YYYY-MM-DD (mode 700, keeps 2 newest).
rem Exit code != 0 means the snapshot was NOT published (older snapshots stay intact).
setlocal
set KEY=C:\Users\smach\.ssh\plutustoys_vps
set HOST=root@45.94.157.4
set SSHOPT=-o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30 -o ServerAliveCountMax=6
set SRC=C:\Users\smach\Claude\Projects
set EXTRA=
if exist "C:\Users\smach\plutus_backup_repo\.git" set EXTRA=-C C:\Users\smach plutus_backup_repo
set TAR=C:\Windows\System32\tar.exe
set TMPTAR=%TEMP%\cowork_snapshot.tar
set TMPLIST=%TEMP%\cowork_snapshot.lst
set REMOTE=/root/.plutus_cowork_snapshot_remote.sh

if /I "%~1"=="--dry-run" goto dryrun

if exist "%TMPTAR%" del /q "%TMPTAR%"
%TAR% -cf "%TMPTAR%" --exclude=*.tmp --exclude=.~lock* --exclude=backups_logs -C %SRC% PlutusToys_avtonomiya %EXTRA%
if errorlevel 1 (echo SNAPSHOT_FAILED: tar could not pack everything - a file is locked/unreadable - nothing sent & del /q "%TMPTAR%" 2>nul & exit /b 12)

set COUNT=
%TAR% -tf "%TMPTAR%" > "%TMPLIST%"
if errorlevel 1 (echo SNAPSHOT_FAILED: cannot list the packed archive & del /q "%TMPTAR%" 2>nul & exit /b 13)
for /f %%n in ('powershell -NoProfile -Command "(Get-Content -LiteralPath $env:TMPLIST | Where-Object { -not $_.EndsWith('/') }).Count"') do set COUNT=%%n
del /q "%TMPLIST%" 2>nul
if "%COUNT%"=="" (echo SNAPSHOT_FAILED: cannot count packed files & del /q "%TMPTAR%" 2>nul & exit /b 13)
echo packed %COUNT% files

scp -q -i %KEY% %SSHOPT% "%~dp0cowork_snapshot_remote.sh" %HOST%:%REMOTE%
if errorlevel 1 (echo SNAPSHOT_FAILED: scp of the remote script failed & del /q "%TMPTAR%" 2>nul & exit /b 10)
ssh -i %KEY% %SSHOPT% %HOST% "sed -i 's/\r$//' %REMOTE% && bash %REMOTE% %COUNT%" < "%TMPTAR%"
set RC=%errorlevel%
del /q "%TMPTAR%" 2>nul
exit /b %RC%

:dryrun
echo [dry-run] ssh access and free space on the VPS:
ssh -i %KEY% %SSHOPT% %HOST% "df -h / | tail -1; ls -1d /opt/plutustoys_backup/cowork_* 2>/dev/null | tail -3"
if errorlevel 1 (echo [dry-run] ssh FAILED & exit /b 11)
echo [dry-run] local size of the folder to send:
powershell -NoProfile -Command "$m=Get-ChildItem -LiteralPath '%SRC%\PlutusToys_avtonomiya' -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum; 'files={0} GB={1:N2}' -f $m.Count, ($m.Sum/1GB)"
echo [dry-run] nothing was sent.
exit /b 0
