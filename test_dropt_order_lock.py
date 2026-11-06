#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dropt_order_lock.py — 06.11.2026 (аудит #1104): купівля товарів Dropt (id «dropt-<артикул>») ЗАКРИТА на сервері, доки не збудовано конвеєр замовлень Dropt.
Інваріанти: (1) build_order відхиляє будь-який кошик із dropt- позицією (і окремий, і змішаний з Toysi/rt-, будь-який спосіб оплати), навіть якщо ціна в index.json є (картки відкрито SITE_DROPT_LIVE);
(2) Toysi/rt- замовлення поводяться як раніше; (3) orders_db.get_orders_ready_to_forward НЕ повертає замовлення з dropt- (і cod, і paid prepaid), Toysi-замовлення повертає; (4) order_router.route_order зупиняє
dropt-замовлення ДО звернення до Toysi."""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "site"))
import orders_db  # noqa: E402
import order_router as orr  # noqa: E402
import site_order_api as so  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def raises(fn):
    try:
        fn()
    except so.OrderError:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


so.price_map = lambda: {"100": {"price": 250, "name": "Тест"}, "rt-101": {"price": 400, "name": "RT"}, "dropt-59298": {"price": 754, "name": "Dropt-товар"}}
so.stock_snapshot = lambda: ({"100": 5, "rt-101": 3, "dropt-59298": 3}, None)
base = {"name": "Іван Петренко", "phone": "0501234567", "city_name": "Київ", "warehouse_name": "відділення 5"}
D = [{"id": "dropt-59298", "qty": 1}]
chk("dropt- + prepaid → відмова", raises(lambda: so.build_order(dict(base, items=D, payment_method="prepaid"))))
chk("dropt- + cod → відмова", raises(lambda: so.build_order(dict(base, items=D, payment_method="cod"))))
chk("dropt- + спосіб не вказано → відмова", raises(lambda: so.build_order(dict(base, items=D))))
chk("dropt- у змішаному кошику з Toysi → відмова", raises(lambda: so.build_order(dict(base, items=[{"id": "100", "qty": 1}] + D, payment_method="prepaid"))))
chk("dropt- у змішаному кошику з rt- → відмова", raises(lambda: so.build_order(dict(base, items=[{"id": "rt-101", "qty": 1}] + D, payment_method="prepaid"))))
chk("id із великими літерами/пробілами («  DROPT-59298 ») → теж відмова", raises(lambda: so.build_order(dict(base, items=[{"id": "  DROPT-59298 ", "qty": 1}], payment_method="prepaid"))))
chk("підробка: dropt- id, якого нема в index.json → відмова (не 500)", raises(lambda: so.build_order(dict(base, items=[{"id": "dropt-1", "qty": 1}], payment_method="prepaid"))))
chk("is_dropt_id: лише префікс dropt-; rt-/Toysi/порожнє — ні", so.is_dropt_id("dropt-5") and not so.is_dropt_id("rt-5") and not so.is_dropt_id("100") and not so.is_dropt_id("") and not so.is_dropt_id(None))
o, t = so.build_order(dict(base, items=[{"id": "100", "qty": 1}]))
chk("Toysi без змін: cod за замовчуванням", o["payment_method"] == "cod" and t == 250)
o, t = so.build_order(dict(base, items=[{"id": "rt-101", "qty": 1}], payment_method="prepaid"))
chk("rt- без змін: prepaid проходить", o["payment_method"] == "prepaid" and t == 400)

# БД: форвард
orders_db.init_db()


def new_order(oid, code, pm, paid):
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": "site", "payment_method": pm, "payment_confirmed": paid, "customer_name": "Іван Петренко", "phone": "380671112233",
                                   "email": "b@e.com", "np_branch": "Київ, Відділення №5", "carrier": "nova_poshta", "items": [{"toysi_code": code, "name": "x", "qty": 1, "price": 100}]})
        c.execute("UPDATE orders SET payment_confirmed=? WHERE internal_order_id=?", (paid, f"site_{oid}"))
        c.commit()


new_order("T1", "100", "cod", 0)
new_order("D1", "dropt-59298", "cod", 0)
new_order("D2", "dropt-59298", "prepaid", 1)
new_order("R1", "rt-101", "prepaid", 1)
with orders_db.get_connection() as c:
    ready = {x["internal_order_id"] for x in orders_db.get_orders_ready_to_forward(c)}
chk("get_orders_ready_to_forward: Toysi є; dropt- (cod і оплачений prepaid) та rt- — немає", ready == {"site_T1"})

# роутер
orr._ensure_site_prepaid_receipt = lambda conn, order, tm: True
for fn in ("_check_prom_not_cancelled", "_check_eva_not_cancelled", "_check_rozetka_not_cancelled"):
    setattr(orr, fn, lambda conn, order: True)
reached = []
orr.fetch_toysi_catalog = lambda: reached.append("fetch") or {}
orr.site_np_hold_reason = lambda o, t: reached.append("hold") or "stop"
orr.send_throttled_alert = lambda *a, **k: None
orr.route_order(None, {"internal_order_id": "PT-D", "platform": "site", "items": [{"toysi_code": "dropt-59298", "qty": 1}]})
chk("route_order: dropt-замовлення зупиняється ДО звернення до Toysi", reached == [])
orr.route_order(None, {"internal_order_id": "PT-D2", "platform": "site", "items": [{"toysi_code": "100", "qty": 1}, {"toysi_code": "Dropt-1", "qty": 1}]})
chk("route_order: dropt- у змішаному замовленні (регістр) теж не йде в Toysi", reached == [])
orr.route_order(None, {"internal_order_id": "PT-2", "platform": "site", "items": [{"toysi_code": "100", "qty": 1}]})
chk("route_order: звичайне замовлення йде далі (fetch каталогу Toysi)", reached[:1] == ["fetch"])

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
