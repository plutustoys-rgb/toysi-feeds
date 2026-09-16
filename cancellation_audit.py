#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""READ-ONLY: аудит СКАСОВАНИХ замовлень — що, коли, чому, на яку суму, з поверненням чи ні.

НІЧОГО НЕ ПИШЕ. Дає власнику/бухгалтеру переглядний слід кожного скасування (вимога
власника 2026-09-16: «аудит скасованих замовлень», а не тихий флаг статусу).

Три класи скасувань, усі з orders.db:
  1. Скасовано ДО відправки (мертве): status='cancelled' (bank_check авто-очистка застряглих
     передоплат) або *_cancelled_before_forward (пре-форвардна перевірка order_router).
     Посилки нема → повернення не потрібне.
  2. Скасовано ПІСЛЯ відправки: delivery_status IN ('cancelled','returned') + є toysi_ttn.
     Тут створюється зворотна ТТН НП (np_return_ttn) — показуємо її.
Поля: платформа, № замовлення, сума, коли скасовано, причина, оплачено?, зворотна ТТН.

Запуск на VPS: `cd /opt/plutustoys && venv/bin/python3 cancellation_audit.py [дні]`
  (необов'язковий аргумент — глибина в днях за cancelled_at/created_at; дефолт усі).
"""
import json
import sys
from datetime import datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from orders_db import get_connection, _row_to_dict

_CANCELLED_STATUSES = (
    "cancelled", "prom_cancelled_before_forward",
    "eva_cancelled_before_forward", "rozetka_cancelled_before_forward",
)


def _amount(items_json) -> float:
    try:
        items = json.loads(items_json) if isinstance(items_json, str) else (items_json or [])
    except (ValueError, TypeError):
        return 0.0
    return sum((it.get("price") or 0) * (it.get("qty") or 1) for it in items)


def _reason(o: dict) -> str:
    if o.get("cancel_reason"):
        return o["cancel_reason"]
    st = o.get("status") or ""
    if st.endswith("_cancelled_before_forward"):
        return f"скасовано в кабінеті ДО передачі ({st.split('_')[0]})"
    if o.get("delivery_status") == "returned":
        return "повернення (Toysi-статус)"
    if o.get("delivery_status") == "cancelled":
        return "скасовано (Toysi-статус)"
    return st or "?"


def main() -> None:
    max_days = None
    if len(sys.argv) > 1:
        try:
            max_days = int(sys.argv[1])
        except ValueError:
            max_days = None
    cutoff = (datetime.now() - timedelta(days=max_days)) if max_days else None

    placeholders = ",".join("?" for _ in _CANCELLED_STATUSES)
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT * FROM orders
                WHERE status IN ({placeholders})
                   OR delivery_status IN ('cancelled', 'returned')""",
            _CANCELLED_STATUSES,
        ).fetchall()
    orders = [_row_to_dict(r) for r in rows]

    shipped, not_shipped = [], []
    for o in orders:
        ts = o.get("cancelled_at") or o.get("created_at")
        if cutoff and ts:
            try:
                if datetime.fromisoformat(ts) < cutoff:
                    continue
            except ValueError:
                pass
        (shipped if o.get("toysi_ttn") else not_shipped).append(o)

    total = len(shipped) + len(not_shipped)
    by_plat = {}
    for o in shipped + not_shipped:
        p = o.get("platform") or "?"
        by_plat[p] = by_plat.get(p, 0) + 1
    sum_amount = sum(_amount(o.get("items")) for o in shipped + not_shipped)

    win = f"за {max_days} дн." if max_days else "за весь час"
    print(f"=== АУДИТ СКАСОВАНИХ ЗАМОВЛЕНЬ ({win}, read-only) ===")
    print(f"Усього скасованих: {total}  |  сума: {sum_amount:.2f} грн  |  по площадках: {dict(by_plat) or '—'}")

    def _fmt(o):
        ts = (o.get("cancelled_at") or o.get("created_at") or "?")[:16]
        paid = "оплачено" if o.get("payment_confirmed") else "не оплачено"
        return (f"  • {o.get('platform')} №{o.get('order_id')} — {_amount(o.get('items')):.2f} грн — "
                f"{ts} — {_reason(o)} — {paid}")

    print(f"\n📦 Скасовано ПІСЛЯ відправки (потрібна/створена зворотна ТТН): {len(shipped)}")
    for o in sorted(shipped, key=lambda x: x.get("cancelled_at") or "", reverse=True):
        rttn = o.get("np_return_ttn")
        print(_fmt(o) + (f" — зворотна ТТН {rttn}" if rttn else " — ⚠️ зворотна ТТН ще не створена (dry-run?)"))

    print(f"\n🗑 Скасовано ДО відправки (мертве, посилки нема): {len(not_shipped)}")
    for o in sorted(not_shipped, key=lambda x: x.get("cancelled_at") or "", reverse=True):
        print(_fmt(o))


if __name__ == "__main__":
    main()
