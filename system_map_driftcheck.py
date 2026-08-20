#!/usr/bin/env python3
"""system_map_driftcheck.py — запобіжник проти гниття SYSTEM_MAP.md (SSOT).

НАВІЩО (правило власника 2026-08-20): мапа ролей/автоматик гниє, коли її пишуть з пам'яті й ніхто
не звіряє. Цей скрипт діфить ЖИВУ систему проти машинно-читаного реєстру в SYSTEM_MAP.md (розділ 6)
і кричить, ЩО саме розійшлось — щоб мапа сама казала, коли збрехала (а не ми дізнавались постфактум).

ЩО ЗВІРЯЄ (за середовищем):
  • Windows (локальна машина) — таски `Get-ScheduledTask` проти `local_tasks`.
  • Linux (VPS) — systemd-юніти проти `vps_units`.
Друкує: у мапі-але-не-живе (зникла автоматика?) і живе-але-не-в-мапі (додали, не вписали в SSOT).

ЗАПУСК:
    python system_map_driftcheck.py            # звірка поточного середовища
    python system_map_driftcheck.py --json      # машинний вивід
Код виходу: 0 — збіг; 1 — дрейф; 2 — не зміг зняти живий стан (не караємо як дрейф).

ПІДКЛЮЧЕННЯ: на кожній машині повісити на таймер (локально — Windows-таск; на VPS — systemd
oneshot+timer), щоб дрейф ловився сам і алертив (наступний крок, окремо).
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
MAP_FILE = BASE_DIR / "SYSTEM_MAP.md"
# ключові слова для фільтра юнітів VPS (ті самі, що в бандлі знаття)
VPS_UNIT_KEYWORDS = ("plutus", "feed", "order", "prom", "rozetka", "novapay",
                     "daily", "deadline", "watchdog", "catalog", "scan")
# СИСТЕМНІ юніти Ubuntu, які збігаються з keyword'ами (daily→apt-daily, catalog→systemd-journal-
# catalog-update) — це НЕ наші, drift-check їх ігнорує (інакше хибний «дрейф»; знайдено живо на VPS).
VPS_UNIT_EXCLUDE_PREFIX = ("apt-", "apt.", "systemd-", "fwupd", "logrotate", "man-db", "dpkg",
                           "e2scrub", "fstrim", "motd", "update-notifier", "ua-", "ua_", "snap.",
                           "phpsessionclean", "certbot", "unattended-upgrades")


def load_registry() -> dict:
    """Витягує JSON-реєстр із розділу 6 SYSTEM_MAP.md (перший ```json блок після маркера)."""
    if not MAP_FILE.exists():
        raise SystemExit(f"[drift] нема {MAP_FILE.name} — SSOT відсутній")
    text = MAP_FILE.read_text(encoding="utf-8")
    # беремо останній ```json ... ``` блок (машинний реєстр наприкінці файлу)
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not blocks:
        raise SystemExit("[drift] у SYSTEM_MAP.md нема машинного JSON-реєстру (розділ 6)")
    try:
        return json.loads(blocks[-1])
    except json.JSONDecodeError as e:
        raise SystemExit(f"[drift] JSON-реєстр у SYSTEM_MAP.md не парситься: {e}")


def live_windows_tasks() -> set | None:
    """Живі Windows-таски PlutusToys* через PowerShell. None — не вдалося зняти."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-ScheduledTask | Where-Object { $_.TaskName -match 'Plutus' } "
             "| Select-Object -ExpandProperty TaskName"],
            capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"[drift] не зняв Windows-таски: {e}", file=sys.stderr)
        return None
    if r.returncode != 0:
        print(f"[drift] Get-ScheduledTask впав: {r.stderr[:200]}", file=sys.stderr)
        return None
    return {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}


def live_vps_units() -> set | None:
    """Живі systemd-юніти (unit-files) за ключовими словами. None — не вдалося зняти."""
    try:
        r = subprocess.run(["systemctl", "list-unit-files", "--no-pager", "--no-legend"],
                           capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"[drift] не зняв systemd-юніти: {e}", file=sys.stderr)
        return None
    if r.returncode != 0:
        print(f"[drift] systemctl впав: {r.stderr[:200]}", file=sys.stderr)
        return None
    units = set()
    for ln in r.stdout.splitlines():
        name = ln.split()[0] if ln.split() else ""
        low = name.lower()
        if not name or low.startswith(VPS_UNIT_EXCLUDE_PREFIX):
            continue   # системні Ubuntu-юніти — не наші
        if any(k in low for k in VPS_UNIT_KEYWORDS):
            # тримаємо і .timer, і .service — але для звірки з мапою беремо базове ім'я без суфікса
            units.add(name)
    return units


def _diff(declared: set, live: set, label: str) -> tuple:
    """Друкує розбіжності; повертає (є_дрейф: bool, повідомлення: list[str])."""
    missing = declared - live      # у мапі є, живого нема → зникло/переіменовано
    extra = live - declared         # живе є, у мапі нема → додали, не вписали в SSOT
    if not missing and not extra:
        print(f"[drift] {label}: ✅ збіг ({len(live)} живих = реєстр)")
        return False, []
    msgs = []
    if missing:
        m = f"{label}: У МАПІ Є, ЖИВОГО НЕМА ({len(missing)}): {sorted(missing)}"
        print(f"[drift] ⚠️ {m}"); msgs.append(m)
    if extra:
        m = f"{label}: ЖИВЕ Є, У МАПІ НЕМА ({len(extra)}): {sorted(extra)}"
        print(f"[drift] ⚠️ {m}"); msgs.append(m)
    return True, msgs


def _alert(text: str) -> None:
    """Best-effort Telegram-алерт (щоб дрейф сам казав про себе). Збій алерту не валить перевірку."""
    try:
        from telegram_notify import send_telegram_message
        send_telegram_message(text)
    except Exception as e:  # noqa: BLE001
        print(f"[drift] Telegram-алерт не надіслано (не критично): {e}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description="Звірка SYSTEM_MAP.md проти живої системи.")
    ap.add_argument("--json", action="store_true", help="машинний вивід")
    ap.add_argument("--alert", action="store_true", help="слати Telegram-алерт при дрейфі (для таймера)")
    a = ap.parse_args()

    reg = load_registry()
    is_windows = os.name == "nt" or sys.platform.startswith("win")
    result = {"env": "windows" if is_windows else "linux", "drift": False, "checked": None, "error": None}
    msgs = []

    if is_windows:
        declared = set(reg.get("local_tasks", []))
        live = live_windows_tasks()
        if live is None:
            result["error"] = "не зняв Windows-таски"
        else:
            result["checked"] = "local_tasks"
            result["drift"], msgs = _diff(declared, live, "Локальні таски")
    else:
        declared = set(reg.get("vps_units", []))
        if any("__PROVISIONAL__" in d for d in declared):
            print("[drift] VPS-реєстр у SSOT ще ПРОВІЗОРНИЙ — звірку пропущено. Звірити бандлом і оновити розділ 6.")
            result["error"] = "vps_units provisional"
        else:
            live = live_vps_units()
            if live is None:
                result["error"] = "не зняв systemd-юніти"
            else:
                # мапа може тримати короткі імена; звіряємо за входженням
                live_base = {u.rsplit(".", 1)[0] for u in live}
                result["checked"] = "vps_units"
                result["drift"], msgs = _diff(declared, live_base, "VPS-юніти")

    if a.alert and result["drift"] and msgs:
        _alert("🗺️ SYSTEM_MAP дрейф (" + result["env"] + "):\n" + "\n".join(msgs)
               + "\n→ онови SYSTEM_MAP.md (реальність змінилась).")

    if a.json:
        print(json.dumps(result, ensure_ascii=False))
    # exit: 0 збіг, 1 дрейф, 2 не зняв живе
    if result["error"] and not result["checked"]:
        sys.exit(2)
    sys.exit(1 if result["drift"] else 0)


if __name__ == "__main__":
    main()
