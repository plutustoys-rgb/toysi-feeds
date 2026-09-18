"""source_freshness.py — детектор ТИШІ для kandydaty-скриптів, що читають найновіший файл
з теки (RozetkaPay/ПриватБанк/… у документи_КОДВ/YYYY-MM/<Джерело>/).

НАВІЩО (незалежний аудитор, КОДВ_журнал.md «ДОПОВНЕННЯ 5», Д3, 2026-09-18): три входи КОДВ
мертві тижнями (RozetkaPay — 0 файлів за вересень при 17 банківських переказах; ПриватБанк —
з 11.08; EVA — з 12.09), а Windows-задачі, що їх запускають, звітують `LastTaskResult=0`
щодня. Скрипт жує старий файл і мовчки видає нуль кандидатів — це виглядає як «усе гаразд»,
а насправді вхід зупинився. Telegram-алерти самі по собі НЕ рішення (власник, 2026-09-17:
«вони губляться серед шуму») — тому цей модуль пише ПЕРСИСТЕНТНИЙ звіт
(документи_КОДВ/_mertvi_vhody.md), той самий патерн, що kandydaty_registry.py для відкритих
кандидатів, а не черговий пінг, який ніхто не читає.

ЯК ІНТЕГРУВАТИ (мінімально, у кінці main() скрипта, що читає найновіший файл з теки):
    import source_freshness as sf
    sf.check_and_record("RozetkaPay", str(DOCS_DIR / "*" / "RozetkaPay" / "*.xlsx"), max_stale_days=3)
    sf.write_report()

`check_and_record` сам вирішує freshness (mtime найновішого файлу за glob), пише стан у
`_dzherela_stan.json`. `write_report()` — окремий крок (як write_open_report у
kandydaty_registry.py), формує зведений `_mertvi_vhody.md` по ВСІХ джерелах, що коли-небудь
викликали check_and_record — не лише тому, що викликав саме зараз.
"""
import glob
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COWORK_DIR = Path(os.environ.get(
    "PLUTUS_COWORK_DIR", r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
STATE_PATH = COWORK_DIR / "документи_КОДВ" / "_dzherela_stan.json"
REPORT_PATH = COWORK_DIR / "документи_КОДВ" / "_mertvi_vhody.md"


def _load_state(path: Path = None) -> dict:
    path = path or STATE_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict, path: Path = None) -> None:
    path = path or STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def check_and_record(name: str, glob_pattern: str, max_stale_days: int, path: Path = None) -> dict:
    """Перевіряє mtime найновішого файлу за `glob_pattern`. Записує/оновлює стан джерела
    `name` у STATE_PATH. Повертає {"stale": bool, "age_days": float|None, "newest": str|None,
    "reason": "no_files"|"stale"|"fresh"}."""
    files = [f for f in glob.glob(glob_pattern) if not os.path.basename(f).startswith("~$")]
    state = _load_state(path)
    today = date.today().isoformat()

    if not files:
        entry = {"glob": glob_pattern, "max_stale_days": max_stale_days,
                  "newest": None, "age_days": None, "stale": True, "reason": "no_files",
                  "last_checked": today}
        state[name] = entry
        _save_state(state, path)
        return entry

    newest = max(files, key=os.path.getmtime)
    age_days = (datetime.now().timestamp() - os.path.getmtime(newest)) / 86400
    stale = age_days >= max_stale_days
    entry = {"glob": glob_pattern, "max_stale_days": max_stale_days,
              "newest": os.path.basename(newest), "age_days": round(age_days, 1), "stale": stale,
              "reason": "stale" if stale else "fresh", "last_checked": today}
    state[name] = entry
    _save_state(state, path)
    return entry


def write_report(path: Path = None, out_path: Path = None) -> Path:
    """Зведений звіт по ВСІХ джерелах, що коли-небудь реєструвались через check_and_record —
    не лише тому, яке щойно перевірилось. Свіжі джерела теж показані (щоб було видно, що
    моніторинг ЖИВИЙ, не лише список проблем)."""
    state = _load_state(path)
    out_path = out_path or REPORT_PATH
    today = date.today()

    stale_rows = []
    fresh_rows = []
    for name, e in sorted(state.items()):
        if e.get("reason") == "no_files":
            row = f"| {name} | — | — | 🔴 жодного файлу |"
            stale_rows.append(row)
        elif e.get("stale"):
            row = f"| {name} | {e.get('newest', '?')} | {e.get('age_days', '?')} | 🔴 старіший за поріг {e.get('max_stale_days', '?')} дн |"
            stale_rows.append(row)
        else:
            row = f"| {name} | {e.get('newest', '?')} | {e.get('age_days', '?')} | ✅ свіжий |"
            fresh_rows.append(row)

    lines = [
        f"# Мертві входи КОДВ — детектор тиші, {today.isoformat()}",
        "",
        "Автоматично зведено з усіх джерел, підключених до `source_freshness.py`. «Мертвий» —",
        "найновіший файл теки старіший за власний поріг джерела (не «нема кандидатів», а",
        "«вхід взагалі не оновлюється» — скрипт може жувати той самий старий файл роками й",
        "мовчки видавати нуль кандидатів, виглядаючи зеленим у Task Scheduler).",
        "",
        "| Джерело | Найновіший файл | Днів тому | Стан |",
        "|---|---|---|---|",
    ]
    if stale_rows:
        lines.extend(stale_rows)
    if fresh_rows:
        lines.extend(fresh_rows)
    if not stale_rows and not fresh_rows:
        lines.append("| — | — | — | Жодне джерело ще не перевірялось. |")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


if __name__ == "__main__":
    p = write_report()
    state = _load_state()
    n_stale = sum(1 for e in state.values() if e.get("stale"))
    print(f"[source_freshness] Звіт сформовано: {p} ({n_stale}/{len(state)} джерел мертві)")
