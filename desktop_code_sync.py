# -*- coding: utf-8 -*-
"""desktop_code_sync.py — підтягує ЗМЕРДЖЕНИЙ master у робочу копію, з якої працюють локальні Windows-задачі.

ЧОМУ (2026-10-06, знайдено при розкатці): десктопні задачі (PlutusToys_RozetkaLocalChain → run_rozetka_local.py, PlutusToys-CabinetAudit →
local_cabinet_audit.ps1, Checkbox/Graph6/NovaPay-архіватор) виконуються з `C:\\Users\\smach\\rozetka_agent`, а ця копія була на застарілій гілці
(docs/system-map-site-live, 36 комітів позаду master) і НІЧИМ не оновлювалась: аудитовані PR (стоп-бренди Rozetka, akty_zvirka,
kandydaty fee…) на десктоп не потрапляли. VPS має vps_code_sync.sh — десктоп такого механізму не мав.

БЕЗПЕЧНО: нічого не затирає, ніколи не падає (exit 0):
  • лише основна копія (`.git` — тека; у worktree `.git` — файл → пропуск) і лише гілка `master`;
  • лише коли відстежувані файли чисті (`git diff --quiet HEAD`); чужі незакомічені правки → пропуск з попередженням;
  • лише `merge --ff-only origin/master` (без merge-комітів/конфліктів).
Викликається на початку кожної локальної задачі (.ps1 і run_rozetka_local.py).
"""
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def sync(base: Path = BASE) -> str:
    """Повертає короткий статус-рядок (для логу задачі)."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}   # жодних діалогів credential-менеджера у фоновій задачі

    def _git(*args):
        return subprocess.run(["git", *args], cwd=base, capture_output=True, text=True, timeout=120, env=env)
    try:
        if not (base / ".git").is_dir():
            return "пропуск: це не основна копія (worktree або без .git)"
        branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch != "master":
            return f"пропуск: гілка «{branch}», очікується master — код НЕ оновлено (перемкнути вручну)"
        if _git("diff", "--quiet", "HEAD").returncode != 0:
            return "пропуск: є незакомічені зміни відстежуваних файлів — код НЕ оновлено (щоб нічого не затерти)"
        # lowSpeed*: «живе, але мертве» з'єднання обривається за ~30 с (subprocess timeout на Windows не вбиває дочірній git-remote-https; аудит #632)
        f = _git("-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=30", "fetch", "-q", "origin", "master")
        if f.returncode != 0:
            return f"пропуск: fetch не вдався ({f.stderr.strip()[:120]})"
        before = _git("rev-parse", "--short", "HEAD").stdout.strip()
        m = _git("merge", "-q", "--ff-only", "origin/master")
        if m.returncode != 0:
            return f"пропуск: fast-forward неможливий ({' '.join(m.stderr.split())[:300]})"
        after = _git("rev-parse", "--short", "HEAD").stdout.strip()
        return f"актуально: {after}" if before == after else f"оновлено {before} → {after}"
    except Exception as e:  # noqa: BLE001 — синхронізація ніколи не валить задачу
        return f"збій синхронізації ({type(e).__name__}: {str(e)[:120]}) — задача працює з наявним кодом"


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"[DesktopCodeSync] {sync()}")
    sys.exit(0)
