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


# 3: глобальний circuit-breaker (N=10 скасованих/повернених COD-замовлень сайту, не по телефону)
os.environ["AUDIT_NO_TELEGRAM"] = "1"
api.COD_BREAKER_STATE_FILE = tempfile.mktemp(suffix=".json")   # НЕ бойовий .local_secrets
with orders_db.get_connection() as conn:
    conn.execute("DELETE FROM orders WHERE order_id LIKE 'PT-BRK-%'")
    conn.commit()

    def _insert_unsuccessful(n, status, phone_prefix="380509"):
        for i in range(n):
            orders_db.insert_order(conn, {
                "order_id": f"PT-BRK-{status}-{i}", "platform": "site", "payment_method": "cod",
                "customer_name": "Тест Тестенко", "phone": f"{phone_prefix}{i:06d}",
                "np_branch": "Київ, Відділення №1",
                "items": [{"toysi_code": "1", "name": "x", "qty": 1, "price": 10}],
                "delivery_status": status,
            })
        conn.commit()

    _insert_unsuccessful(api.SITE_COD_CIRCUIT_BREAKER_N - 1, "cancelled")
    _chk(f"брейкер: {api.SITE_COD_CIRCUIT_BREAKER_N - 1} скасованих (нижче порогу) → проходить",
         not _raises(api._check_cod_circuit_breaker, conn))

    # РІЗНІ статуси зі списку (cancelled + returned) рахуються РАЗОМ до одного порогу
    _insert_unsuccessful(1, "returned", phone_prefix="380508")
    _chk(f"брейкер: {api.SITE_COD_CIRCUIT_BREAKER_N} скасованих+повернених (на межі) → відхилено",
         _raises(api._check_cod_circuit_breaker, conn))

    # delivered/інший статус НЕ рахується до брейкера
    conn.execute("DELETE FROM orders WHERE order_id LIKE 'PT-BRK-%'")
    conn.commit()
    _insert_unsuccessful(api.SITE_COD_CIRCUIT_BREAKER_N + 3, "delivered", phone_prefix="380507")
    _chk("брейкер: 'delivered' не рахується — проходить попри N+3 записів",
         not _raises(api._check_cod_circuit_breaker, conn))

    # НЕ-site платформа не рахується
    conn.execute("DELETE FROM orders WHERE order_id LIKE 'PT-BRK-%'")
    conn.commit()
    for i in range(api.SITE_COD_CIRCUIT_BREAKER_N + 2):
        orders_db.insert_order(conn, {
            "order_id": f"PT-BRK-rozetka-{i}", "platform": "rozetka", "payment_method": "cod",
            "customer_name": "Тест Тестенко", "phone": f"380506{i:06d}",
            "np_branch": "Київ, Відділення №1",
            "items": [{"toysi_code": "1", "name": "x", "qty": 1, "price": 10}],
            "delivery_status": "cancelled",
        })
    conn.commit()
    _chk("брейкер: скасування НЕ-site платформ не рахуються",
         not _raises(api._check_cod_circuit_breaker, conn))

    # RESET_AFTER: старі скасування (ДО мітки) не рахуються, лише нові (ПІСЛЯ)
    conn.execute("DELETE FROM orders WHERE order_id LIKE 'PT-BRK-%'")
    conn.commit()
    old_ts = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    for i in range(api.SITE_COD_CIRCUIT_BREAKER_N + 2):
        orders_db.insert_order(conn, {
            "order_id": f"PT-BRK-reset-{i}", "platform": "site", "payment_method": "cod",
            "customer_name": "Тест Тестенко", "phone": f"380505{i:06d}",
            "np_branch": "Київ, Відділення №1",
            "items": [{"toysi_code": "1", "name": "x", "qty": 1, "price": 10}],
            "delivery_status": "cancelled", "created_at": old_ts,
        })
    conn.commit()
    reset_mark = datetime.now().isoformat(timespec="seconds")
    api.SITE_COD_CIRCUIT_BREAKER_RESET_AFTER = reset_mark
    try:
        _chk("брейкер: RESET_AFTER — старі скасування (до мітки) ігноруються, проходить",
             not _raises(api._check_cod_circuit_breaker, conn))
        orders_db.insert_order(conn, {
            "order_id": "PT-BRK-reset-new", "platform": "site", "payment_method": "cod",
            "customer_name": "Тест Тестенко", "phone": "380504000001",
            "np_branch": "Київ, Відділення №1",
            "items": [{"toysi_code": "1", "name": "x", "qty": 1, "price": 10}],
            "delivery_status": "cancelled",
            "created_at": (datetime.now() + timedelta(seconds=1)).isoformat(timespec="seconds"),
        })
        conn.commit()
        _chk("брейкер: RESET_AFTER — досі проходить, 1 нове скасування << N",
             not _raises(api._check_cod_circuit_breaker, conn))
    finally:
        api.SITE_COD_CIRCUIT_BREAKER_RESET_AFTER = ""   # не протікає в наступні тести файлу

if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Гардрейли COD сайту (стеля суми + ліміт на телефон/добу + глобальний circuit-breaker) працюють")
sys.exit(0)
