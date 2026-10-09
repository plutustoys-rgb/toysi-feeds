#!/usr/bin/env python3
"""system_map_driftcheck.py — запобіжник проти гниття SYSTEM_MAP.md (SSOT).

НАВІЩО (правило власника 2026-08-20): мапа ролей/автоматик гниє, коли її пишуть з пам'яті й ніхто
не звіряє. Цей скрипт діфить ЖИВУ систему проти машинно-читаного реєстру в SYSTEM_MAP.md (розділ 6)
і кричить, ЩО саме розійшлось — щоб мапа сама казала, коли збрехала (а не ми дізнавались постфактум).

ЩО ЗВІРЯЄ (за середовищем):
  • Windows (локальна машина) — таски `Get-ScheduledTask` проти `local_tasks` (мають ДІЯТИ) і
    `local_tasks_disabled` (навмисно вимкнені — код лишено, вмикається однією командою). Звіряється і
    ІМ'Я, і СТАН: таск, що в мапі «діє», а в системі `Disabled` (або навпаки), — це дрейф. Раніше
    перевірка бачила лише імена, тому вимкнений 22.09.2026 `PlutusToys_AgentWatch` лишався в мапі
    «робочим» і перевірка казала «збіг» (знайдено архітектурним аудитом 09.10.2026).
  • Linux (VPS) — systemd-юніти проти `vps_units` (лише імена).
Друкує: у мапі-але-не-живе (зникла автоматика?), живе-але-не-в-мапі (додали, не вписали в SSOT) і
розбіжність стану (мапа каже «діє», а таск вимкнений, чи навпаки).

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
                     "daily", "deadline", "watchdog", "catalog", "scan", "social",
                     "site-",   # "site-" (з дефісом): site-rebuild перевірка не бачила, поки слова не було (09.10.2026)
                     "driftcheck", "link-cache", "np-warehouse")   # сторож схеми, link-cache-validator, np-warehouse-sync — теж були невидимі
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


def parse_task_states(stdout: str) -> dict:
    """Рядки виду `Ім'я|Стан` → {ім'я: стан}. Порожні й без роздільника — пропускає."""
    states = {}
    for ln in stdout.splitlines():
        name, sep, state = ln.strip().partition("|")
        if sep and name.strip():
            states[name.strip()] = state.strip()
    return states


def live_windows_task_states() -> dict | None:
    """Живі Windows-таски PlutusToys* з їхнім СТАНОМ ({ім'я: Ready/Running/Disabled/…}) через
    PowerShell. None — не вдалося зняти."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-ScheduledTask | Where-Object { $_.TaskName -match 'Plutus' } "
             "| ForEach-Object { $_.TaskName + '|' + $_.State }"],
            capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"[drift] не зняв Windows-таски: {e}", file=sys.stderr)
        return None
    if r.returncode != 0:
        print(f"[drift] Get-ScheduledTask впав: {r.stderr[:200]}", file=sys.stderr)
        return None
    return parse_task_states(r.stdout)


def filter_vps_units(unit_files) -> set:
    """Імена unit-файлів → наші: за ключовими словами, без системних Ubuntu-юнітів. Тримає і .timer,
    і .service (базове ім'я без суфікса береться пізніше, при звірці з мапою). Окрема чиста функція —
    щоб фільтр тестувався без сервера: юніт, якого фільтр не бачить, дає хибне «У МАПІ Є, ЖИВОГО НЕМА»."""
    units = set()
    for name in unit_files:
        low = name.lower()
        if not name or low.startswith(VPS_UNIT_EXCLUDE_PREFIX):
            continue   # системні Ubuntu-юніти — не наші
        if any(k in low for k in VPS_UNIT_KEYWORDS):
            units.add(name)
    return units


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
    return filter_vps_units([ln.split()[0] for ln in r.stdout.splitlines() if ln.split()])


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


def _diff_local_tasks(active: set, disabled: set, states: dict, label: str = "Локальні таски") -> tuple:
    """Звіряє ІМЕНА і СТАН. `active` — мапа каже «діє», `disabled` — «навмисно вимкнено».
    Дрейф: нема в системі; є в системі, але не в мапі; мапа «діє», а таск Disabled; мапа «вимкнено»,
    а таск діє; ім'я в обох списках реєстру. Повертає (є_дрейф, повідомлення)."""
    live = set(states)
    msgs = []
    both = active & disabled
    if both:
        msgs.append(f"{label}: В РЕЄСТРІ В ОБОХ СПИСКАХ (діє І вимкнено) ({len(both)}): {sorted(both)}")
    missing = (active | disabled) - live
    if missing:
        msgs.append(f"{label}: У МАПІ Є, ЖИВОГО НЕМА ({len(missing)}): {sorted(missing)}")
    extra = live - (active | disabled)
    if extra:
        msgs.append(f"{label}: ЖИВЕ Є, У МАПІ НЕМА ({len(extra)}): {sorted(extra)}")
    wrong_off = sorted(n for n in (active & live) if states[n] == "Disabled")
    if wrong_off:
        msgs.append(f"{label}: МАПА КАЖЕ «ДІЄ», А ТАСК ВИМКНЕНО ({len(wrong_off)}): {wrong_off}")
    wrong_on = sorted(n for n in (disabled & live) if states[n] != "Disabled")
    if wrong_on:
        msgs.append(f"{label}: МАПА КАЖЕ «ВИМКНЕНО», А ТАСК ДІЄ ({len(wrong_on)}): {wrong_on}")
    if not msgs:
        print(f"[drift] {label}: ✅ збіг ({len(active)} діють + {len(disabled)} навмисно вимкнені = реєстр)")
        return False, []
    for m in msgs:
        print(f"[drift] ⚠️ {m}")
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
        active = set(reg.get("local_tasks", []))
        disabled = set(reg.get("local_tasks_disabled", []))
        states = live_windows_task_states()
        if states is None:
            result["error"] = "не зняв Windows-таски"
        else:
            result["checked"] = "local_tasks"
            result["drift"], msgs = _diff_local_tasks(active, disabled, states)
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
