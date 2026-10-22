#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_vps_audit_guard.py — приймальні тести відновленого deploy/claude_home/hooks/vps-audit-guard.sh.

Три живі тести з архіву (запис Коду 22.09.2026, archive/consultant/2026-10_archive.md:936-941):
  (а) блок без маркера; (б) дозвіл з правильним хешем; (в) повторний блок після зміни файлу.
+ межі: команда без ssh/scp не блокується; згадка файлу в git/cat не блокується; файл не знайдено -> блок;
  формат маркера sha256sum із зірочкою; ввід без _vps_ -> тихий пропуск; битий JSON -> пропуск з попередженням.
Усе в тимчасовій теці (VPS_GUARD_REPO). Потрібен bash у PATH. `python test_vps_audit_guard.py` -> exit 0/1.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOOK = Path(__file__).resolve().parent / "deploy" / "claude_home" / "hooks" / "vps-audit-guard.sh"
BASH = shutil.which("bash")
F = []


def chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        F.append(name)


if not BASH:
    print("[SKIP] bash не знайдено")
    sys.exit(0)

tmp = Path(tempfile.mkdtemp())
(tmp / "_vps_demo.sh").write_text("echo audited\n", encoding="utf-8", newline="\n")
env = {**os.environ, "VPS_GUARD_REPO": str(tmp).replace("\\", "/"), "PYTHONIOENCODING": "utf-8"}


def run(cmd, tool="Bash", raw=None):
    payload = raw if raw is not None else json.dumps({"tool_name": tool, "tool_input": {"command": cmd}, "cwd": str(tmp)})
    r = subprocess.run([BASH, str(HOOK)], input=payload.encode("utf-8"), capture_output=True, env=env, timeout=30)
    out = r.stdout.decode("utf-8", "replace").strip()
    return r.returncode, out, r.stderr.decode("utf-8", "replace")


def denied(out):
    try:
        return json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    except Exception:  # noqa: BLE001
        return False


send = "scp -i k _vps_demo.sh root@45.94.157.4:/tmp/ && ssh root@45.94.157.4 'bash /tmp/_vps_demo.sh'"
digest = hashlib.sha256((tmp / "_vps_demo.sh").read_bytes()).hexdigest()

rc, out, _ = run(send)
chk("(а) без маркера -> БЛОК", rc == 0 and denied(out))
(tmp / ".vps_audit_ok").write_text(f"{digest}  _vps_demo.sh\n", encoding="utf-8")
rc, out, _ = run(send)
chk("(б) правильний хеш -> дозвіл (порожній вивід)", rc == 0 and out == "")
(tmp / "_vps_demo.sh").write_text("echo CHANGED after audit\n", encoding="utf-8", newline="\n")
rc, out, _ = run(send)
chk("(в) файл змінено після аудиту -> знову БЛОК", rc == 0 and denied(out))

(tmp / "_vps_demo.sh").write_text("echo audited\n", encoding="utf-8", newline="\n")
(tmp / ".vps_audit_ok").write_text(f"{digest} *_vps_demo.sh\n", encoding="utf-8")
chk("формат sha256sum із зірочкою -> дозвіл", denied(run(send)[1]) is False)
chk("PowerShell-інструмент: той самий блок/дозвіл (дозвіл)", run(send, tool="PowerShell")[1] == "")
(tmp / ".vps_audit_ok").write_text("", encoding="utf-8")
chk("PowerShell-інструмент без маркера -> БЛОК", denied(run(send, tool="PowerShell")[1]))
chk("git add/cat без ssh/scp -> не блокується", run("git add _vps_demo.sh && cat _vps_demo.sh")[1] == "")
chk("ssh без _vps_ скрипта -> не блокується", run("ssh root@45.94.157.4 'systemctl --failed'")[1] == "")
chk("файл не знайдено -> БЛОК", denied(run("scp _vps_nope.sh root@45.94.157.4:/tmp/")[1]))
chk("ввід без _vps_ -> тихий пропуск", run("", raw='{"tool_input":{"command":"ls"}}') == (0, "", ""))
rc, out, err = run("", raw="_vps_ not json")
chk("битий JSON -> пропуск з попередженням у stderr", rc == 0 and out == "" and "не розібрано" in err)

shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if F else 0)
