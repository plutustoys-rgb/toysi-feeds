#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rt_order_runner.py — конвеєр замовлень RoyalToys (20.10.2026). Офлайн (підміна зовнішніх викликів), тимчасова БД.
Інваріанти: (1) rt-замовлення НЕ потрапляють у get_orders_ready_to_forward (роутер Toysi їх не бачить, watchdog не алертить), але є в get_rt_orders_pending; delivered/returned/скасовані/sandbox/не оплачені — ні;
(2) map_items: усі rt-, є в supplier_rt.json, артикул/опт/габарити, залишок ≥ qty; змішане/нема/немає залишку → RunnerError; (3) DRY-RUN нічого не створює (ні ТТН, ні замовлення, БД не змінюється);
(4) реальний режим закритий воротами (RT_RUNNER_APPLY, NP_TTN_APPLY, RT_ORDER_SUBMIT, rt_cabinet.FINAL_IMPLEMENTED) — без них жодного створення ТТН; (5) ланцюг станів ttn_created → marking_ready → rt_ordered, ідемпотентний (повторний запуск не створює другу ТТН);
(6) чек не видано → ТТН не створюється; (7) трекінг НП: shipped/delivered/returned (лише за текстом), returned → алерт; (8) персональні дані клієнта в замовлення RoyalToys не передаються."""
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
import rt_order_runner as rr  # noqa: E402
import rt_cabinet  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


SUP = {"rt-1": {"vendor_code": "200513", "cost": 155, "dims_cm": [10, 10, 10], "weight_kg": 0.3, "stock": 9},
       "rt-2": {"vendor_code": "300", "cost": 100, "dims_cm": [5, 5, 5], "weight_kg": None, "stock": 1},
       "rt-3": {"vendor_code": "", "cost": 100, "dims_cm": [5, 5, 5], "stock": 5},
       "rt-4": {"vendor_code": "400", "cost": 100, "dims_cm": None, "stock": 5}}


def new_order(oid, items=None, paid=1, platform="site", pm="prepaid", city="CITY", wh="5", receipt=True, status=None):
    orders_db.init_db()
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": platform, "payment_method": pm, "payment_confirmed": 0, "customer_name": "Іван Петренко", "phone": "380671112233",
                                   "email": "b@e.com", "np_branch": "Київ, Відділення №5", "np_city_ref": city, "np_warehouse_number": wh, "carrier": "nova_poshta",
                                   "items": items or [{"toysi_code": "rt-1", "name": "Бульбашки", "qty": 2, "price": 300}]})
        iid = f"{platform}_{oid}"
        c.execute("UPDATE orders SET payment_confirmed=?, checkbox_ettn_registered_at=?, status=COALESCE(?, status) WHERE internal_order_id=?", (paid, "2026-10-20T10:00:00" if receipt else None, status, iid))
        c.commit()
    return iid


def get(iid):
    with orders_db.get_connection() as c:
        return orders_db._row_to_dict(c.execute("SELECT * FROM orders WHERE internal_order_id=?", (iid,)).fetchone())


a = new_order("A1")
b = new_order("B1", items=[{"toysi_code": "100", "name": "Toysi", "qty": 1, "price": 100}])
cc = new_order("C1", paid=0)
dd = new_order("D1", status="sandbox_test")
with orders_db.get_connection() as c:
    ready = {o["internal_order_id"] for o in orders_db.get_orders_ready_to_forward(c)}
    pend = {o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c)}
chk("get_orders_ready_to_forward: rt-замовлення (A1) НЕ потрапляє (роутер/watchdog їх не бачать), звичайне (B1) — так", a not in ready and b in ready)
chk("get_rt_orders_pending: лише оплачене rt (A1); не rt (B1), не оплачене (C1), sandbox (D1) — ні", pend == {a})

order = get(a)
items = rr.map_items(order, SUP)
chk("map_items: артикул, опт, габарити з supplier_rt.json, qty із замовлення", items[0]["vendor_code"] == "200513" and items[0]["cost"] == 155.0 and items[0]["qty"] == 2 and items[0]["dims_cm"] == [10, 10, 10])


def raises(items_, sup=SUP):
    try:
        rr.map_items({"items": items_}, sup)
        return False
    except rr.RunnerError:
        return True


chk("map_items: змішане / нема в файлі / залишок менший / нема артикулу / нема габаритів / порожньо → RunnerError",
    raises([{"toysi_code": "100", "qty": 1}]) and raises([{"toysi_code": "rt-99", "qty": 1}]) and raises([{"toysi_code": "rt-2", "qty": 2}]) and raises([{"toysi_code": "rt-3", "qty": 1}]) and raises([{"toysi_code": "rt-4", "qty": 1}]) and raises([]))


class Fake:
    def __init__(self, receipt_ok=True, ttn_info=None):
        self.calls = []
        self.receipt_ok = receipt_ok
        self.ttn_info = ttn_info
        self.rt_orders = []

    def tracking(self, ttn):
        self.calls.append("tracking")
        return self.ttn_info

    def ttn_dry_run(self, o):
        self.calls.append("ttn_dry")
        return {"cost": 140}

    def rt_dry_run(self, items):
        self.calls.append("rt_dry")
        return {"ready": True, "cart_total": 310.0, "min_order_note": ["Мінімальна…"]}

    def ensure_receipt(self, conn, o):
        self.calls.append("receipt")
        return self.receipt_ok

    def create_ttn(self, o):
        self.calls.append("create_ttn")
        return {"number": "20451500000001", "ref": "REF-1"}

    def save_marking(self, ref, path):
        self.calls.append("marking")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4 x")

    def rt_order(self, items, ttn, pdf):
        self.calls.append("rt_order")
        self.rt_orders.append({"items": items, "ttn": ttn, "pdf": pdf})
        return {"submitted": True, "rt_order_ref": "RT-77"}


os.environ["RT_MARKING_DIR"] = tempfile.mkdtemp()
rr.MARKING_DIR = Path(os.environ["RT_MARKING_DIR"])
fk = Fake()
with orders_db.get_connection() as c:
    rep = rr.process_order(c, get(a), SUP, apply=False, deps=fk)
chk("DRY-RUN: розрахунок ТТН і перевірка форми RoyalToys є, СТВОРЕННЯ немає (create_ttn/rt_order/marking не викликались), БД не змінена", fk.calls == ["ttn_dry", "rt_dry"] and get(a)["rt_state"] is None and not rep["problems"])
for k in ("RT_RUNNER_APPLY", "NP_TTN_APPLY", "RT_ORDER_SUBMIT"):
    os.environ.pop(k, None)
fk = Fake()
with orders_db.get_connection() as c:
    rep = rr.process_order(c, get(a), SUP, apply=True, deps=fk)
chk("apply=True без воріт → відмова з причиною, жодного створення, rt_error записано", "create_ttn" not in fk.calls and rep["problems"] and "недоступний" in rep["problems"][0] and get(a)["rt_error"] and get(a)["rt_state"] is None)
os.environ.update({"RT_RUNNER_APPLY": "1", "NP_TTN_APPLY": "1", "RT_ORDER_SUBMIT": "1"})
ok, why = rr.apply_enabled()
chk("навіть при трьох змінних реальний режим закритий, поки rt_cabinet.FINAL_IMPLEMENTED=False", ok is False and "кнопку" in why and rt_cabinet.FINAL_IMPLEMENTED is False)
rt_cabinet.FINAL_IMPLEMENTED = True        # лише в тесті
ok, why = rr.apply_enabled()
chk("усі ворота відкриті (тест) → дозволено", ok is True)
fk = Fake(receipt_ok=False)
with orders_db.get_connection() as c:
    rep = rr.process_order(c, get(a), SUP, apply=True, deps=fk)
chk("чек не видано → ТТН НЕ створюється", "create_ttn" not in fk.calls and rep["problems"] and get(a)["rt_state"] is None)
fk = Fake()
with orders_db.get_connection() as c:
    rep = rr.process_order(c, get(a), SUP, apply=True, deps=fk)
st = get(a)
chk("ланцюг: чек → ТТН → наліпка → замовлення RoyalToys; стан rt_ordered, номер RT-77, ТТН збережена у toysi_ttn", fk.calls == ["receipt", "create_ttn", "marking", "rt_order"] and st["rt_state"] == "rt_ordered" and st["rt_order_ref"] == "RT-77" and st["toysi_ttn"] == "20451500000001" and st["rt_ttn_ref"] == "REF-1" and Path(st["rt_marking_path"]).exists())
sent = fk.rt_orders[0]
chk("у замовлення RoyalToys немає ПІБ/телефону клієнта (лише артикули, кількості, опт, ТТН, PDF)", set(sent["items"][0]).isdisjoint({"customer_name", "phone"}) and "customer_name" not in sent and "phone" not in sent)
fk2 = Fake(ttn_info=None)
with orders_db.get_connection() as c:
    rr.process_order(c, get(a), SUP, apply=True, deps=fk2)
chk("повторний запуск ідемпотентний: ТТН/замовлення вдруге НЕ створюються (лише трекінг)", fk2.calls == ["tracking"] and get(a)["rt_state"] == "rt_ordered")
for info, want in (({"status": "Відправлення прямує", "status_code": "5", "delivered": False}, "shipped"), ({"status": "Відправлення отримано", "status_code": "9", "delivered": True}, "delivered")):
    iid = get(a)["internal_order_id"]
    with orders_db.get_connection() as c:
        rr.process_order(c, get(a), SUP, apply=True, deps=Fake(ttn_info=info))
        if want == "shipped":
            chk("трекінг: «прямує» → shipped", get(a)["rt_state"] == "shipped" and get(a)["delivery_status"] == "shipped")
        else:
            chk("трекінг: отримано → delivered, delivery_status=delivered, delivered_at проставлено", get(a)["rt_state"] == "delivered" and get(a)["delivery_status"] == "delivered")
with orders_db.get_connection() as c:
    chk("доставлене замовлення виходить із get_rt_orders_pending", a not in {o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c)})
chk("_tracking_state: «створена/очікує» (1,2,3) — без змін; «Відмова одержувача» → returned; код 104/111 без тексту відмови — НЕ returned",
    rr._tracking_state({"status": "Створено", "status_code": "1", "delivered": False}) == (None, None) and rr._tracking_state({"status": "Відмова одержувача", "status_code": "103", "delivered": False}) == ("returned", "returned") and rr._tracking_state({"status": "Змінено адресу", "status_code": "104", "delivered": False}) == ("shipped", "shipped") and rr._tracking_state(None) == (None, None))
e = new_order("E1", city=None, wh=None)
with orders_db.get_connection() as c:
    rep = rr.process_order(c, get(e), SUP, apply=False, deps=Fake())
chk("нема розпізнаного відділення НП → зупинка з причиною (без дзвінків НП/RoyalToys)", rep["problems"] and "відділення" in rep["problems"][0])
try:
    with orders_db.get_connection() as c:
        orders_db.set_rt_state(c, e, "bogus")
    ok2 = False
except ValueError:
    ok2 = True
chk("set_rt_state: невідомий стан/поле → ValueError", ok2)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
