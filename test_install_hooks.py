#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_install_hooks.py — регрес deploy/claude_home/install_hooks.py (запускається ЛИШЕ проти підміненої домівки).

settings.json глобальний (зламаний хук валить усі сесії), тому перевіряємо вимоги Консультанта 22.10:
  --dry-run нічого не пише; бекап ПЕРЕД записом поза ~/.claude і шлях друкується; ідемпотентність за точною
  командою (чужий my-session-start.sh не вводить в оману); чужі налаштування збережені; --rollback повертає
  оригінал; битий JSON -> зрозуміла помилка і нічого не записано (ні скриптів, ні CLAUDE.md).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT = Path(__file__).resolve().parent / "deploy" / "claude_home" / "install_hooks.py"
F = []


def chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        F.append(name)


def fresh(settings_text):
    home = Path(tempfile.mkdtemp())
    (home / ".claude").mkdir()
    if settings_text is not None:
        (home / ".claude" / "settings.json").write_text(settings_text, encoding="utf-8")
    bk = home / "backups"
    env = {**os.environ, "USERPROFILE": str(home), "HOME": str(home), "PLUTUS_BACKUP_DIR": str(bk),
           "PYTHONIOENCODING": "utf-8"}
    return home, bk, env


def run(env, *args):
    r = subprocess.run([sys.executable, "-W", "ignore", str(SCRIPT), *args], env=env, capture_output=True, timeout=60)
    return r.returncode, r.stdout.decode("utf-8", "replace")


ORIG = json.dumps({"theme": "dark", "autoMode": {"x": 1}, "hooks": {
    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "bash /c/x/merge-guard.sh"}]}],
    "SessionStart": [{"hooks": [{"type": "command", "command": "bash /c/x/my-session-start.sh"}]}]}},
    ensure_ascii=False, indent=4)

# --- dry-run
home, bk, env = fresh(ORIG)
s = home / ".claude" / "settings.json"
rc, out = run(env, "--dry-run")
chk("dry-run: код 0, підсумковий JSON надруковано", rc == 0 and "session-start.sh" in out and "vps-audit-guard.sh" in out)
chk("dry-run: settings.json не змінено, бекапу/скриптів нема",
    s.read_text(encoding="utf-8") == ORIG and not bk.exists() and not (home / ".claude" / "hooks").exists())

# --- install
rc, out = run(env)
cfg = json.loads(s.read_text(encoding="utf-8"))
chk("install: код 0", rc == 0)
chk("бекап створено ПОЗА ~/.claude і шлях надруковано", any(bk.glob("settings.json.*")) and str(bk) in out)
chk("чужі налаштування збережено (theme, autoMode)", cfg.get("theme") == "dark" and cfg.get("autoMode") == {"x": 1})
chk("чужий SessionStart збережено, наш доданий окремим блоком",
    len(cfg["hooks"]["SessionStart"]) == 2 and cfg["hooks"]["SessionStart"][1].get("matcher") == "startup|resume|compact|clear")
chk("PreCompact додано", len(cfg["hooks"]["PreCompact"]) == 1)
chk("vps-audit-guard на Bash і PowerShell, merge-guard цілий",
    {b.get("matcher") for b in cfg["hooks"]["PreToolUse"]} == {"Bash", "PowerShell"}
    and any("merge-guard" in h["command"] for b in cfg["hooks"]["PreToolUse"] for h in b["hooks"]))
chk("скрипти встановлено з LF, CLAUDE.md створено",
    all(b"\r" not in (home / ".claude" / "hooks" / n).read_bytes() for n in ("session-start.sh", "pre-compact.sh", "vps-audit-guard.sh"))
    and (home / ".claude" / "CLAUDE.md").exists())

# --- ідемпотентність
before = s.read_text(encoding="utf-8")
nb = len(list(bk.glob("settings.json.*")))
rc, out = run(env)
chk("повторний запуск: нічого не дублює і settings.json не переписує", rc == 0 and s.read_text(encoding="utf-8") == before)
chk("повторний запуск: файл не змінено (skip)", "[skip] settings.json" in out)

# --- rollback
rc, out = run(env, "--rollback")
chk("rollback: settings.json повернено до оригіналу", rc == 0 and json.loads(s.read_text(encoding="utf-8")) == json.loads(ORIG))
chk("rollback: стан перед відкатом теж збережено", any(bk.glob("settings.json.before_rollback_*")))

# --- битий JSON
home2, bk2, env2 = fresh("{ це не json // коментар")
rc, out = run(env2)
chk("битий JSON: код 2 і зрозуміла помилка", rc == 2 and "не розбирається" in out)
chk("битий JSON: нічого не записано (ні скриптів, ні CLAUDE.md, ні бекапу)",
    not (home2 / ".claude" / "hooks").exists() and not (home2 / ".claude" / "CLAUDE.md").exists() and not bk2.exists())

# --- нема settings.json взагалі
home3, bk3, env3 = fresh(None)
rc, out = run(env3)
chk("нема settings.json: створюється з нашими хуками", rc == 0 and "SessionStart" in (home3 / ".claude" / "settings.json").read_text(encoding="utf-8"))

# --- rollback без бекапів
home4, bk4, env4 = fresh(ORIG)
rc, out = run(env4, "--rollback")
chk("rollback без бекапів: помилка, settings.json цілий", rc == 1 and (home4 / ".claude" / "settings.json").read_text(encoding="utf-8") == ORIG)

sys.exit(1 if F else 0)
