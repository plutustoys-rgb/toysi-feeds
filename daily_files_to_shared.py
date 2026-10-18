# -*- coding: utf-8 -*-
"""daily_files_to_shared.py — щоденна доставка файлів VPS → спільна тека для SEO (лише читання з VPS; замість ручного копіювання Кодом; запит SEO 16.10).

З /opt/plutustoys/reports/gmc/ беремо НАЙНОВІШИЙ набір кожного типу (gmc_status_*.csv, gmc_summary_*.txt, gmc_account_settings_*.json, gmc_settings_changes_*.txt, googlebot_daily_*.csv, googlebot_summary_*.txt, gsc_*.csv) і кладемо в
`SEO_метрики/<дата>/` спільної теки (перекачуємо щоразу: файли невеликі, актуальність важливіша). Якщо в gmc_summary є рядок «🚩» (approved упав / нова причина) — один запис `## [Автомат → SEO] GMC: прапорець <дата>` угорі SEO_CHANNEL.md (вставка перед першим рядком, що ПОЧИНАЄТЬСЯ з «## »; один на добу; без Telegram).
Доступ: ssh-ключ плутустойс (як у сесій Коду), BatchMode, timeout; недоступно → повідомлення й код 0 (не валить MarketplaceActions). Крок 5 у run_marketplace_actions.py."""
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SHARED = Path(os.environ.get("SHARED_DIR", r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
KEY = Path(os.environ.get("VPS_SSH_KEY", str(Path.home() / ".ssh" / "plutustoys_vps")))
HOST = "root@45.94.157.4"
REMOTE_DIR = "/opt/plutustoys/reports/gmc"
PATTERNS = ("gmc_status_", "gmc_summary_", "gmc_account_settings_", "gmc_settings_changes_", "googlebot_daily_", "googlebot_summary_", "gsc_query_page_", "gsc_daily_", "gsc_sitemaps_", "gsc_inspect_", "eva_full_import_", "gsc_weekly_summary_", "gsc_inspect_summary_", "gsc_verification_")
MARK = "[Автомат → SEO] GMC: прапорець"
MARK_GSC = "[Автомат → SEO] GSC: прапорець"
FIXED_FILES = ("gsc_inspect_history.csv",)          # накопичувальні файли без дати в імені (історія «перший день проіндексовано»)


def latest_per_pattern(names: list) -> list:
    """З переліку імен (gmc_status_2026-10-16.csv …) — найновіший файл кожного шаблону."""
    best = {}
    for n in names:
        for p in PATTERNS:
            m = re.fullmatch(re.escape(p) + r"(\d{4}-\d{2}-\d{2})\.(?:csv|txt|json|md)", n)           # лише справжні файли: без .tmp, пробілів, ;
            if m and (p not in best or m.group(1) > best[p][0]):
                best[p] = (m.group(1), n)
    return [v[1] for v in best.values()]


def flags_in_summary(text: str) -> list:
    """Рядки прапорців із gmc_summary (починаються з «  🚩»)."""
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip().startswith("🚩")]


def post_flag(flags: list, day: str, channel: Path, mark: str = MARK) -> bool:
    try:
        with open(channel, encoding="utf-8", newline="") as _f:
            s = _f.read()
    except OSError:
        return False
    head = f"## {mark} {day}"
    if re.search(r"(?m)^## " + re.escape(mark) + " " + re.escape(day), s):
        return False
    nl = "\r\n" if "\r\n" in s else "\n"
    entry = (head + "\n" + "\n".join(flags) + f"\nДжерело: щоденний gmc_summary ({day}); порівняння з попереднім днем — факт зміни, без порогів. Файли — SEO_метрики/{day}/. Дія — SEO.\n\n").replace("\n", nl)
    m = re.search(r"(?m)^## ", s)
    new = (s[:m.start()] + entry + s[m.start():]) if m else (s + nl + entry)
    try:
        tmp = channel.with_name(channel.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as _f:
            _f.write(new)
        os.replace(tmp, channel)
    except OSError:
        return False
    return True


def _ssh(cmd: str, timeout: int = 60):
    return subprocess.run(["ssh", "-i", str(KEY), "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", HOST, cmd], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def main() -> int:
    if not KEY.exists():
        print(f"[daily_files] нема ssh-ключа {KEY}: крок пропущено")
        return 0
    try:
        r = _ssh(f"ls -1 {REMOTE_DIR}")
        if r.returncode != 0:
            print(f"[daily_files] ls на VPS не вдалось (код {r.returncode}): крок пропущено", file=sys.stderr)
            return 0
        names = latest_per_pattern([x.strip() for x in r.stdout.splitlines() if x.strip()])
        day = date.today().isoformat()
        out = SHARED / "SEO_метрики" / day
        out.mkdir(parents=True, exist_ok=True)
        got = []
        names = list(names) + [x for x in FIXED_FILES if x in {y.strip() for y in r.stdout.splitlines()}]
        for n in names:
            dst = out / n
            c = subprocess.run(["scp", "-q", "-i", str(KEY), "-o", "BatchMode=yes", f"{HOST}:{REMOTE_DIR}/{n}", str(dst)], capture_output=True, text=True, timeout=120)
            if c.returncode == 0:
                got.append(n)
        print(f"[daily_files] {day}: скопійовано {len(got)} з {len(names)}: {', '.join(got)}")
        ver = next((out / n for n in got if n.startswith("gsc_verification_")), None)
        if ver:
            vf = flags_in_summary(ver.read_text(encoding="utf-8"))
            if vf:
                print("[daily_files] прапорці GSC:", "запис у SEO_CHANNEL додано" if post_flag(vf, ver.stem.replace("gsc_verification_", ""), SHARED / "SEO_CHANNEL.md", MARK_GSC) else "уже є за цю дату / канал недоступний")
        summ = next((out / n for n in got if n.startswith("gmc_summary_")), None)
        if summ:
            fl = flags_in_summary(summ.read_text(encoding="utf-8"))
            if fl:
                print("[daily_files] прапорці GMC:", "запис у SEO_CHANNEL додано" if post_flag(fl, summ.stem.replace("gmc_summary_", ""), SHARED / "SEO_CHANNEL.md") else "уже є за цю дату / канал недоступний")
    except Exception as e:  # noqa: BLE001
        print(f"[daily_files] збій: {type(e).__name__}: {str(e)[:150]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
