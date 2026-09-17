#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_site_cod_guardrails.py — регрес-тест гардрейлів COD сайту (CONSULTANT_CHANNEL.md
2026-09-13 → 2026-09-16, «32% скасувань COD на EVA — кожне третє не доходить до грошей»):
  1. Стеля суми COD-замовлення (SITE_COD_CEILING) — вище лише передоплата.
  2. Ліміт COD-замовлень з одного телефону за 24 год (SITE_COD_PHONE_DAILY_LIMIT).

Мережа не потрібна. orders.db — ТИМЧАСОВА (ORDERS_DB_PATH), не бойова.
`python test_site_cod_guardrails.py` → exit 0/1.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ["ORDERS_DB_PATH"] = tempfile.mktemp(suffix=".db")

import orders_db
import site_order_api as api

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


def _raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except api.OrderError:
        return True


# 1: стеля COD — нижче/на межі проходить, вище — відмова
_chk("стеля: 3000 (на межі) проходить", not _raises(api._check_cod_ceiling, "cod", api.SITE_COD_CEILING))
_chk("стеля: 3001 (вище) відхилено", _raises(api._check_cod_ceiling, "cod", api.SITE_COD_CEILING + 1))
_chk("стеля: prepaid НЕ обмежений сумою", not _raises(api._check_cod_ceiling, "prepaid", 999999))

# 2: ліміт на телефон — жива тимчасова БД
orders_db.init_db()
with orders_db.get_connection() as conn:
    _chk("ліміт: 0 замовлень → проходить", not _raises(api._check_cod_phone_limit, conn, "380500000001"))

    # Вставляємо (LIMIT-1) COD-замовлень за останні 24 год на той самий телефон — ще має проходити
    for i in range(api.SITE_COD_PHONE_DAILY_LIMIT - 1):
        orders_db.insert_order(conn, {
            "order_id": f"PT-TEST-{i}", "platform": "site", "payment_method": "cod",
            "customer_name": "Тест Тестенко", "phone": "380500000001",
            "np_branch": "Київ, Відділення №1", "items": [{"toysi_code": "1", "name": "x", "qty": 1, "price": 10}],
        })
    conn.commit()
    _chk(f"ліміт: {api.SITE_COD_PHONE_DAILY_LIMIT - 1} замовлень (нижче ліміту) → проходить",
         not _raises(api._check_cod_phone_limit, conn, "380500000001"))

    # Ще одне — досягаємо ліміту
    orders_db.insert_order(conn, {
        "order_id": "PT-TEST-LAST", "platform": "site", "payment_method": "cod",
        "customer_name": "Тест Тестенко", "phone": "380500000001",
        "np_branch": "Київ, Відділення №1", "items": [{"toysi_code": "1", "name": "x", "qty": 1, "price": 10}],
    })
    conn.commit()
    _chk(f"ліміт: {api.SITE_COD_PHONE_DAILY_LIMIT} замовлень (на межі) → відхилено",
         _raises(api._check_cod_phone_limit, conn, "380500000001"))

    # ІНШИЙ телефон — не зачеплений
    _chk("ліміт: інший телефон не зачеплений", not _raises(api._check_cod_phone_limit, conn, "380500000002"))

    # СТАРЕ замовлення (>24 год) не рахується
    old_ts = (datetime.now() - timedelta(hours=25)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM orders WHERE phone='380500000003'")
    for i in range(api.SITE_COD_PHONE_DAILY_LIMIT + 2):
        orders_db.insert_order(conn, {
            "order_id": f"PT-OLD-{i}", "platform": "site", "payment_method": "cod",
            "customer_name": "Тест Тестенко", "phone": "380500000003",
            "np_branch": "Київ, Відділення №1", "items": [{"toysi_code": "1", "name": "x", "qty": 1, "price": 10}],
            "created_at": old_ts,
        })
    conn.commit()
    _chk("ліміт: замовлення старші 24 год НЕ рахуються", not _raises(api._check_cod_phone_limit, conn, "380500000003"))

    # PREPAID не рахується в COD-ліміт
    conn.execute("DELETE FROM orders WHERE phone='380500000004'")
    for i in range(api.SITE_COD_PHONE_DAILY_LIMIT + 2):
        orders_db.insert_order(conn, {
            "order_id": f"PT-PREPAID-{i}", "platform": "site", "payment_method": "prepaid",
            "customer_name": "Тест Тестенко", "phone": "380500000004",
            "np_branch": "Київ, Відділення №1", "items": [{"toysi_code": "1", "name": "x", "qty": 1, "price": 10}],
        })
    conn.commit()
    _chk("ліміт: prepaid-замовлення НЕ рахуються в COD-ліміт",
         not _raises(api._check_cod_phone_limit, conn, "380500000004"))


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Гардрейли COD сайту (стеля суми + ліміт на телефон/добу) працюють")
sys.exit(0)
