#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_awaiting_payment_excludes_cancelled.py — регрес-тест get_orders_awaiting_payment.

Живий інцидент 20–22.10: 16 скасованих у кабінеті передоплат (status='cancelled', payment_confirmed=0)
лишались у вибірці й повторно проходили Прохід 2 bank_check КОЖЕН цикл (запит у кабінет + UPDATE
в одній транзакції) → довгий write-lock → «database is locked» у order-pipeline.

ІНВАРІАНТИ:
  - prepaid + payment_confirmed=0 + status не 'cancelled' → у вибірці;
  - після mark_cancelled замовлення з вибірки зникає;
  - cod, оплачені (payment_confirmed=1) → не в вибірці (як і раніше).

Власна тимчасова БД (TemporaryDirectory). `python test_awaiting_payment_excludes_cancelled.py` → exit 0/1.
"""
import os
import sys
import tempfile

import orders_db as odb

_FAILS = []


def _check(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


def _order(oid, method="prepaid", paid=False):
    return {"order_id": oid, "platform": "prom", "payment_method": method,
            "payment_confirmed": paid, "items": [{"toysi_code": "1", "name": "x", "qty": 1, "price": 100}]}


with tempfile.TemporaryDirectory() as d:
    db = os.path.join(d, "t.db")
    odb.init_db(db)
    with odb.get_connection(db) as conn:
        for o in (_order("1"), _order("2"), _order("3", method="cod"), _order("4", paid=True)):
            odb.insert_order(conn, o)

    with odb.get_connection(db) as conn:
        ids = {o["internal_order_id"] for o in odb.get_orders_awaiting_payment(conn)}
    _check("prepaid неоплачені (1,2) у вибірці, cod/оплачені — ні", ids == {"prom_1", "prom_2"})

    with odb.get_connection(db) as conn:
        odb.mark_cancelled(conn, "prom_2", "тест")

    with odb.get_connection(db) as conn:
        ids = {o["internal_order_id"] for o in odb.get_orders_awaiting_payment(conn)}
    _check("скасоване (2) зникло з вибірки, 1 лишилось", ids == {"prom_1"})

sys.exit(1 if _FAILS else 0)
