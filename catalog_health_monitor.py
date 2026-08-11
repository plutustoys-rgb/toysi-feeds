"""catalog_health_monitor.py — щоденний монітор здоров'я Prom-каталогу.

ЧОМУ (2026-08-11, пряма претензія власника «чому це підняв я, а не система»):
ціль — у Prom ПОСТІЙНО ~6000 НАЯВНИХ КОНКУРЕНТНИХ товарів. Дрейф цієї метрики
(out-of-stock займають слоти тарифу, авто-заміна відстає) раніше ніхто не ловив
автоматично — власник побачив вручну (6116/6000 опубліковано, ~2016 не в наявності).
Цей монітор РАХУЄ метрику й АЛЕРТИТЬ у Telegram при дрейфі — щоб попереджала система.

ЛИШЕ ЧИТАННЯ + Telegram. Нічого не деактивує (це задача prom_catalog_sync.py);
перевикористовує його ж функції, щоб не дублювати логіку й не бити API двічі.
Деплой: копія в /opt/plutustoys, окремий systemd-таймер (щодня).
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from parser import fetch_toysi_catalog, assert_catalog_size_sane, CatalogSizeError
from generate_prom_feed_top import select_top_items, SELECT_COUNT
from prom_catalog_sync import (
    fetch_prom_products,
    supplement_cabinet_view,
    find_stale_external_ids,
    check_product_count_sane,
)
from telegram_notify import send_telegram_message

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
HISTORY_FILE = BASE_DIR / "catalog_health_history.jsonl"

TARGET = SELECT_COUNT                 # 6000 — ціль наявних конкурентних у каталозі
SELECTION_SHORTFALL_ALERT = 500       # алерт, якщо пул конкурентних наявних < TARGET - цього
STALE_BACKLOG_ALERT = 400             # алерт, якщо out-of-stock у слотах (видимих) перевищує це
ALERT_INTERVAL_HOURS = 24             # throttle: не частіше 1 алерту/добу


def _last_record() -> dict | None:
    """Останній валідний рядок історії (пропускаючи биті — стійко до частково
    записаного JSONL, той самий підхід, що meta_feed_coverage_monitor._load_last)."""
    if not HISTORY_FILE.exists():
        return None
    for line in reversed(HISTORY_FILE.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def _alert_due(now: datetime) -> bool:
    """True, якщо з моменту ОСТАННЬОГО алерту минуло > ALERT_INTERVAL_HOURS
    (або алертів ще не було) — щоб не спамити Telegram щопрогону."""
    last = _last_record()
    if not last or not last.get("alerted"):
        return True
    try:
        last_ts = datetime.fromisoformat(last["ts"])
    except (KeyError, ValueError):
        return True
    return now - last_ts > timedelta(hours=ALERT_INTERVAL_HOURS)


def evaluate(selection_size: int, published: int, stale: int, view_warning: str | None) -> list:
    """Повертає список причин для алерту (порожній = здоровий). Не мутує нічого."""
    reasons = []
    if selection_size < TARGET - SELECTION_SHORTFALL_ALERT:
        reasons.append(
            f"пул НАЯВНИХ КОНКУРЕНТНИХ товарів лише {selection_size} (ціль {TARGET}) — "
            f"бракує {TARGET - selection_size}: вітрина не заповнюється до 6000 живими товарами."
        )
    if view_warning:
        # Неповний зріз кабінету — stale недорахований, але сам факт сигналізуємо.
        reasons.append(f"неповний зріз кабінету Prom — {view_warning}")
    elif stale > STALE_BACKLOG_ALERT:
        reasons.append(
            f"{stale} out-of-stock товарів займають слоти тарифу й не замінені — "
            f"авто-заміна відстає. Почисти: prom_catalog_sync.py --apply --max-deactivations 300."
        )
    return reasons


def main() -> None:
    now = datetime.now()

    toysi_catalog = fetch_toysi_catalog()
    try:
        assert_catalog_size_sane(toysi_catalog)
    except CatalogSizeError as e:
        # Усічений (але валідний) фід Toysi → selection_size був би хибно низьким і
        # дав би ХИБНИЙ алерт про «бракує товарів». Краще пропустити прогін, ніж збрехати.
        print(f"[CatalogHealth] Toysi-каталог усічений ({e}) — пропускаю прогін, щоб не давати хибний алерт.",
              file=sys.stderr)
        return

    top_catalog = select_top_items(toysi_catalog)
    selection_size = len(top_catalog)
    desired_ids = {str(pid) for pid in top_catalog}
    toysi_ids = {str(pid) for pid in toysi_catalog}

    prom_products = fetch_prom_products()
    prom_products = supplement_cabinet_view(prom_products, desired_ids)
    published = len(prom_products)
    view_warning = check_product_count_sane(prom_products)
    stale = len(find_stale_external_ids(prom_products, desired_ids, toysi_ids))

    reasons = evaluate(selection_size, published, stale, view_warning)
    alerted = False
    if reasons and _alert_due(now):
        send_telegram_message(
            "🟠 Здоров'я Prom-каталогу — потрібна увага:\n\n" + "\n\n".join(f"• {r}" for r in reasons) +
            f"\n\n(пул наявних конкурентних: {selection_size}/{TARGET}; у кабінеті видно: {published}; "
            f"застарілих out-of-stock: {stale})"
        )
        alerted = True
    elif reasons:
        print(f"[CatalogHealth] Проблеми є, але Telegram-алерт пропущено — 24-год throttle: {reasons}",
              file=sys.stderr)

    record = {
        "ts": now.isoformat(),
        "selection_size": selection_size,
        "published": published,
        "stale": stale,
        "view_incomplete": bool(view_warning),
        "target": TARGET,
        "alerted": alerted,
    }
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[CatalogHealth] Не вдалось записати історію ({e})", file=sys.stderr)

    status = "АЛЕРТ" if alerted else ("проблеми (throttle)" if reasons else "OK")
    print(f"[CatalogHealth] {status}: пул наявних конкурентних {selection_size}/{TARGET}, "
          f"у кабінеті {published}, застарілих {stale}.")


if __name__ == "__main__":
    main()
