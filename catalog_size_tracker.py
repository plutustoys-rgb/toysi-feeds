"""
catalog_size_tracker.py — трек наповненості вітрини по кожному майданчику
(STATUS 2026-07-31, пряме прохання власника: «дивитися по кількості товарів на
вітрині... з трендом і алертом на падіння»).

НАВІЩО (окремо від circuit breaker catalog-синку): `prom_catalog_sync.py` ловить
РІЗКИЙ обвал за ОДИН прогін. Цей трекер веде ЧАСОВИЙ РЯД кількості товарів у фідах
і алертить не лише на різке падіння день-у-день, а й на ПОВІЛЬНЕ, непомітне за один
прогін просідання за дні/тижні, та на падіння нижче цільового порога.

ДЖЕРЕЛО — опубліковані фіди (feeds/*.xml), НЕ кабінет: EVA не має API кількості
активних, а кабінет не показує лічильник «всього» (перевірено 2026-08-01, лише
пагінація 25/стор). Фід — те, що МИ реально відправляємо на майданчик, рахується
надійно й без крихкого скрейпінгу. Для EVA окремо: available="true" (вітрина) і
available="false" (деактивовані OOS, PR #209).

Стан — catalog_size_history.jsonl (локально на VPS, у .gitignore; один JSON-рядок
на прогін). Алерт — через telegram_notify (пише і в Telegram, і в reports/).
Запуск — окремим кроком після генерації/публікації фідів (VPS-таймер).
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from telegram_notify import send_telegram_message

BASE_DIR = Path(__file__).parent
HISTORY_FILE = BASE_DIR / "catalog_size_history.jsonl"

# Фіди для підрахунку. Значення — шлях відносно BASE_DIR.
FEEDS = {
    "prom": "feeds/prom_feed_top.xml",
    "eva": "feeds/eva_feed.xml",
    "rozetka": "feeds/rozetka_feed.xml",
}

# Цільова наповненість (для алерту «нижче цілі»). Prom — топ-6000; EVA — реальний
# валідний пул (~8865 при stock>2, а не EVA_TARGET_SIZE=15000, що навмисно завищена
# стеля); Rozetka — заморожений статичний список (2000). Пороги — М'ЯКІ (алерт-
# орієнтир), не жорсткі: краще попередити зарано, ніж пропустити просідання.
TARGETS = {"prom": 6000, "eva": 8000, "rozetka": 2000}

# Відносне падіння між ДВОМА сусідніми записами (день-у-день / прогін-у-прогін),
# що вважається суттєвим.
DROP_ALERT_FRACTION = 0.15  # -15%

# Абсолютний «підлоговий» поріг — нижче нього алерт незалежно від тренду
# (вітрина суттєво спорожніла відносно цілі). ~50% цілі.
FLOORS = {"prom": 3000, "eva": 4000, "rozetka": 1000}

_OFFER_RE = re.compile(r"<offer\b")
_OFFER_UNAVAILABLE_RE = re.compile(r'<offer\b[^>]*available\s*=\s*"false"')


def count_feed(path: Path) -> dict:
    """Рахує offer'и у фіді. Розрізняє:
      - `absent=True` — файл ВІДСУТНІЙ у цьому середовищі (напр. rozetka_feed.xml на
        VPS, бо Rozetka генерує GH Actions, не VPS-пайплайн) → трекер його ПРОПУСКАЄ,
        а НЕ алертить (це не «вітрина зникла», це «не генерується тут»);
      - `missing=True` (при absent=False) — файл Є, але 0 offer'ів → РЕАЛЬНЕ спорожніння
        (для prom/eva restore-фолбек тримає останню версію, тож 0 = справжня проблема)."""
    if not path.exists():
        return {"total": 0, "available": 0, "unavailable": 0, "missing": True, "absent": True}
    try:
        xml = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"total": 0, "available": 0, "unavailable": 0, "missing": True, "absent": False}
    total = len(_OFFER_RE.findall(xml))
    unavailable = len(_OFFER_UNAVAILABLE_RE.findall(xml))
    return {
        "total": total,
        "available": total - unavailable,
        "unavailable": unavailable,
        "missing": total == 0,
        "absent": False,
    }


def load_last_record() -> dict | None:
    """Останній запис історії (для порівняння тренду). None, якщо історії ще нема
    чи файл пошкоджений (перший прогін — просто без порівняння день-у-день)."""
    try:
        lines = HISTORY_FILE.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def append_record(record: dict) -> None:
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[catalog_size] Не вдалось дописати історію: {e}", file=sys.stderr)


def evaluate(counts: dict, last: dict | None) -> list:
    """Формує список рядків-алертів. counts — {market: {available,...}}, last —
    попередній запис історії (або None). Алерт по майданчику, якщо: фід зник
    (missing) АБО available нижче FLOOR АБО падіння > DROP_ALERT_FRACTION відносно
    попереднього available. «Вітрина» для EVA — саме available (не total, бо
    total включає деактивовані OOS з available='false')."""
    alerts = []
    last_counts = (last or {}).get("counts", {})
    for market, c in counts.items():
        if c.get("absent"):
            continue  # фід не генерується в цьому середовищі (напр. rozetka на VPS) — не алертимо
        now_avail = c["available"]
        floor = FLOORS.get(market, 0)
        target = TARGETS.get(market, 0)

        if c.get("missing"):
            alerts.append(f"🛑 {market.upper()}: фід ПОРОЖНІЙ/ВІДСУТНІЙ (0 товарів) — вітрина зникла!")
            continue
        if now_avail < floor:
            alerts.append(
                f"🔻 {market.upper()}: {now_avail} товарів на вітрині — нижче підлогового порога "
                f"{floor} (ціль {target})."
            )
        prev_avail = (last_counts.get(market) or {}).get("available")
        if isinstance(prev_avail, int) and prev_avail > 0:
            drop = (prev_avail - now_avail) / prev_avail
            if drop >= DROP_ALERT_FRACTION:
                alerts.append(
                    f"📉 {market.upper()}: вітрина впала {prev_avail} → {now_avail} "
                    f"(−{drop * 100:.0f}% з минулого прогону)."
                )
    return alerts


def run() -> None:
    counts = {market: count_feed(BASE_DIR / rel) for market, rel in FEEDS.items()}
    last = load_last_record()

    record = {"timestamp": datetime.now().isoformat(timespec="seconds"), "counts": counts}
    append_record(record)

    summary = " | ".join(
        f"{m.upper()}: " + (
            "пропущено (не генерується тут)" if c.get("absent")
            else f"{c['available']}" + (f" (+{c['unavailable']} деактив.)" if c["unavailable"] else "")
        )
        for m, c in counts.items()
    )
    print(f"[catalog_size] Наповненість вітрини: {summary}")

    alerts = evaluate(counts, last)
    if alerts:
        message = "📊 Наповненість вітрини — увага:\n" + "\n".join(alerts) + f"\n\nПоточно: {summary}"
        print(f"[catalog_size] {message}", file=sys.stderr)
        send_telegram_message(message)
    else:
        print("[catalog_size] Усі майданчики в межах норми (без алертів).")


if __name__ == "__main__":
    run()
