"""export_orders.py — вивантаження замовлень з orders.db для аналізу (запит консультанта 2026-08-25).

Рядок на замовлення: internal_order_id, дата (created_at), канал (platform), виручка (Σ price×qty
з items), к-сть позицій (Σ qty), delivery_status, likely_test (евристика). Пише CSV у reports/ +
друкує ПІДСУМОК по каналах у stdout (щоб власник бачив через `… | cc`).

⚠️ COST по замовленню тут НЕ рахуємо — собівартість живе в `kodv_ledger.jsonl` (graph6_cogs, зона КОДВ),
не в orders.db. Виручка тут = продажна ціна (price×qty). Маржу зшиває КОДВ через ledger.

⚠️ Тест-замовлення: окремого прапорця в схемі НЕМА. `likely_test` — евристика (ім'я/телефон/id містять
«тест/test» або відомий тест-маркер). КОДВ/власник підтверджує тест-vs-реальне за source/ім'ям/телефоном.

ЗАПУСК (на VPS, де лежить orders.db):
    cd /opt/plutustoys && venv/bin/python3 export_orders.py --from 2026-07-17 --to 2026-08-24
    → reports/orders_export_2026-07-17_2026-08-24.csv + підсумок у stdout.
"""
import argparse
import csv
import json
import os
import sys

import orders_db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Евристика тест-замовлення (немає прапорця в схемі; КОДВ/власник підтверджує остаточно).
_TEST_MARKERS = ("тест", "test", "998877")


def _revenue_and_count(items_json: str):
    """(виручка Σ price×qty, к-сть позицій Σ qty) з items-JSON. Толерантно до кривих полів."""
    try:
        items = json.loads(items_json) if isinstance(items_json, str) else (items_json or [])
    except (json.JSONDecodeError, TypeError):
        return 0.0, 0
    rev = 0.0
    cnt = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            qty = int(it.get("qty") or 1)
        except (ValueError, TypeError):
            qty = 1
        try:
            price = float(it.get("price") or 0)
        except (ValueError, TypeError):
            price = 0.0
        rev += price * qty
        cnt += qty
    return round(rev, 2), cnt


def _likely_test(row: dict) -> str:
    blob = " ".join(str(row.get(k) or "") for k in ("internal_order_id", "order_id", "customer_name", "phone")).lower()
    return "1" if any(m in blob for m in _TEST_MARKERS) else ""


def export(date_from: str, date_to: str, out_csv: str = None) -> str:
    out_csv = out_csv or os.path.join("reports", f"orders_export_{date_from}_{date_to}.csv")
    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    import sqlite3
    conn = sqlite3.connect(orders_db.DB_PATH)   # read-only експорт, прямий конект (get_connection — CM)
    conn.row_factory = sqlite3.Row
    # created_at — ISO-рядок; порівняння рядків працює для ISO. Верхня межа — кінець дня to.
    rows = conn.execute(
        "SELECT internal_order_id, order_id, platform, created_at, items, delivery_status, "
        "customer_name, phone, status, payment_method "
        "FROM orders WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
        (date_from, date_to + "T99"),
    ).fetchall()
    conn.close()

    cols = ["internal_order_id", "created_at", "platform", "revenue", "item_count",
            "delivery_status", "status", "payment_method", "likely_test"]
    per_channel = {}
    total_rev = 0.0
    n_real = 0
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            r = dict(r)   # sqlite3.Row не має .get() — далі працюємо зі словником
            rev, cnt = _revenue_and_count(r["items"])
            test = _likely_test(r)
            w.writerow({
                "internal_order_id": r["internal_order_id"], "created_at": r["created_at"],
                "platform": r["platform"], "revenue": rev, "item_count": cnt,
                "delivery_status": r["delivery_status"] or "", "status": r["status"] or "",
                "payment_method": r["payment_method"] or "", "likely_test": test,
            })
            if not test:
                per_channel[r["platform"]] = per_channel.get(r["platform"], 0) + 1
                total_rev += rev
                n_real += 1
    print(f"[export_orders] {len(rows)} замовлень {date_from}..{date_to} -> {out_csv}")
    print(f"[export_orders] РЕАЛЬНИХ (не likely_test): {n_real}; по каналах: {dict(sorted(per_channel.items()))}")
    print(f"[export_orders] виручка реальних: {round(total_rev, 2)} грн; сер.чек: "
          f"{round(total_rev / n_real, 2) if n_real else 0} грн")
    print("[export_orders] COST/маржа — не тут; kodv_ledger.jsonl (graph6_cogs), зона КОДВ.")
    return out_csv


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="date_from", default="2026-07-17", help="дата від (YYYY-MM-DD)")
    ap.add_argument("--to", dest="date_to", default="2026-08-24", help="дата до включно (YYYY-MM-DD)")
    ap.add_argument("--out", default=None, help="шлях CSV (за замовч. reports/orders_export_<range>.csv)")
    a = ap.parse_args()
    export(a.date_from, a.date_to, a.out)


if __name__ == "__main__":
    main()
