"""
weekly_balance_digest.py — тижневий дайджест ТЕНДЕНЦІЇ балансів кабінетів із
balance_history.jsonl (наповнює eva_cabinet_scraper.py та майбутні скрейпери).

НАВІЩО (2026-08-04, рішення власника «без телеграма, у папку» + «тижнева тенденція»):
щоб бачити ДИНАМІКУ за тиждень (баланс на початок/кінець, мін/макс, зміна), а не лише
щоденні числа. Пише .md у спільну Cowork-папку (AUDIT_REPORT_DIR) — БЕЗ Telegram.
Порогів/алертів НЕ ставимо — накопичуємо тенденцію (рішення власника 2026-08-01).

Запуск (напр. раз на тиждень з Task Scheduler, або щодня — перезапише сьогоднішній):
    python weekly_balance_digest.py
"""
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
# Той самий каталог, що й аудитори/скрейпер (AUDIT_REPORT_DIR → Cowork-папка локально).
OUT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or (BASE_DIR / "reports"))
HISTORY_FILE = OUT_DIR / "balance_history.jsonl"
WINDOW_DAYS = 7


def load_rows() -> list:
    """Читає balance_history.jsonl. Відсутній файл / биті рядки → пропускаємо
    (не валимо: краще частковий дайджест, ніж крах)."""
    if not HISTORY_FILE.exists():
        return []
    rows = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def build_digest(rows: list, now: datetime) -> str:
    week_ago = now - timedelta(days=WINDOW_DAYS)
    by_platform: dict = defaultdict(list)
    for r in rows:
        ts = r.get("ts")
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            continue
        if t >= week_ago:
            by_platform[str(r.get("platform") or "?")].append((t, r))

    lines = [f"# Тижневий дайджест балансів — {now.strftime('%Y-%m-%d %H:%M')}",
             f"_Вікно: останні {WINDOW_DAYS} днів. Пороги не ставимо — лише тенденція._", ""]
    if not by_platform:
        lines.append(f"_За останні {WINDOW_DAYS} днів у balance_history.jsonl немає записів "
                     f"(скрейпери ще не наповнили / сесія не створена)._")
        return "\n".join(lines) + "\n"

    for platform, entries in sorted(by_platform.items()):
        entries.sort(key=lambda e: e[0])
        last = entries[-1][1]

        # EVA пише balance_total, Toysi — deposit; беремо те, що є.
        def _amt(r):
            v = r.get("balance_total")
            return v if v is not None else r.get("deposit")
        totals = [_amt(e[1]) for e in entries if _amt(e[1]) is not None]
        label = "Депозит" if platform == "toysi" else "Баланс"
        lines.append(f"## {platform.upper()} ({len(entries)} записів за тиждень)")
        if totals:
            change = round(totals[-1] - totals[0], 2)
            arrow = "▲" if change > 0 else ("▼" if change < 0 else "=")
            lines.append(f"- {label}: {totals[0]} → {totals[-1]} ₴  ({arrow} {change:+.2f})")
            lines.append(f"- Мін / Макс за тиждень: {min(totals)} / {max(totals)} ₴")
        else:
            lines.append(f"- {label}: немає числових значень за тиждень")
        if any(last.get(k) is not None for k in ("active", "moderation", "errors")):
            lines.append(f"- Наповненість (останнє): активні {last.get('active')}, "
                         f"модерація {last.get('moderation')}, з помилками {last.get('errors')}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    now = datetime.now()
    rows = load_rows()
    digest = build_digest(rows, now)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"weekly_balance_digest_{now.strftime('%Y-%m-%d')}.md"
    out_path.write_text(digest, encoding="utf-8")
    print(f"[WeeklyDigest] Дайджест збережено: {out_path}")


if __name__ == "__main__":
    main()
