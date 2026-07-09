"""
reconcile_revenue.py — звіряє замовлення в локальній БД (orders.db,
orders_watcher.py/order_router.py) з тим, що показує Prom Orders API за
той самий період.

Знаходить:
  - замовлення в Prom API, яких немає в БД (orders_watcher.py міг
    пропустити — простій сервісу, помилка мережі тощо)
  - замовлення в БД (platform=prom), яких Prom API за цей період більше
    не показує (скасовано/змінено на боці Prom?)
  - розбіжності суми замовлення (сума, збережена в БД при отриманні,
    проти суми, яку Prom API показує зараз)

Rozetka НЕ звіряється — Seller API ще не підключено (orders_watcher.py,
fetch_new_orders_rozetka: NotImplementedError без ключа).

Пише reconciliation_report_YYYY-MM-DD.md у спільну папку звітів.

Запуск:
    python reconcile_revenue.py                   # за вчора (00:00-23:59)
    python reconcile_revenue.py --date 2026-07-08  # за конкретну добу
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from orders_db import get_connection, init_db
from orders_watcher import PROM_API_URL, REQUEST_TIMEOUT, _parse_prom_price

# Консоль Windows (cp1251) інакше показує кирилицю як мотлох (те саме в daily_report.py).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

PROM_API_KEY = os.environ.get("PROM_API_KEY", "")

REPORT_DIR = Path(r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya")

AMOUNT_TOLERANCE = 0.01  # грн — округлення при парсингу ціни, не справжня розбіжність


def fetch_prom_orders_for_period(date_from: str, date_to: str) -> list:
    """Усі замовлення Prom за період (на відміну від orders_watcher.
    fetch_new_orders_prom(), якому потрібні лише status=pending — тут
    навмисно без фільтра status, щоб бачити геть усі замовлення для звірки).

    ПОКИ НЕ ПЕРЕВІРЕНО на реальному Prom API: чи саме date_from/date_to —
    точні назви GET-параметрів і чи формат дати (YYYY-MM-DD) той, що очікує
    /orders/list (за публічною документацією public-api.docs.prom.ua), і чи
    пагінація через last_id (id останнього замовлення попередньої сторінки,
    той самий підхід, що для звичайного опитування) справді ловить усі
    замовлення за період. Перший реальний запуск варто звірити вручну з
    кабінетом Prom."""
    if not PROM_API_KEY:
        print("[Reconcile] PROM_API_KEY не задано — звірка з Prom API неможлива без ключа.", file=sys.stderr)
        return []

    orders = []
    last_id = None
    while True:
        params = {"date_from": date_from, "date_to": date_to, "limit": 100}
        if last_id is not None:
            params["last_id"] = last_id
        try:
            response = requests.get(
                f"{PROM_API_URL}/orders/list",
                headers={"Authorization": f"Bearer {PROM_API_KEY}"},
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"[Reconcile] Помилка з'єднання з Prom API: {e}", file=sys.stderr)
            break

        try:
            data = response.json()
        except ValueError:
            print(f"[Reconcile] Невалідна відповідь Prom API (не JSON): {response.text[:300]}", file=sys.stderr)
            break

        page = data.get("orders", [])
        if not page:
            break
        orders.extend(page)
        if len(page) < 100:
            break
        last_id = page[-1]["id"]

    return orders


def _prom_order_total(order: dict) -> float:
    return sum(
        _parse_prom_price(p.get("price")) * int(p.get("quantity") or 1)
        for p in order.get("products", [])
    )


def _db_orders_for_period(conn, date_from: str, date_to: str) -> dict:
    """{order_id: сума} для платформи prom, за created_at в БД."""
    rows = conn.execute(
        "SELECT order_id, items FROM orders WHERE platform = 'prom' AND created_at >= ? AND created_at < ?",
        (date_from, date_to),
    ).fetchall()
    result = {}
    for row in rows:
        items = json.loads(row["items"])
        result[row["order_id"]] = sum(item.get("price", 0) * item.get("qty", 1) for item in items)
    return result


def build_reconciliation(date_from: str, date_to: str) -> str:
    init_db()
    with get_connection() as conn:
        db_orders = _db_orders_for_period(conn, date_from, date_to)

    lines = [f"# Звірка виручки Prom — {date_from}\n"]
    lines.append(f"\nЗамовлень у БД (platform=prom): {len(db_orders)}")

    if not PROM_API_KEY:
        lines.append(
            "\n\n⚠️ PROM_API_KEY не задано — звірка з Prom API не виконана, "
            "показані лише дані з локальної БД."
        )
        return "".join(lines)

    prom_orders = fetch_prom_orders_for_period(date_from, date_to)
    prom_by_id = {str(o["id"]): _prom_order_total(o) for o in prom_orders}

    missing_in_db   = sorted(set(prom_by_id) - set(db_orders))
    missing_in_prom = sorted(set(db_orders) - set(prom_by_id))
    common_ids      = set(prom_by_id) & set(db_orders)
    mismatched = [
        (oid, db_orders[oid], prom_by_id[oid])
        for oid in sorted(common_ids)
        if abs(db_orders[oid] - prom_by_id[oid]) > AMOUNT_TOLERANCE
    ]

    lines.append(f"\nЗамовлень у Prom API: {len(prom_by_id)}")

    lines.append("\n\n## Пропущено orders_watcher.py (є в Prom, немає в БД)")
    if missing_in_db:
        for oid in missing_in_db:
            lines.append(f"\n- {oid}: {prom_by_id[oid]:.2f} грн")
    else:
        lines.append("\nНемає — усі замовлення Prom потрапили в БД.")

    lines.append("\n\n## Є в БД, немає в Prom API за цей період (скасовано/змінено на боці Prom?)")
    if missing_in_prom:
        for oid in missing_in_prom:
            lines.append(f"\n- {oid}: {db_orders[oid]:.2f} грн")
    else:
        lines.append("\nНемає.")

    lines.append("\n\n## Розбіжність суми (те саме замовлення, різна сума)")
    if mismatched:
        for oid, db_total, prom_total in mismatched:
            lines.append(
                f"\n- {oid}: БД {db_total:.2f} грн, Prom API зараз {prom_total:.2f} грн "
                f"(різниця {prom_total - db_total:+.2f} грн)"
            )
    else:
        lines.append("\nНемає розбіжностей серед спільних замовлень.")

    total_prom = sum(prom_by_id.values())
    total_db   = sum(db_orders.values())
    lines.append("\n\n## Підсумок")
    lines.append(f"\nВиручка за Prom API: {total_prom:.2f} грн")
    lines.append(f"\nВиручка за БД: {total_db:.2f} грн")
    lines.append(f"\nРізниця: {total_prom - total_db:+.2f} грн")

    return "".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None, help="Доба YYYY-MM-DD для звірки (default: вчора)")
    args = ap.parse_args()

    day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today() - timedelta(days=1)
    date_from = day.isoformat()
    date_to = (day + timedelta(days=1)).isoformat()

    report = build_reconciliation(date_from, date_to)
    print(report)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"reconciliation_report_{day.isoformat()}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\n[Reconcile] Звіт збережено: {out_path}")


if __name__ == "__main__":
    main()
