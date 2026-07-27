"""Пише vps_code_sync_state.json після кожного прогону vps_code_sync.sh —
єдине джерело правди для check_autodeploy_status() у service_watchdog.py
(замінює колишню SHA256-звірку .py-файлів проти origin/master, яка
структурно ставала зайвою, щойно /opt/plutustoys перетворився на
справжній git-клон з автопулом).

Викликається лише з vps_code_sync.sh на VPS, не призначений для прямого
запуску.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vps_code_sync_state.json")


def _load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True, choices=["ok", "failed"])
    parser.add_argument("--commit", required=True, help="поточний HEAD після спроби синхронізації")
    parser.add_argument("--changed", default="", help="git diff --name-only, по рядку на файл")
    parser.add_argument("--reason", default=None, help="причина невдачі (лише для --status failed)")
    args = parser.parse_args()

    state = _load_state()
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    state["last_status"] = args.status
    state["last_commit"] = args.commit
    state["reason"] = args.reason
    state["changed_files"] = [line for line in args.changed.splitlines() if line.strip()]

    if args.status == "ok":
        state["last_success_commit"] = args.commit

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    print(f"[vps_code_sync_report] {args.status}: {args.commit[:8]}"
          + (f" — {args.reason}" if args.reason else ""))


if __name__ == "__main__":
    main()
