#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dropt_order_runner.py — конвеєр замовлень Dropt (07.11.2026). Офлайн (підміна зовнішніх викликів), тимчасова БД.
Інваріанти: (1) dropt-замовлення не потрапляють у get_orders_ready_to_forward і в get_rt_orders_pending (RoyalToys), але є в get_dropt_orders_pending; (2) map_items: усі dropt-, є у файлі,
артикул/закупівля, ознака залишку ≥ qty, ціна ≥ закупівлі; (3) DRY-RUN нічого не створює; (4) реальний режим закритий воротами і заглушкою кабінету; (5) повний ланцюг ttn_created → marking_ready → rt_ordered;
(6) мало балансу → ТТН НЕ створюється; мало балансу на фіналі → крок повертається; (7) чек не видано → ТТН нема; (8) STUCK не повторюється; (9) некоректний PDF не йде в кабінет;
(10) трекінг НП; (11) DROPT_RUNNER_SINCE відсікає старі; (12) сервер: ворота SITE_DROPT_ORDERS, лише передплата, лише окремим замовленням, залишок fail-closed; (13) dropt_stock_entries: свіжість файлу й позицій."""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
for k in ("DROPT_RUNNER_APPLY", "NP_TTN_APPLY", "DROPT_ORDER_SUBMIT", "DROPT_RUNNER_SINCE", "SITE_DROPT_ORDERS"):
    os.environ.pop(k, None)
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "site"))
import orders_db  # noqa: E402
import dropt_order_runner as dr  # noqa: E402
import dropt_cabinet  # noqa: E402
import site_order_api as so  # noqa: E402
import stock_snapshot as ss  # noqa: E402

F = []
ALERTS = []
dr._alert = lambda oid, key, text, cooldown=0: ALERTS.append((oid, key))


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


SUP = {"dropt-59298": {"vendor_code": "59298", "cost": 300, "stock": 3},
       "dropt-2": {"vendor_code": "2", "cost": 100, "stock": 1},
       "dropt-3": {"vendor_code": "", "cost": 100, "stock": 3},
       "dropt-4": {"vendor_code": "4", "cost": 0, "stock": 3}}


def new_order(oid, items=None, paid=1, platform="site", pm="prepaid", city="CITY", wh="5", receipt=True, status=None):
    orders_db.init_db()
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": platform, "payment_method": pm, "payment_confirmed": 0, "customer_name": "Іван Петренко", "phone": "380671112233",
                                   "email": "b@e.com", "np_branch": "Київ, Відділення №5", "np_city_ref": city, "np_warehouse_number": wh, "carrier": "nova_poshta",
                                   "items": items or [{"toysi_code": "dropt-59298", "name": "Товар Dropt", "qty": 1, "price": 450}]})
        iid = f"{platform}_{oid}"
        c.execute("UPDATE orders SET payment_confirmed=?, checkbox_ettn_registered_at=?, status=COALESCE(?, status) WHERE internal_order_id=?", (paid, "2026-11-08T10:00:00" if receipt else None, status, iid))
        c.commit()
    return iid


def get(iid):
    with orders_db.get_connection() as c:
        return orders_db._row_to_dict(c.execute("SELECT * FROM orders WHERE internal_order_id=?", (iid,)).fetchone())


a = new_order("A1")
b = new_order("B1", items=[{"toysi_code": "100", "name": "Toysi", "qty": 1, "price": 100}])
r1 = new_order("R1", items=[{"toysi_code": "rt-1", "name": "RT", "qty": 1, "price": 100}])
cc = new_order("C1", paid=0)
dd = new_order("D1", status="sandbox_test")
cod = new_order("COD1", pm="cod", paid=0)
with orders_db.get_connection() as c:
    ready = {o["internal_order_id"] for o in orders_db.get_orders_ready_to_forward(c)}
    pend = {o["internal_order_id"] for o in orders_db.get_dropt_orders_pending(c)}
    rtp = {o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c)}
    codp = {o["internal_order_id"] for o in orders_db.get_dropt_cod_orders(c)}
chk("get_orders_ready_to_forward: dropt (A1, COD1) не потрапляє у Toysi-форвард, Toysi (B1) — так", a not in ready and cod not in ready and b in ready)
chk("get_dropt_orders_pending: лише оплачений dropt (A1); не rt, не Toysi, не оплачений, не sandbox, не cod", pend == {a})
chk("get_rt_orders_pending (RoyalToys) не бачить dropt-замовлень (A1), бачить rt (R1)", a not in rtp and r1 in rtp)
chk("get_dropt_cod_orders: лише dropt + накладений (COD1)", codp == {cod})

items = dr.map_items(get(a), SUP)
chk("map_items: артикул, закупівля з supplier_dropt.json, qty/price із замовлення", items[0]["vendor_code"] == "59298" and items[0]["cost"] == 300.0 and items[0]["qty"] == 1 and items[0]["price"] == 450)


def raises(items_, sup=SUP):
    try:
        dr.map_items({"items": items_}, sup)
        return False
    except dr.RunnerError:
        return True


chk("map_items: змішане / нема у файлі / ознака залишку < qty / нема артикулу / закупівля 0 / порожньо → RunnerError",
    raises([{"toysi_code": "100", "qty": 1, "price": 9}]) and raises([{"toysi_code": "dropt-99", "qty": 1, "price": 9}]) and raises([{"toysi_code": "dropt-2", "qty": 2, "price": 999}])
    and raises([{"toysi_code": "dropt-3", "qty": 1, "price": 999}]) and raises([{"toysi_code": "dropt-4", "qty": 1, "price": 999}]) and raises([]))
chk("map_items: ціна клієнта нижча за закупівлю → RunnerError (збиток)", raises([{"toysi_code": "dropt-59298", "qty": 1, "price": 299}]))
m = dr.map_items({"items": [{"toysi_code": "Dropt-59298", "qty": 1, "price": 450}, {"toysi_code": "dropt-59298", "qty": 2, "price": 450}]}, SUP)
chk("map_items: однакові рядки зливаються, регістр id не важливий", len(m) == 1 and m[0]["qty"] == 3)


class Fake:
    def __init__(self, receipt_ok=True, ttn_info=None, ready=None, order_res=None, pdf=b"%PDF-1.4 x"):
        self.calls = []
        self.receipt_ok = receipt_ok
        self.ttn_info = ttn_info
        self.ready = ready if ready is not None else {"ready": True, "problems": []}
        self.order_res = order_res or {"submitted": True, "dropt_order_ref": "D-77", "total_on_checkout": 300.0, "balance_after": 120.5}
        self.pdf = pdf
        self.orders = []

    def tracking(self, ttn):
        self.calls.append("tracking")
        return self.ttn_info

    def ttn_dry_run(self, o):
        self.calls.append("ttn_dry")
        return {"cost": 140}

    def ensure_receipt(self, conn, o):
        self.calls.append("receipt")
        return self.receipt_ok

    def ensure_cabinet_session(self):
        self.calls.append("session")

    def dropt_dry_run(self, items):
        self.calls.append("dropt_dry")
        return self.ready

    def create_ttn(self, o):
        self.calls.append("create_ttn")
        return {"number": "20451500000001", "ref": "REF-1"}

    def save_marking(self, ref, path):
        self.calls.append("marking")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.pdf)

    def dropt_order(self, items, ttn, pdf):
        self.calls.append("dropt_order")
        self.orders.append({"items": items, "ttn": ttn, "pdf": pdf})
        return self.order_res


os.environ["DROPT_MARKING_DIR"] = tempfile.mkdtemp()
dr.MARKING_DIR = Path(os.environ["DROPT_MARKING_DIR"])


def run_one(iid, fk, apply):
    with orders_db.get_connection() as c:
        return dr.process_order(c, get(iid), SUP, apply=apply, deps=fk)


fk = Fake()
rep = run_one(a, fk, apply=False)
chk("DRY-RUN: чек + розрахунок ТТН; кабінет Dropt НЕ чіпається, ТТН/замовлення не створюються, БД не змінена",
    fk.calls == ["receipt", "ttn_dry"] and get(a)["rt_state"] is None and not get(a)["toysi_ttn"] and not rep["problems"])

# реальний режим закрито воротами, навіть якщо кабінет «підключено»
dropt_cabinet.final_implemented = lambda: True
fk = Fake()
rep = run_one(a, fk, apply=True)
chk("apply без воріт: жодних створень, причина називає ворота", "create_ttn" not in fk.calls and "dropt_order" not in fk.calls and rep["problems"] and "DROPT_RUNNER_APPLY" in rep["problems"][0])
os.environ.update(DROPT_RUNNER_APPLY="1", NP_TTN_APPLY="1", DROPT_ORDER_SUBMIT="1")
fk = Fake()
rep = run_one(a, fk, apply=True)
chk("apply без DROPT_RUNNER_SINCE: закрито", "create_ttn" not in fk.calls and rep["problems"] and "DROPT_RUNNER_SINCE" in rep["problems"][0])
os.environ["DROPT_RUNNER_SINCE"] = "2026-11-08"
dropt_cabinet.final_implemented = lambda: False
fk = Fake()
rep = run_one(a, fk, apply=True)
chk("apply з усіма воротами, але кабінет — заглушка: закрито", "create_ttn" not in fk.calls and rep["problems"] and "не підключено" in rep["problems"][0])
dropt_cabinet.final_implemented = lambda: True

# чек не видано
fk = Fake(receipt_ok=False)
rep = run_one(a, fk, apply=True)
chk("чек не видано → ТТН не створюється", "create_ttn" not in fk.calls and rep["problems"] and "чек" in rep["problems"][0])

# мало балансу ДО ТТН
ALERTS.clear()
fk = Fake(ready={"ready": False, "low_balance": True, "problems": ["баланс 10 < 300"]})
rep = run_one(a, fk, apply=True)
chk("мало балансу в preflight → ТТН НЕ створюється, стан не змінився, алерт low_balance", "create_ttn" not in fk.calls and get(a)["rt_state"] is None and (a, "low_balance") in ALERTS)

# повний ланцюг
ALERTS.clear()
fk = Fake()
rep = run_one(a, fk, apply=True)
row = get(a)
chk("повний ланцюг: порядок кроків чек → сесія → preflight → ТТН → наліпка → замовлення", fk.calls == ["receipt", "ttn_dry", "ttn_dry", "session", "dropt_dry", "create_ttn", "marking", "dropt_order"] or
    [x for x in fk.calls if x in ("receipt", "session", "dropt_dry", "create_ttn", "marking", "dropt_order")] == ["receipt", "session", "dropt_dry", "create_ttn", "marking", "dropt_order"])
chk("повний ланцюг: стан rt_ordered, ТТН, номер Dropt, сума, шлях наліпки у БД", row["rt_state"] == "rt_ordered" and row["toysi_ttn"] == "20451500000001" and row["rt_order_ref"] == "D-77" and row["rt_total"] == 300.0 and str(row["rt_marking_path"]).endswith("site_A1.pdf"))
chk("в кабінет передано артикул, qty, закупівлю й НАШУ ціну продажу, ТТН і PDF", fk.orders and fk.orders[0]["items"][0] == {"toysi_code": "dropt-59298", "qty": 1, "price": 450, "name": "Товар Dropt", "vendor_code": "59298", "cost": 300.0}
    and fk.orders[0]["ttn"] == "20451500000001" and fk.orders[0]["pdf"].endswith("site_A1.pdf"))
chk("алерт ✅ «ordered» після оформлення", (a, "ordered") in ALERTS)
before = list(fk.calls)
rep2 = run_one(a, fk, apply=True)
chk("повторний запуск rt_ordered не створює нічого вдруге (лише трекінг)", [x for x in fk.calls[len(before):]] == ["tracking"])

# трекінг НП
for info, want in (({"status": "Відправлено", "status_code": "4"}, "shipped"), ({"status": "Отримано", "status_code": "9", "delivered": True}, "delivered")):
    fk2 = Fake(ttn_info=info)
    run_one(a, fk2, apply=True)
    chk(f"трекінг НП → {want}", get(a)["rt_state"] == want)
b2 = new_order("A2")
fk2 = Fake()
run_one(b2, fk2, apply=True)
fk2 = Fake(ttn_info={"status": "Відмова одержувача", "status_code": "102"})
ALERTS.clear()
run_one(b2, fk2, apply=True)
chk("трекінг НП: «відмов…» → returned + алерт", get(b2)["rt_state"] == "returned" and (b2, "returned") in ALERTS)

# низький баланс НА фіналі: ТТН створено, крок повертається
a3 = new_order("A3")
fk3 = Fake(order_res={"submitted": False, "not_submitted_certain": True, "low_balance": True, "problems": ["баланс замалий"]})
ALERTS.clear()
rep = run_one(a3, fk3, apply=True)
chk("мало балансу на фіналі → стан повернуто в marking_ready (ТТН живе), алерт, збій у звіті", get(a3)["rt_state"] == "marking_ready" and get(a3)["toysi_ttn"] and (a3, "low_balance") in ALERTS and rep["problems"])
fk3b = Fake()
rep = run_one(a3, fk3b, apply=True)
chk("наступний цикл: з marking_ready оформлює без другої ТТН", "create_ttn" not in fk3b.calls and get(a3)["rt_state"] == "rt_ordered" and get(a3)["rt_order_ref"] == "D-77")

# невідомий результат кліку: лишається rt_ordering, конвеєр сам не повторює
a4 = new_order("A4")
fk4 = Fake(order_res={"submitted": False, "problems": ["таймаут після кліку"]})
rep = run_one(a4, fk4, apply=True)
chk("результат кліку невідомий → стан rt_ordering, rt_error записано", get(a4)["rt_state"] == "rt_ordering" and rep["problems"])
fk4b = Fake()
rep = run_one(a4, fk4b, apply=True)
chk("STUCK (rt_ordering): конвеєр сам НЕ повторює, нічого не створює, пише причину", "dropt_order" not in fk4b.calls and "create_ttn" not in fk4b.calls and rep["problems"] and "результат невідомий" in rep["problems"][0])

# некоректна наліпка
a5 = new_order("A5")
fk5 = Fake(pdf=b"not a pdf")
rep = run_one(a5, fk5, apply=True)
chk("некоректний PDF не йде в Dropt: стан ttn_created, замовлення не оформлено", "dropt_order" not in fk5.calls and get(a5)["rt_state"] == "ttn_created" and rep["problems"])
a6 = new_order("A6")
fk6 = Fake(pdf=b"%PDF-" + b"x" * (3 * 1024 * 1024))
rep = run_one(a6, fk6, apply=True)
chk("PDF > 2 МБ не йде в Dropt", "dropt_order" not in fk6.calls and get(a6)["rt_state"] == "ttn_created")

# відділення НП / ПІБ
a7 = new_order("A7", city=None, wh=None)
fk7 = Fake()
rep = run_one(a7, fk7, apply=True)
chk("нема відділення НП → ТТН не створюється", "create_ttn" not in fk7.calls and rep["problems"])

# run(): DROPT_RUNNER_SINCE відсікає старіші замовлення; dry-run за замовчуванням
os.environ.pop("DROPT_RUNNER_APPLY")
a8 = new_order("A8")
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET created_at='2026-10-01T10:00:00' WHERE internal_order_id=?", (a8,))
    c.commit()
supp_path = Path(tempfile.mkdtemp()) / "supplier_dropt.json"
supp_path.write_text(json.dumps({"updated": datetime.now(timezone.utc).isoformat(), "items": SUP}), encoding="utf-8")
os.environ["SUPPLIER_DROPT_FILE"] = str(supp_path)
import dropt_supplier_export as dse  # noqa: E402
dse.OUT_FILE = supp_path
fk8 = Fake()
reps = dr.run(apply=False, deps=fk8)
touched = {r.get("order") for r in reps}
chk("run(): старіше за DROPT_RUNNER_SINCE (A8) не чіпається; нові без стану — у dry-run без створень", a8 not in touched and "create_ttn" not in fk8.calls and "dropt_order" not in fk8.calls)

# ---- сервер: ворота, лише передплата, лише окремо, fail-closed залишок ----
so.price_map = lambda: {"100": {"price": 250, "name": "Тест"}, "rt-101": {"price": 400, "name": "RT"}, "dropt-59298": {"price": 450, "name": "Dropt-товар"}}
so.stock_snapshot = lambda: ({"100": 5, "rt-101": 3, "dropt-59298": 3}, None)
base = {"name": "Іван Петренко", "phone": "0501234567", "city_name": "Київ", "warehouse_name": "відділення 5"}
D = [{"id": "dropt-59298", "qty": 1}]


def oe(fn):
    try:
        fn()
    except so.OrderError:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


chk("ворота SITE_DROPT_ORDERS закриті (за замовчуванням) → dropt- відхиляється, навіть prepaid", oe(lambda: so.build_order(dict(base, items=D, payment_method="prepaid"))))
os.environ["SITE_DROPT_ORDERS"] = "1"
o, t = so.build_order(dict(base, items=D, payment_method="prepaid"))
chk("ворота відкриті: dropt- + prepaid проходить, сума = ціна × qty", o["payment_method"] == "prepaid" and t == 450 and o["items"][0]["toysi_code"] == "dropt-59298")
chk("ворота відкриті: dropt- + cod → відмова", oe(lambda: so.build_order(dict(base, items=D, payment_method="cod"))))
chk("ворота відкриті: dropt- + спосіб не вказано → відмова (fail-closed)", oe(lambda: so.build_order(dict(base, items=D))))
chk("ворота відкриті: dropt- + Toysi / dropt- + rt- → відмова (лише окремим замовленням)", oe(lambda: so.build_order(dict(base, items=[{"id": "100", "qty": 1}] + D, payment_method="prepaid")))
    and oe(lambda: so.build_order(dict(base, items=[{"id": "rt-101", "qty": 1}] + D, payment_method="prepaid"))))
so.stock_snapshot = lambda: (None, None)
chk("знімка залишків нема/застарів → dropt- «немає» (fail-closed)", oe(lambda: so.build_order(dict(base, items=D, payment_method="prepaid"))) or bool(so.check_stock([{"toysi_code": "dropt-59298", "name": "x", "qty": 1}])))
so.stock_snapshot = lambda: ({"100": 5}, None)
chk("є знімок, але ключа dropt- у ньому нема → «немає»", bool(so.check_stock([{"toysi_code": "dropt-59298", "name": "x", "qty": 1}])))
so.stock_snapshot = lambda: ({"dropt-59298": 3}, None)
chk("залишок 3, замовлено 4 → StockChanged-рядок", bool(so.check_stock([{"toysi_code": "dropt-59298", "name": "x", "qty": 4}])))
os.environ.pop("SITE_DROPT_ORDERS")

# ---- dropt_stock_entries ----
now = datetime.now(timezone.utc)
fresh = {"updated": now.isoformat(), "items": {"dropt-1": {"stock": 3, "checked_at": int(now.timestamp()) - 600}, "dropt-2": {"stock": 3, "checked_at": int(now.timestamp()) - 5 * 3600}, "dropt-3": {"stock": "x"}}}
pf = Path(tempfile.mkdtemp()) / "s.json"
pf.write_text(json.dumps(fresh), encoding="utf-8")
e = ss.dropt_stock_entries(path=pf)
chk("dropt_stock_entries: свіжа позиція є; стара позиція (5 год) і нечислова — ні", e == {"dropt-1": 3})
old = dict(fresh, updated=datetime.fromtimestamp(now.timestamp() - 4 * 3600, timezone.utc).isoformat())
pf.write_text(json.dumps(old), encoding="utf-8")
chk("dropt_stock_entries: файл старший за 3 год → порожньо (fail-closed)", ss.dropt_stock_entries(path=pf) == {})
chk("dropt_stock_entries: нема файлу → порожньо", ss.dropt_stock_entries(path=Path(tempfile.mkdtemp()) / "none.json") == {})

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
