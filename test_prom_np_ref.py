#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_prom_np_ref.py — структурний НП-реф Prom (orders_watcher._convert_prom_order, 20.10.2026; Toysi-менеджер: «не підтягується інформація по відділенню», замовлення Prom 434846615).
Інваріанти: (1) Prom recipient_address.warehouse_id → np_ref_id ЗАВЖДИ (НП-перевізник); (2) для НОВОГО замовлення warehouse_by_ref → np_city_ref + np_warehouse_number; (3) для вже відомого замовлення НП НЕ кличеться (вікно 72 год перечитується щоциклу);
(4) збій/порожня відповідь НП → np_ref_id лишається (ретрай у build_toysi_order), city/number None, виняток не валить; (5) нема recipient_address / Укрпошта → поля None (текстовий шлях як було);
(6) наскрізно: build_toysi_order для такого замовлення віддає shipping_city_id і shipping_warehouse_id (Toysi CRM підтягує місто)."""
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
sys.path.insert(0, str(Path(__file__).parent))
import orders_watcher as ow  # noqa: E402
import nova_poshta  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


REF = "7b422fc7-e1b8-11e3-8c4a-0050568002cf"
CITY = "f706230e-4078-11de-b509-001d92f78698"
calls = []


def fake_wh(ref):
    calls.append(ref)
    return {"city_ref": CITY, "number": "2", "description": "Відділення №2 (до 30 кг): вул. Незалежності, 22"}


nova_poshta.warehouse_by_ref = fake_wh


def prom(order_id="1", provider="nova_poshta", ra=True):
    dpd = {"provider": provider}
    if ra:
        dpd["recipient_address"] = {"city_id": CITY, "city_name": "м. Кагарлик (Київська обл.)", "warehouse_id": REF}
    return {"id": order_id, "status": "pending", "payment_option": {"name": "Пром-оплата"}, "payment_data": {"status": "paid"}, "client_first_name": "Іван", "client_last_name": "Петренко",
            "phone": "380671112233", "delivery_address": "м. Кагарлик (Київська обл.), №2 (до 30 кг): вул. Незалежності, 22",
            "delivery_provider_data": dpd, "delivery_option": {"name": "Нова Пошта"}, "products": [{"sku": "211798", "name": "Вкладиш", "quantity": 1, "price": "250.75"}]}


import orders_db  # noqa: E402
orders_db.init_db()
o = ow._convert_prom_order(prom("1001"))
chk("нове замовлення: np_ref_id, np_city_ref=CityRef НП, np_warehouse_number=2", o["np_ref_id"] == REF and o["np_city_ref"] == CITY and o["np_warehouse_number"] == "2" and calls == [REF])

# замовлення вже в БД → НП не кличеться
import orders_db  # noqa: E402
orders_db.init_db()
with orders_db.get_connection() as c:
    orders_db.insert_order(c, ow.normalize_order(o))
calls.clear()
o2 = ow._convert_prom_order(prom("1001"))
chk("відоме замовлення: np_ref_id є, НП НЕ кличеться (np_city_ref/number None — вставка відбувається лише для нових)", o2["np_ref_id"] == REF and calls == [] and o2["np_city_ref"] is None and o2["np_warehouse_number"] is None)


def boom(ref):
    raise RuntimeError("НП недоступна")


nova_poshta.warehouse_by_ref = boom
o3 = ow._convert_prom_order(prom("1002"))
chk("збій НП: np_ref_id лишається (ретрай при форварді), city/number None, без винятку", o3["np_ref_id"] == REF and o3["np_city_ref"] is None and o3["np_warehouse_number"] is None)
nova_poshta.warehouse_by_ref = lambda ref: None
o4 = ow._convert_prom_order(prom("1003"))
chk("НП повернула None: np_ref_id лишається, city/number None", o4["np_ref_id"] == REF and o4["np_city_ref"] is None)

nova_poshta.warehouse_by_ref = fake_wh
calls.clear()
o5 = ow._convert_prom_order(prom("1004", ra=False))
chk("нема recipient_address: усе None, НП не кличеться (текстовий шлях як було)", o5["np_ref_id"] is None and o5["np_city_ref"] is None and calls == [])
o6 = ow._convert_prom_order(prom("1005", provider="ukrposhta"))
chk("Укрпошта: НП-поля None, НП не кличеться", o6["np_ref_id"] is None and calls == [])

# наскрізно: build_toysi_order
import order_router  # noqa: E402
nova_poshta.warehouse_by_ref = fake_wh
norm = ow.normalize_order(ow._convert_prom_order(prom("1006")))
norm["internal_order_id"] = "prom_1006"
norm["platform"] = "prom"
res = order_router.build_toysi_order(norm)
payload = res[0] if isinstance(res, tuple) else res
txt = str(payload)
chk("build_toysi_order: Toysi отримує shipping_city_id (CityRef НП) і shipping_warehouse_id=2", payload is not None and CITY in txt and "'shipping_warehouse_id': '2'" in txt)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
