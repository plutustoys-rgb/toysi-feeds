#!/usr/bin/env python3
"""Зріз замовлень для запиту відгуків (заявка SMM 2026-08-19, MARKETING_CHANNEL.md).

SMM просить дані, щоб просити відгук ЛИШЕ у того, чиє замовлення реально доїхало:
order_id, marketplace, order_date, delivery_status, status, items (назва+sku).

⚠️ БЕЗ ФІНАНСІВ — за прямим правилом каналу (cost/margin/суми НІКОЛИ у спільні файли) і
за самим запитом SMM («без сум, без собівартості, без маржі»). З items беремо ЛИШЕ
name+toysi_code, поле price свідомо ВИКИДАЄМО.

Поля, яких у нас НЕМА (чесно позначено в CSV як порожні/довідково):
- delivered_date: окремого таймстемпа доставки не зберігаємо; ознака доставленості —
  delivery_status='delivered' (order_status_tracker за живим трекінгом НП). Дату видачі
  наразі не фіксуємо окремою колонкою.
- review_requested: центрального поля нема; SMM веде власний ledger за стабільним order_id.

Вивід: CSV у stdout або у файл (--out). За замовчуванням лише доставлені
(delivery_status='delivered') — саме review-придатний набір; --all для всіх активних.

Запуск на VPS: /opt/plutustoys/venv/bin/python3 export_review_candidates.py --out reports/review_candidates.csv
"""
import argparse
import csv
import json
import sys

from orders_db import get_connection

# Поля items, які ДОЗВОЛЕНО віддавати. price НАВМИСНО відсутній (без фінансів).
_ITEM_SAFE_FIELDS = ("name", "toysi_code")

_HEADER = [
    "order_id", "marketplace", "order_date", "delivery_status",
    "order_status", "toysi_order_id", "items",
]


def _safe_items(items_json: str) -> str:
    """items (JSON) → компактний рядок 'назва (sku)'; БЕЗ price/qty-сум."""
    try:
        items = json.loads(items_json) if items_json else []
    except (ValueError, TypeError):
        return ""
    parts = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        sku = str(it.get("toysi_code") or "").strip()
        parts.append(f"{name} ({sku})" if sku else name)
    return "; ".join(p for p in parts if p)


def fetch_candidates(delivered_only: bool = True) -> list:
    """Замовлення для запиту відгуків. delivered_only → лише delivery_status='delivered'."""
    where = "WHERE forwarded_to_toysi_at IS NOT NULL"
    if delivered_only:
        where += " AND delivery_status = 'delivered'"
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT order_id, platform, created_at, delivery_status, status,
                   toysi_order_id, items
            FROM orders
            {where}
            ORDER BY created_at DESC
            """
        ).fetchall()
    out = []
    for r in rows:
        out.append([
            r["order_id"],
            r["platform"],
            r["created_at"],
            r["delivery_status"] or "",
            r["status"],
            r["toysi_order_id"] or "",
            _safe_items(r["items"]),
        ])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Зріз замовлень для запиту відгуків (без фінансів).")
    ap.add_argument("--all", action="store_true",
                    help="усі передані Toysi замовлення (за замовч. — лише delivered)")
    ap.add_argument("--out", help="шлях до CSV-файла (за замовч. — stdout)")
    a = ap.parse_args()

    rows = fetch_candidates(delivered_only=not a.all)

    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(_HEADER)
            w.writerows(rows)
        print(f"[review-export] {len(rows)} рядків → {a.out}")
    else:
        w = csv.writer(sys.stdout)
        w.writerow(_HEADER)
        w.writerows(rows)


if __name__ == "__main__":
    main()
