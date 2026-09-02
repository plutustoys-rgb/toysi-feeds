@echo off
rem PlutusToys control panel launcher.
rem Stops any already-running panel on port 8787, starts a fresh one from the
rem repo directory (so imports resolve), then opens the browser. One double-click,
rem no more "cd wrong dir / old version still running".
cd /d C:\Users\smach\rozetka_agent
rem stop a panel already listening on 8787 (so a new version loads)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "127.0.0.1:8787" ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
rem start the panel in its own labeled window (Ctrl+C or close it to stop)
start "PlutusToys Panel" cmd /k python control_panel.py
rem give the server a moment to bind, then open the panel in the default browser
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8787
