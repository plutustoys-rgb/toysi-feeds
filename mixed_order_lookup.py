#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mixed_order_lookup.py — «батьківський платіж → два замовлення» для ЗМІШАНОГО кошика сайту (23.10.2026). ТІЛЬКИ ЧИТАННЯ orders.db.

LiqPay, алерти й сторінка подяки несуть БАТЬКІВСЬКИЙ номер (PT-ДАТА-ЧАС-hex), а в БД лежать ДВА рядки (Toysi і RT) з іншими internal id. Цей інструмент за будь-яким із номерів
(батьківський, internal id частини з/без `site_`, короткий номер покупця PT-NNNNNN) показує всю групу: id, тип, публічний номер, оплату, статус, ТТН, суму, спільний payment_id.
Без персональних даних (ПІБ/телефон/адреса не друкуються).

Запуск:  python mixed_order_lookup.py PT-20261023-090000-aaaaaa
         python mixed_order_lookup.py PT-123456
"""
import re
import sqlite3
import sys

import orders_db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PARENT_RE = re.compile(r"(?:site_)?(PT-\d{8}-\d{6}-[0-9a-f]{6})", re.I)
PUBLIC_RE = re.compile(r"PT-[1-9][0-9]{5}")


def _is_rt(items) -> bool:
    return any(str(i.get("toysi_code") or "").lower().startswith("rt-") for i in (items or []))


def resolve(conn: sqlite3.Connection, ident: str) -> list:
    """Список рядків групи (dict) за номером; [] — не знайдено / це не змішане замовлення."""
    ident = (ident or "").strip()
    m = PARENT_RE.fullmatch(ident)
    pub = PUBLIC_RE.fullmatch(ident)
    if pub:
        row = conn.execute("SELECT * FROM orders WHERE platform='site' AND public_number=?", (ident,)).fetchone()
    elif m:
        oid = "PT-" + m.group(1)[3:].lower()
        kids = orders_db.get_group_children(conn, oid)
        if kids:
            return kids
        row = conn.execute("SELECT * FROM orders WHERE internal_order_id=? AND platform='site'", (f"site_{oid}",)).fetchone()
    else:
        return []
    if not row:
        return []
    d = orders_db._row_to_dict(row)
    return orders_db.get_group_children(conn, d.get("site_group_id")) if d.get("site_group_id") else []


def render(kids: list) -> str:
    lines = []
    for k in kids:
        lines.append(f"{k['internal_order_id']} | {'RT' if _is_rt(k.get('items')) else 'Toysi'} | № {k.get('public_number') or '—'} | сума {k.get('site_charged_total')} | оплата {'так' if k.get('payment_confirmed') else 'НІ'} "
                     f"| статус {k.get('status')} | ТТН {k.get('toysi_ttn') or k.get('rt_ttn_ref') or '—'} | LiqPay payment_id {k.get('liqpay_payment_id') or '—'} "
                     f"| чек Checkbox {k.get('checkbox_receipt_id') or '—'} (прапорець {'є' if k.get('checkbox_ettn_registered_at') else 'нема'}, claim {k.get('receipt_claim_at') or 'нема'}) "
                     f"| повернення refund_receipt_at {k.get('refund_receipt_at') or 'нема'}")
    return f"Батьківський номер платежу LiqPay: {kids[0].get('site_group_id')}\n" + "\n".join(lines)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    uri = "file:" + orders_db.DB_PATH.replace("\\", "/") + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            kids = resolve(conn, argv[0])
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        print(f"База недоступна або без колонки site_group_id ({e}). Перевір ORDERS_DB_PATH; колонка з'являється після init_db на оновленому коді.")
        return 3
    if not kids:
        print("Не знайдено або це не змішане замовлення (немає site_group_id).")
        return 1
    print(render(kids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
