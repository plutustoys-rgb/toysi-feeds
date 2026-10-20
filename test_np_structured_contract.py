#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_np_structured_contract.py — НАСКРІЗНИЙ контракт «усі площадки віддають Toysi ТОЧНЕ місто і відділення НП» (20.10.2026).
Причина: за місяць три окремі інциденти одного класу (EVA «№3→№2», Rozetka однойменні села, Prom — лише текст, 6/6 замовлень без shipping_city_id; скарга менеджера Toysi 20.10), кожен виправляли на своїй площадці.
Тест бере РЕАЛІСТИЧНЕ сире замовлення кожної площадки (Prom, Rozetka, EVA, сайт) → converter/normalize_order → build_toysi_order і вимагає: shipping_warehouse_id = номер клієнта І shipping_city_id = CityRef НП.
Нова площадка або зміна формату, що знову скотиться до тексту, ламає цей тест ДО мержу. НП підмінена (warehouse_by_ref → Кагарлик №2); мережі й бойових даних немає."""
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
sys.path.insert(0, str(Path(__file__).parent))
import orders_db  # noqa: E402
import orders_watcher as ow  # noqa: E402
import nova_poshta  # noqa: E402
import order_router as orr  # noqa: E402
import rozetka_client  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


REF = "7b422fc7-e1b8-11e3-8c4a-0050568002cf"
CITY = "f706230e-4078-11de-b509-001d92f78698"
orders_db.init_db()
nova_poshta.warehouse_by_ref = lambda ref: {"city_ref": CITY, "number": "2", "description": "Відділення №2 (до 30 кг): вул. Незалежності, 22"} if ref == REF else None
orr.settlement_raion = lambda *a, **k: ""
orr.warehouse_by_ref = nova_poshta.warehouse_by_ref      # order_router імпортує функцію за іменем — підміняємо й там (інакше жива НП: мережа залежно від .env)
rozetka_client.is_order_paid = lambda oid: True

RAW = {
    "prom": lambda: ow._convert_prom_order({
        "id": 5001, "status": "pending", "payment_option": {"name": "Пром-оплата"}, "payment_data": {"status": "paid"},
        "client_first_name": "Іван", "client_last_name": "Петренко", "phone": "380671112233",
        "delivery_address": "м. Кагарлик (Київська обл.), №2 (до 30 кг): вул. Незалежності, 22",
        "delivery_provider_data": {"provider": "nova_poshta", "recipient_address": {"city_id": CITY, "city_name": "м. Кагарлик (Київська обл.)", "warehouse_id": REF}},
        "delivery_option": {"name": "Нова Пошта"}, "products": [{"sku": "211798", "name": "Вкладиш", "quantity": 1, "price": "250.75"}]}),
    "rozetka": lambda: ow._convert_rozetka_order({
        "id": 5002, "recipient_phone": "380671112233", "user_phone": "380671112233", "payment_type": "card", "payment_type_name": "Оплата карткою",
        "delivery": {"delivery_service_name": "Нова Пошта", "ref_id": REF, "place_number": "2", "city": {"name": "Кагарлик"}, "recipient_title": "Петренко Іван",
                     "place_street": "вул. Незалежності", "place_house": "22", "name_logo": "np"},
        "purchases": [{"item": {"id": "211798", "name": "Вкладиш", "article": "211798", "price": 250.75}, "quantity": 1, "price": 250.75, "item_name": "Вкладиш"}]}),
    "eva": lambda: ow._convert_eva_order({
        "id": "8-5003", "payment": {"method": "LIQPAY", "status": "authorized"}, "recipient": {"phone": "380671112233", "first_name": "Іван", "last_name": "Петренко"},
        "shipping": {"method": "novaposhta_warehouse", "address": {"city": "Кагарлик", "street": {"name": "вул. Незалежності, 22"}, "city_id": CITY, "warehouse_id": REF, "warehouse_number": "2"}},
        "items": [{"id": "211798", "name": "Вкладиш", "quantity": 1, "price": 250.75}]}),
}

for plat, make in RAW.items():
    try:
        raw = make()
        norm = ow.normalize_order(raw)
        norm["internal_order_id"] = f"{plat}_contract"
        norm["platform"] = plat
        norm["payment_method"] = "prepaid"
        payload = orr.build_toysi_order(norm)
        ok = payload is not None and payload.get("shipping_warehouse_id") == "2" and payload.get("shipping_city_id") == CITY
        chk(f"{plat}: у Toysi йде shipping_warehouse_id=2 і shipping_city_id=CityRef НП (точне місто й відділення)", ok)
        if not ok:
            print("   payload:", {k: v for k, v in (payload or {}).items() if k.startswith("shipping")}, "| np-поля:", {k: norm.get(k) for k in ("np_city_ref", "np_warehouse_number", "np_ref_id")})
    except Exception as e:  # noqa: BLE001
        chk(f"{plat}: converter/роутер відпрацював без винятку ({type(e).__name__}: {str(e)[:90]})", False)

# сайт: чекаут віддає ref+number напряму (автокомпліт НП) -> build_order -> роутер
try:
    sys.path.insert(0, str(Path(__file__).parent / "site"))
    import site_order_api as so
    so.price_map = lambda: {"100": {"price": 250, "name": "Тест"}}
    so.stock_snapshot = lambda: ({"100": 5}, None)
    payload_in = {"items": [{"id": "100", "qty": 1}], "name": "Іван Петренко", "phone": "0501234567", "city_name": "Кагарлик", "warehouse_name": "Відділення №2",
                  "np_city_ref": CITY, "np_warehouse_number": "2", "payment_method": "prepaid"}
    order_s, _total = so.build_order(payload_in)
    order_s["internal_order_id"] = "site_contract"
    order_s["platform"] = "site"
    order_s["payment_method"] = "prepaid"
    pl = orr.build_toysi_order(order_s)
    chk("site: у Toysi йде shipping_warehouse_id=2 і shipping_city_id=CityRef НП", pl is not None and pl.get("shipping_warehouse_id") == "2" and pl.get("shipping_city_id") == CITY)
except Exception as e:  # noqa: BLE001
    chk(f"site: build_order -> роутер без винятку ({type(e).__name__}: {str(e)[:120]})", False)

# щоденний звіт: рядок «адреса НП без точного міста» по площадках
import daily_report as dr  # noqa: E402
with orders_db.get_connection() as c:
    for oid, plat, city in (("D1", "prom", None), ("D2", "prom", ""), ("D3", "eva", CITY)):
        orders_db.insert_order(c, {"order_id": oid, "platform": plat, "payment_method": "prepaid", "payment_confirmed": 1, "customer_name": "Т", "phone": "380671112233", "np_branch": "Київ, №1",
                                   "np_city_ref": city, "carrier": "nova_poshta", "items": [{"toysi_code": "1", "name": "x", "qty": 1, "price": 100}]})
        c.execute("UPDATE orders SET forwarded_to_toysi_at='2026-10-20T10:00:00' WHERE internal_order_id=?", (f"{plat}_{oid}",))
    c.commit()
    line = dr._np_structure_line(c, "2026-10-20T00:00:00")
    ok_all = dr._np_structure_line(c, "2026-10-21T00:00:00")
chk("звіт: рядок показує prom 2 без CityRef (eva з CityRef не рахується)", "prom 2" in line and "eva" not in line)
chk("звіт: коли таких нема — «усі … з точним містом»", "усі" in ok_all and "точне місто" in ok_all)

# build_report() повністю (мережеві рядки підмінені): звіт не падає, рядок про адресу НП є (аудит #901: виклик поза with ронив щоденний звіт)
dr._rozetka_balance_line = lambda: ""
dr._prom_balance_line = lambda: ""
dr._rozetka_validation_status_line = lambda: ""
dr.fetch_toysi_catalog = lambda *a, **k: {}
try:
    rep_txt = dr.build_report()
    chk("build_report() відпрацьовує на БД (звіт не падає) і містить рядок «Адреса НП у Toysi»", "Адреса НП у Toysi" in rep_txt)
except Exception as e:  # noqa: BLE001
    chk(f"build_report() відпрацьовує ({type(e).__name__}: {str(e)[:100]})", False)
with orders_db.get_connection() as c:
    chk("звіт: 0 передач -> «передач не було»", "передач не було" in dr._np_structure_line(c, "2099-01-01T00:00:00", 0))
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
