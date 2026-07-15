"""
reconcile_revenue.py — звіряє замовлення в локальній БД (orders.db,
orders_watcher.py/order_router.py) з тим, що показує API маркетплейсу за
той самий період — для Prom і Rozetka (з 2026-07-15).

Знаходить, для кожної платформи:
  - замовлення в API маркетплейсу, яких немає в БД (orders_watcher.py міг
    пропустити — простій сервісу, помилка мережі тощо)
  - замовлення в БД, яких API за цей період більше не показує
    (скасовано/змінено на боці маркетплейсу?)
  - розбіжності суми замовлення (сума, збережена в БД при отриманні,
    проти суми, яку API показує зараз)

Пише reconciliation_report_YYYY-MM-DD.md у спільну папку звітів (один
файл, по секції на кожну платформу).

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

import rozetka_client
from orders_db import get_connection, init_db
from orders_watcher import PROM_API_URL, REQUEST_TIMEOUT, _parse_prom_price

# Консоль Windows (cp1251) інакше показує кирилицю як мотлох (те саме в daily_report.py).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

PROM_API_KEY     = os.environ.get("PROM_API_KEY", "")
ROZETKA_USERNAME = os.environ.get("ROZETKA_USERNAME", "")
ROZETKA_PASSWORD = os.environ.get("ROZETKA_PASSWORD", "")

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


def fetch_rozetka_orders_for_period(date_from: str, date_to: str) -> list:
    """Усі замовлення Rozetka за період — rozetka_client.
    fetch_orders_by_date_range() (усі три type: В обробці/Успішні/Неуспішні)."""
    if not (ROZETKA_USERNAME and ROZETKA_PASSWORD):
        print(
            "[Reconcile] ROZETKA_USERNAME/ROZETKA_PASSWORD не задано — звірка з Rozetka API неможлива.",
            file=sys.stderr,
        )
        return []
    try:
        return rozetka_client.fetch_orders_by_date_range(date_from, date_to)
    except rozetka_client.RozetkaAPIError as e:
        print(f"[Reconcile] {e}", file=sys.stderr)
        return []


def _rozetka_order_total(order: dict) -> float:
    """order.amount — сума замовлення по товарах (без доставки), той самий
    зміст, що order_total у orders_db (лише товари, без вартості доставки) —
    узгоджено з _db_orders_for_period() нижче, який теж рахує лише
    price*qty з items, без доставки."""
    try:
        return float(order.get("amount", 0))
    except (TypeError, ValueError):
        return 0.0


def _db_orders_for_period(conn, platform: str, date_from: str, date_to: str) -> dict:
    """{order_id: сума} для заданої платформи, за created_at в БД."""
    rows = conn.execute(
        "SELECT order_id, items FROM orders WHERE platform = ? AND created_at >= ? AND created_at < ?",
        (platform, date_from, date_to),
    ).fetchall()
    result = {}
    for row in rows:
        items = json.loads(row["items"])
        result[row["order_id"]] = sum(item.get("price", 0) * item.get("qty", 1) for item in items)
    return result


def _build_platform_section(
    platform: str, platform_label: str, conn, date_from: str, date_to: str,
    creds_available: bool, api_orders: list, api_order_total_fn,
) -> str:
    """Спільна логіка звірки БД проти API маркетплейсу — одна функція для
    Prom і Rozetka замість дублювання (обидві платформи звіряються
    однаково: пропущені/зайві замовлення + розбіжність суми)."""
    db_orders = _db_orders_for_period(conn, platform, date_from, date_to)

    lines = [f"\n\n# Звірка виручки {platform_label} — {date_from}\n"]
    lines.append(f"\nЗамовлень у БД (platform={platform}): {len(db_orders)}")

    if not creds_available:
        lines.append(
            f"\n\n⚠️ Облікові дані {platform_label} не задані — звірка з API не виконана, "
            "показані лише дані з локальної БД."
        )
        return "".join(lines)

    api_by_id = {str(o["id"]): api_order_total_fn(o) for o in api_orders}

    missing_in_db  = sorted(set(api_by_id) - set(db_orders))
    missing_in_api = sorted(set(db_orders) - set(api_by_id))
    common_ids     = set(api_by_id) & set(db_orders)
    mismatched = [
        (oid, db_orders[oid], api_by_id[oid])
        for oid in sorted(common_ids)
        if abs(db_orders[oid] - api_by_id[oid]) > AMOUNT_TOLERANCE
    ]

    lines.append(f"\nЗамовлень у {platform_label} API: {len(api_by_id)}")

    lines.append(f"\n\n## Пропущено orders_watcher.py (є в {platform_label}, немає в БД)")
    if missing_in_db:
        for oid in missing_in_db:
            lines.append(f"\n- {oid}: {api_by_id[oid]:.2f} грн")
    else:
        lines.append(f"\nНемає — усі замовлення {platform_label} потрапили в БД.")

    lines.append(f"\n\n## Є в БД, немає в {platform_label} API за цей період (скасовано/змінено на боці маркетплейсу?)")
    if missing_in_api:
        for oid in missing_in_api:
            lines.append(f"\n- {oid}: {db_orders[oid]:.2f} грн")
    else:
        lines.append("\nНемає.")

    lines.append("\n\n## Розбіжність суми (те саме замовлення, різна сума)")
    if mismatched:
        for oid, db_total, api_total in mismatched:
            lines.append(
                f"\n- {oid}: БД {db_total:.2f} грн, {platform_label} API зараз {api_total:.2f} грн "
                f"(різниця {api_total - db_total:+.2f} грн)"
            )
    else:
        lines.append("\nНемає розбіжностей серед спільних замовлень.")

    total_api = sum(api_by_id.values())
    total_db  = sum(db_orders.values())
    lines.append("\n\n## Підсумок")
    lines.append(f"\nВиручка за {platform_label} API: {total_api:.2f} грн")
    lines.append(f"\nВиручка за БД: {total_db:.2f} грн")
    lines.append(f"\nРізниця: {total_api - total_db:+.2f} грн")

    return "".join(lines)


def build_reconciliation(date_from: str, date_to: str) -> str:
    init_db()
    with get_connection() as conn:
        prom_orders = fetch_prom_orders_for_period(date_from, date_to) if PROM_API_KEY else []
        rozetka_orders = fetch_rozetka_orders_for_period(date_from, date_to)

        prom_section = _build_platform_section(
            "prom", "Prom", conn, date_from, date_to,
            creds_available=bool(PROM_API_KEY), api_orders=prom_orders, api_order_total_fn=_prom_order_total,
        )
        rozetka_section = _build_platform_section(
            "rozetka", "Rozetka", conn, date_from, date_to,
            creds_available=bool(ROZETKA_USERNAME and ROZETKA_PASSWORD),
            api_orders=rozetka_orders, api_order_total_fn=_rozetka_order_total,
        )

    return (prom_section + rozetka_section).lstrip()


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
