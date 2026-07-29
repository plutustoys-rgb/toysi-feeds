"""feed_pipeline_report.py — пише feed_pipeline_state.json після кожного
прогону run_feed_pipeline_vps.sh (VPS, feed-pipeline.timer) — джерело
правди для check_feed_pipeline_vps_status() у service_watchdog.py.

ЗАМІНЮЄ check_feed_pipeline_schedule() (моніторила історію запусків
update-feeds.yml через GitHub API) — після переведення пайплайну на VPS
(2026-07-27, знахідка аудиту pt6) той сигнал стає непридатним: schedule:
в update-feeds.yml закоментовано, тож "останній запуск" там більше не
оновлюється регулярно, і стара перевірка або хибно алармила б постійно,
або мовчала б про реальний стан VPS-пайплайну.

Викликається лише з run_feed_pipeline_vps.sh на VPS, не призначений для
прямого запуску.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feed_pipeline_state.json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True, choices=["ok", "degraded", "failed"],
                         help="ok = усі критичні кроки успішні; degraded = репрайсер/prom_feed_top "
                              "провалились, але фолбек дозволив публікацію; failed = публікація пропущена")
    parser.add_argument("--reason", default=None, help="причина для degraded/failed")
    args = parser.parse_args()

    state = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "last_status": args.status,
        "reason": args.reason,
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    print(f"[feed_pipeline_report] {args.status}" + (f" — {args.reason}" if args.reason else ""))


if __name__ == "__main__":
    main()
