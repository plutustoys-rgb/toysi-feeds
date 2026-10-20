#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rt_order_runner.py — конвеєр замовлень RoyalToys (20.10.2026). Офлайн (підміна зовнішніх викликів), тимчасова БД.
Інваріанти: (1) rt-замовлення НЕ потрапляють у get_orders_ready_to_forward (роутер Toysi їх не бачить, watchdog не алертить), але є в get_rt_orders_pending; delivered/returned/скасовані/sandbox/не оплачені — ні;
(2) map_items: усі rt-, є в supplier_rt.json, артикул/опт/габарити, залишок ≥ qty; змішане/нема/немає залишку → RunnerError; (3) DRY-RUN нічого не створює (ні ТТН, ні замовлення, БД не змінюється);
(4) реальний режим закритий воротами (RT_RUNNER_APPLY, NP_TTN_APPLY, RT_ORDER_SUBMIT, RT_RUNNER_SINCE; режим фіналу reserve підключено, balance — ні) — без них жодного створення ТТН; (5) ланцюг станів ttn_created → marking_ready → rt_ordered, ідемпотентний (повторний запуск не створює другу ТТН);
(6) чек не видано → ТТН не створюється; (7) трекінг НП: shipped/delivered/returned (лише за текстом), returned → алерт; (8) ПІБ і телефон отримувача передаються в RoyalToys (обов'язкові; власник 20.10)."""
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ.pop("RT_AUTO_CONFIRM", None)      # брама автопідтвердження в оточенні не має впливати на старі тести
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

    def rt_dry_run(self, items, recipient=None):
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

    def rt_order(self, items, ttn, pdf, recipient=None):
        self.calls.append("rt_order")
        self.rt_orders.append({"items": items, "ttn": ttn, "pdf": pdf, "recipient": recipient})
        return {"submitted": True, "rt_order_ref": "RT-77"}


os.environ["RT_MARKING_DIR"] = tempfile.mkdtemp()
rr.MARKING_DIR = Path(os.environ["RT_MARKING_DIR"])
fk = Fake()
with orders_db.get_connection() as c:
    rep = rr.process_order(c, get(a), SUP, apply=False, deps=fk)
chk("DRY-RUN: чек (як було в роутері) + розрахунок ТТН; кабінет RoyalToys НЕ чіпається (спільний кошик), СТВОРЕННЯ немає, БД не змінена", fk.calls == ["receipt", "ttn_dry"] and get(a)["rt_state"] is None and not rep["problems"])
for k in ("RT_RUNNER_APPLY", "NP_TTN_APPLY", "RT_ORDER_SUBMIT"):
    os.environ.pop(k, None)
fk = Fake()
with orders_db.get_connection() as c:
    rep = rr.process_order(c, get(a), SUP, apply=True, deps=fk)
chk("apply=True без воріт → відмова з причиною, жодного створення (ні ТТН, ні кабінету), rt_error записано", fk.calls == ["receipt", "ttn_dry"] and rep["problems"] and "недоступний" in rep["problems"][0] and get(a)["rt_error"] and get(a)["rt_state"] is None)
os.environ.update({"RT_RUNNER_APPLY": "1", "NP_TTN_APPLY": "1", "RT_ORDER_SUBMIT": "1"})
ok, why = rr.apply_enabled()
chk("без RT_RUNNER_SINCE реальний режим закритий (старі замовлення, виконані вручну, не чіпаємо)", ok is False and "RT_RUNNER_SINCE" in why)
os.environ["RT_RUNNER_SINCE"] = "2026-01-01"
ok, why = rr.apply_enabled()
os.environ["RT_ORDER_MODE"] = "balance"
ok_b, why_b = rr.apply_enabled()
os.environ.pop("RT_ORDER_MODE")
chk("режим balance («З балансу» не підключено) → реальний режим закритий навіть з усіма змінними", ok_b is False and "не підключено" in why_b)
ok, why = rr.apply_enabled()
chk("усі ворота відкриті (тест) → дозволено", ok is True)
fk = Fake(receipt_ok=False)
with orders_db.get_connection() as c:
    rep = rr.process_order(c, get(a), SUP, apply=True, deps=fk)
chk("чек не видано → ТТН НЕ створюється", fk.calls == ["receipt"] and rep["problems"] and get(a)["rt_state"] is None)
fk = Fake(receipt_ok=False)
with orders_db.get_connection() as c:
    rr.process_order(c, get(a), SUP, apply=False, deps=fk)
chk("B1: у dry-run чек теж виписується (як робив роутер до PR) — інакше rt-замовлення без чека", fk.calls == ["receipt"])
fk = Fake()
with orders_db.get_connection() as c:
    rep = rr.process_order(c, get(a), SUP, apply=True, deps=fk)
st = get(a)
chk("ланцюг: чек → ТТН → наліпка → замовлення RoyalToys; стан rt_ordered, номер RT-77, ТТН збережена у toysi_ttn", fk.calls == ["receipt", "ttn_dry", "rt_dry", "create_ttn", "marking", "rt_order"] and st["rt_state"] == "rt_ordered" and st["rt_order_ref"] == "RT-77" and st["toysi_ttn"] == "20451500000001" and st["rt_ttn_ref"] == "REF-1" and Path(st["rt_marking_path"]).exists())
sent = fk.rt_orders[0]
chk("ПІБ і телефон отримувача ПЕРЕДАЮТЬСЯ в RoyalToys (обов'язкові; власник 20.10)", sent["recipient"] == ("Іван Петренко", "+380671112233"))
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

# ---- аудит #897: A1–A5, M1, M3 ----
class DbFail(Fake):
    """create_ttn пройшов, а запис у БД після нього падає."""
    def create_ttn(self, o):
        r = super().create_ttn(o)
        real = orders_db.set_rt_state

        def boom(*a_, **k_):
            orders_db.set_rt_state = real
            raise RuntimeError("database is locked")
        orders_db.set_rt_state = boom
        return r


f1 = new_order("F1")
fk = DbFail()
with orders_db.get_connection() as c:
    r1 = rr.process_order(c, get(f1), SUP, apply=True, deps=fk)
st1 = get(f1)["rt_state"]
fk_b = Fake()
with orders_db.get_connection() as c:
    r2 = rr.process_order(c, get(f1), SUP, apply=True, deps=fk_b)
chk("A1: збій запису після create_ttn -> стан лишається ttn_creating, наступний запуск НЕ створює другу ТТН", st1 == "ttn_creating" and "create_ttn" not in fk_b.calls and r2["problems"] and "результат невідомий" in r2["problems"][0] and get(f1)["rt_state"] == "ttn_creating")
f2 = new_order("F2")
with orders_db.get_connection() as c:
    chk("A1: claim атомарний (другий claim з того ж стану — False)", orders_db.claim_rt_state(c, f2, None, "ttn_creating") is True and orders_db.claim_rt_state(c, f2, None, "ttn_creating") is False)

g = new_order("G1")
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET toysi_ttn='20450000000001' WHERE internal_order_id=?", (g,))
    c.commit()
    pend = {o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c)}
chk("A2: замовлення з уже внесеною вручну ТТН (rt_state NULL) конвеєр не бере (не дублює відправку)", g not in pend)

h = new_order("H1")
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET refund_receipt_at='2026-10-20T10:00:00' WHERE internal_order_id=?", (h,))
    c.commit()
    pend = {o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c)}
chk("A3: повернене через LiqPay (чек повернення) замовлення НЕ у черзі", h not in pend)

# A4: п'ять посилок у дорозі не блокують нове замовлення
shipped_ids = [new_order(f"S{n}") for n in range(5)]
with orders_db.get_connection() as c:
    for iid_ in (a, f1, f2):
        orders_db.set_rt_state(c, iid_, "manual")
    for n, iid in enumerate(shipped_ids):
        orders_db.set_rt_state(c, iid, "shipped", toysi_ttn=f"2045000000010{n}")
    c.commit()
new1 = new_order("N1")
fk = Fake(ttn_info={"status": "Відправлення прямує", "status_code": "5", "delivered": False})
rr.MAX_ORDERS_PER_RUN = 5
rr.load_supplier = lambda path=None: SUP
reps = rr.run(apply=False, deps=fk)
chk("A4: 5 посилок у дорозі + нове замовлення -> нове оброблено (ліміт лише на дії, не на трекінг)", any(r.get("order") == new1 for r in reps) and sum(1 for r in reps if r.get("state") == "shipped") == 5)
with orders_db.get_connection() as c:
    pend = {o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c)}
chk("A2: стан manual — термінальний", not ({a, f1, f2} & pend))

# A5: однакові рядки зливаються, залишок — по сумі
try:
    rr.map_items({"items": [{"toysi_code": "rt-2", "qty": 1, "price": 300}, {"toysi_code": "rt-2", "qty": 1, "price": 300}]}, SUP)
    ok5 = False
except rr.RunnerError:
    ok5 = True
m = rr.map_items({"items": [{"toysi_code": "rt-1", "qty": 1, "price": 300}, {"toysi_code": "rt-1", "qty": 2, "price": 300}]}, SUP)
chk("A5: два рядки одного rt- зливаються (qty 3), залишок по сумі (1+1 при залишку 1 -> відмова)", ok5 and len(m) == 1 and m[0]["qty"] == 3)


class RtNotReady(Fake):
    def rt_dry_run(self, items, recipient=None):
        self.calls.append("rt_dry")
        return {"ready": False, "problems": ["сума"]}


p1 = new_order("P1")
fk = RtNotReady()
with orders_db.get_connection() as c:
    rp = rr.process_order(c, get(p1), SUP, apply=True, deps=fk)
chk("A5: кабінет RoyalToys не готовий -> ТТН НЕ створюється (без ТТН-сироти), стан NULL", "create_ttn" not in fk.calls and rp["problems"] and get(p1)["rt_state"] is None)
try:
    rr.map_items({"items": [{"toysi_code": "rt-1", "qty": 1, "price": 100}]}, SUP)
    ok6 = False
except rr.RunnerError:
    ok6 = True
chk("M3: клієнт сплатив менше за поточний опт (збиток) -> відмова, ручне рішення", ok6)
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, new1, "delivered")
    c.commit()
chk("M1: delivered -> delivered_at проставлено", get(new1)["delivered_at"])

# ---- повторний аудит #897: N1–N4 ----
n1 = new_order("NN1", city=None, wh=None)
n1b = new_order("NN2", items=[{"toysi_code": "rt-99", "name": "зник", "qty": 1, "price": 300}])
n1c = new_order("NN3", items=[{"toysi_code": "rt-1", "name": "збиток", "qty": 1, "price": 100}])
for iid_ in (n1, n1b, n1c):
    fk = Fake()
    with orders_db.get_connection() as c:
        rr.process_order(c, get(iid_), SUP, apply=False, deps=fk)
    chk(f"N1: {iid_} — чек виписується ПЕРШИМ кроком навіть коли замовлення не проходить перевірки (відділення/зник/збиток)", fk.calls[:1] == ["receipt"])

# N2: ліміт кроків не лишає решту без чека
rr.MAX_ORDERS_PER_RUN = 2
with orders_db.get_connection() as c:
    for iid_ in [x["internal_order_id"] for x in orders_db.get_rt_orders_pending(c)]:
        orders_db.set_rt_state(c, iid_, "manual")
    c.commit()
many = [new_order(f"W{n}") for n in range(5)]
rcpt = []


class Rc(Fake):
    def ensure_receipt(self, conn, o):
        rcpt.append(o["internal_order_id"])
        return True


rr.run(apply=False, deps=Rc())
chk("N2: 5 нових замовлень при ліміті 2 — чек (ensure_receipt) отримали ВСІ 5", set(many) <= set(rcpt))
rr.MAX_ORDERS_PER_RUN = 5

# N3: дата-помилка закриває ворота
os.environ["RT_RUNNER_SINCE"] = "20.10.2026"
ok, why = rr.apply_enabled()
os.environ["RT_RUNNER_SINCE"] = "2026-10-21"
ok2_, _ = rr.apply_enabled()
chk("N3: RT_RUNNER_SINCE=20.10.2026 (не ISO) -> ворота закриті; 2026-10-21 -> проходить цю перевірку", ok is False and "ISO" in why and ok2_ is True)
os.environ["RT_RUNNER_SINCE"] = "2026-01-01"


# N4: відкат rt_ordering -> marking_ready лише за явною ознакою «точно не надіслано»
class RtAmbiguous(Fake):
    def rt_order(self, items, ttn, pdf, recipient=None):
        self.calls.append("rt_order")
        return {"submitted": False, "problems": ["збій"]}


class RtCertain(Fake):
    def rt_order(self, items, ttn, pdf, recipient=None):
        self.calls.append("rt_order")
        return {"submitted": False, "not_submitted_certain": True, "problems": ["баланс"]}


q1 = new_order("Q1")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, q1, "marking_ready", toysi_ttn="20451500000009", rt_ttn_ref="R9", rt_marking_path=str(rr.MARKING_DIR / "x.pdf"))
    c.commit()
with orders_db.get_connection() as c:
    rr.process_order(c, get(q1), SUP, apply=True, deps=RtAmbiguous())
chk("N4: невизначений збій після кроку -> стан ЛИШАЄТЬСЯ rt_ordering (повтор не автоматичний)", get(q1)["rt_state"] == "rt_ordering")
q2 = new_order("Q2")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, q2, "marking_ready", toysi_ttn="20451500000010", rt_ttn_ref="R10", rt_marking_path=str(rr.MARKING_DIR / "x.pdf"))
    c.commit()
with orders_db.get_connection() as c:
    rr.process_order(c, get(q2), SUP, apply=True, deps=RtCertain())
chk("N4: явне «точно не надіслано» (до кліку) -> відкат у marking_ready", get(q2)["rt_state"] == "marking_ready")

# N7: apply без дійсного SINCE — нові замовлення все одно отримують чек, замовлення посеред ланцюга дають алерт
os.environ["RT_RUNNER_SINCE"] = "20.10.2026"
n7 = new_order("N7")
rcpt7 = []


class Rc7(Fake):
    def ensure_receipt(self, conn, o):
        rcpt7.append(o["internal_order_id"])
        return True


rr.run(apply=True, deps=Rc7())
chk("N7: apply без дійсного RT_RUNNER_SINCE -> нове оплачене замовлення все одно отримує чек (і не береться в роботу)", n7 in rcpt7 and get(n7)["rt_state"] is None)
os.environ["RT_RUNNER_SINCE"] = "2026-01-01"

# ПІБ/телефон обов'язкові: некоректні → стоп ДО ТТН
bad_r = new_order("RC1")
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET customer_name='Іван', phone='123' WHERE internal_order_id=?", (bad_r,))
    c.commit()
fk = Fake()
with orders_db.get_connection() as c:
    rr_bad = rr.process_order(c, get(bad_r), SUP, apply=True, deps=fk)
chk("без повного ПІБ / з поганим телефоном -> стоп ДО створення ТТН (без ТТН-сироти), RunnerError з причиною", "create_ttn" not in fk.calls and rr_bad["problems"] and "ПІБ" in rr_bad["problems"][0] and get(bad_r)["rt_state"] is None)
chk("_recipient: нормалізує телефон 0671112233 -> +380671112233", rr._recipient({"customer_name": "Іван Петренко", "phone": "0671112233"}) == ("Іван Петренко", "+380671112233"))
# OrderError, що вийшла з order_flow (до кліку: сесія/автовхід/ворота), -> «точно не відправлено», а не вічне rt_ordering
_orig = rt_cabinet.order_flow
try:
    def _boom(*a, **k):
        raise rt_cabinet.OrderError("сесія кабінету не діє")
    rt_cabinet.order_flow = _boom
    r_oe = rr._Deps().rt_order([{"vendor_code": "MK 6039", "qty": 1, "cost": 20.0}], "20451565972503", "/x.pdf", None)
finally:
    rt_cabinet.order_flow = _orig
chk("OrderError з order_flow -> not_submitted_certain=True (крок відкочується, не застрягає у rt_ordering)", r_oe.get("not_submitted_certain") is True and not r_oe.get("submitted") and r_oe["problems"] == ["сесія кабінету не діє"])
class NoSession(Fake):
    def ensure_cabinet_session(self):
        self.calls.append("ensure_session")
        raise RuntimeError("капча")


ns1 = new_order("NS1")
fk = NoSession()
with orders_db.get_connection() as c:
    rns = rr.process_order(c, get(ns1), SUP, apply=True, deps=fk)
chk("B1 (аудит #923): нове замовлення (state NULL), автовхід не вдався -> ТТН НЕ створюється, кабінет не чіпаємо, стан NULL, причина в problems",
    fk.calls.count("ensure_session") == 1 and "create_ttn" not in fk.calls and "rt_dry" not in fk.calls and get(ns1)["rt_state"] is None and any("недоступний" in x for x in rns["problems"]))
class OkSession(Fake):
    def ensure_cabinet_session(self):
        self.calls.append("ensure_session")


ns2 = new_order("NS2")
fk = OkSession()
with orders_db.get_connection() as c:
    rr.process_order(c, get(ns2), SUP, apply=True, deps=fk)
chk("B1: автовхід (або жива сесія) ок -> йде далі: ensure_session ПЕРЕД rt_dry і ТТН", fk.calls.index("ensure_session") < fk.calls.index("rt_dry") < fk.calls.index("create_ttn"))
# ---- повернення: повідомлення RoyalToys про ТТН повернення (20.10) ----
class RetFake(Fake):
    def __init__(self, info, send_ok=True):
        super().__init__()
        self.ttn_info = info
        self.send_ok = send_ok
        self.sent = []

    def notify_rt_return(self, text):
        self.sent.append(text)
        return self.send_ok


rt1 = new_order("RET1")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, rt1, "returned", rt_order_ref="RT000773594", toysi_ttn="20451565972503")
    c.commit()
fk = RetFake({"status": "Відмова одержувача", "status_code": "102", "delivered": False, "return_ttn": None})
with orders_db.get_connection() as c:
    o1 = rr.notify_returns(c, fk, True)
chk("повернення без ТТН повернення в НП: не повідомляємо RoyalToys, не позначаємо, повторимо наступного циклу", fk.sent == [] and get(rt1)["rt_return_notified_at"] is None and o1[0].get("return_notice"))
fk = RetFake({"status": "Відмова одержувача", "status_code": "102", "delivered": False, "return_ttn": "20451570000001"}, send_ok=False)
with orders_db.get_connection() as c:
    o2 = rr.notify_returns(c, fk, False)
chk("dry-run (apply=False): нічого не шлемо RoyalToys і не позначаємо", fk.sent == [] and get(rt1)["rt_return_notified_at"] is None)
with orders_db.get_connection() as c:
    o3 = rr.notify_returns(c, fk, True)
chk("apply: текст для RoyalToys з № замовлення RT і обома ТТН, без ПІБ/телефону; автонадсилання не вдалось → все одно позначено (власник отримав алерт з текстом)",
    len(fk.sent) == 1 and "RT000773594" in fk.sent[0] and "20451565972503" in fk.sent[0] and "20451570000001" in fk.sent[0] and "Петренко" not in fk.sent[0] and get(rt1)["rt_return_notified_at"] and o3[0]["sent_to_rt"] is False)
fk2 = RetFake({"status": "Відмова", "delivered": False, "return_ttn": "20451570000001"})
with orders_db.get_connection() as c:
    o4 = rr.notify_returns(c, fk2, True)
chk("повторно (вже повідомлено) — нічого не шлемо вдруге", fk2.sent == [] and o4 == [])

import nova_poshta as _np
import telegram_userbot_client as _ub
_orig_call = _np._call
try:
    _np._call = lambda m, c, p: [{"Status": "Відмова одержувача", "StatusCode": "102", "ActualDeliveryDate": "", "LastCreatedOnTheBasisNumber": "20451570000001", "LastCreatedOnTheBasisDocumentType": "CargoReturn"}]
    r_a = _np.get_tracking_status("20451565972503")
    _np._call = lambda m, c, p: [{"Status": "x", "LastCreatedOnTheBasisNumber": "20451565972503", "LastCreatedOnTheBasisDocumentType": "CargoReturn"}]
    r_b = _np.get_tracking_status("20451565972503")
    _np._call = lambda m, c, p: [{"Status": "x", "LastCreatedOnTheBasisNumber": "123", "LastCreatedOnTheBasisDocumentType": "CargoReturn"}]
    r_c = _np.get_tracking_status("20451565972503")
    _np._call = lambda m, c, p: [{"Status": "Відмова", "LastCreatedOnTheBasisNumber": "20451570000009", "LastCreatedOnTheBasisDocumentType": "Redirecting"}]
    r_e = _np.get_tracking_status("20451565972503")
    r_c = _np.get_tracking_status("20451565972503")
    _np._call = lambda m, c, p: [{"Status": "x"}]
    r_d = _np.get_tracking_status("20451565972503")
finally:
    _np._call = _orig_call
chk("get_tracking_status.return_ttn: 14 цифр + тип документа «повернення», не збігається з вихідною -> береться; збіг із вихідною / не 14 цифр / нема / інший тип (переадресація) -> None (based_* для діагностики)", r_a["return_ttn"] == "20451570000001" and r_b["return_ttn"] is None and r_c["return_ttn"] is None and r_d["return_ttn"] is None
    and r_e["return_ttn"] is None and r_e["based_ttn"] == "20451570000009" and r_e["based_type"] == "Redirecting")
os.environ.pop("TELEGRAM_USERBOT_RT_TARGET", None)
try:
    _ub.send_rt_notice("текст")
    ub_ok = False
except _ub.UserbotError:
    ub_ok = True
chk("send_rt_notice без TELEGRAM_USERBOT_RT_TARGET -> UserbotError, нічого не йде", ub_ok)
# аудит #927: notify_returns викликається з run() навіть коли інших замовлень «у роботі» немає
rt2 = new_order("RET2")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, rt2, "returned", rt_order_ref="RT000773595", toysi_ttn="20451565972999")
    c.commit()
fk3 = RetFake({"status": "Відмова", "delivered": False, "return_ttn": "20451570000002"})
rr.run(apply=True, deps=fk3)
chk("run(): returned-замовлення без жодного іншого в роботі — повідомлення RoyalToys усе одно надіслано й позначено", any("RT000773595" in t for t in fk3.sent) and get(rt2)["rt_return_notified_at"])
# ---- автопідтвердження «З балансу» (рішення власника 20.10) ----
class AC(Fake):
    def __init__(self, conf):
        super().__init__()
        self.conf = list(conf)
        self.conf_calls = []

    def rt_order(self, items, ttn, pdf, recipient=None):
        self.calls.append("rt_order")
        return {"submitted": True, "rt_order_ref": "RT000900001", "total_on_checkout": 618.0}

    def rt_confirm(self, rt_ref, total):
        self.conf_calls.append((rt_ref, total))
        return self.conf.pop(0)


def _with_gate(val):
    if val is None:
        os.environ.pop("RT_AUTO_CONFIRM", None)
    else:
        os.environ["RT_AUTO_CONFIRM"] = val


_with_gate(None)
ac0 = new_order("AC0")
fk = AC([])
with orders_db.get_connection() as c:
    rr.process_order(c, get(ac0), SUP, apply=True, deps=fk)
chk("авто-брама RT_AUTO_CONFIRM вимкнена: резерв створено, підтвердження НЕ пробується (як було), rt_total збережено", fk.conf_calls == [] and get(ac0)["rt_state"] == "rt_ordered" and get(ac0)["rt_confirmed_at"] is None and get(ac0)["rt_total"] == 618.0)
_with_gate("1")
ac1 = new_order("AC1")
fk = AC([{"confirmed": True, "balance_before": 1000.0, "balance_after": 1000.0}])
with orders_db.get_connection() as c:
    rep1 = rr.process_order(c, get(ac1), SUP, apply=True, deps=fk)
chk("авто-брама увімкнена: після резерву одразу «З балансу» з номером RT і збереженою сумою; rt_confirmed_at проставлено", fk.conf_calls == [("RT000900001", 618.0)] and get(ac1)["rt_confirmed_at"] and not rep1["problems"])
fk = AC([])
with orders_db.get_connection() as c:
    rr.process_order(c, get(ac1), SUP, apply=True, deps=fk)
chk("уже підтверджене (rt_confirmed_at) — повторно не підтверджуємо", fk.conf_calls == [])
ac2 = new_order("AC2")
fk = AC([{"confirmed": False, "low_balance": True, "problems": ["недостатньо балансу: доступно 10, потрібно 618"]}])
with orders_db.get_connection() as c:
    rep2 = rr.process_order(c, get(ac2), SUP, apply=True, deps=fk)
chk("мало балансу: резерв лишається rt_ordered, НЕ позначено підтвердженим, без problems (алерт «поповніть»)", get(ac2)["rt_state"] == "rt_ordered" and get(ac2)["rt_confirmed_at"] is None and not rep2["problems"])
fk = AC([{"confirmed": True, "balance_before": 1000.0, "balance_after": 1000.0}])
with orders_db.get_connection() as c:
    rr.process_order(c, get(ac2), SUP, apply=True, deps=fk)
chk("після поповнення наступний цикл (rt_ordered) підтверджує сам", fk.conf_calls == [("RT000900001", 618.0)] and get(ac2)["rt_confirmed_at"])
ac3 = new_order("AC3")
fk = AC([{"confirmed": False, "already": True, "problems": ["статус не «Очікує оплати»"]}])
with orders_db.get_connection() as c:
    rr.process_order(c, get(ac3), SUP, apply=True, deps=fk)
chk("уже підтверджено вручну (status already) — позначаємо без повторного кліку", get(ac3)["rt_confirmed_at"] and len(fk.conf_calls) == 1)
ac4 = new_order("AC4")
fk = AC([{"confirmed": False, "cancelled": True, "problems": ["скасоване"]}])
with orders_db.get_connection() as c:
    rep4 = rr.process_order(c, get(ac4), SUP, apply=True, deps=fk)
chk("скасовано в кабінеті — стан manual, не підтверджено", get(ac4)["rt_state"] == "manual" and get(ac4)["rt_confirmed_at"] is None)
ac5 = new_order("AC5")
class ACboom(AC):
    def rt_confirm(self, rt_ref, total):
        raise RuntimeError("x")
fk = ACboom([])
with orders_db.get_connection() as c:
    rep5 = rr.process_order(c, get(ac5), SUP, apply=True, deps=fk)
chk("збій підтвердження після створеного резерву: стан rt_ordered лишається (резерв є), problems з поясненням, наступний цикл повторить", get(ac5)["rt_state"] == "rt_ordered" and any("збій" in x for x in rep5["problems"]))
_with_gate(None)
# аудит #929: у гілці rt_ordered — cancelled -> manual БЕЗ трекінгу/confirm_fail; unknown_status не підтверджує; «ttn-…» без номера RT -> ручна звірка
_with_gate("1")
ac6 = new_order("AC6")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, ac6, "rt_ordered", rt_order_ref="RT000900006", toysi_ttn="20451500000006")
    c.commit()
fk = AC([{"confirmed": False, "cancelled": True, "problems": ["скасоване"]}])
fk.ttn_info = {"status": "Відправлення прямує", "status_code": "5", "delivered": False}
with orders_db.get_connection() as c:
    rep6 = rr.process_order(c, get(ac6), SUP, apply=True, deps=fk)
chk("rt_ordered + скасовано в кабінеті: manual, трекінг НП НЕ перезаписує стан, без хибного «повторимо»", get(ac6)["rt_state"] == "manual" and "tracking" not in fk.calls and not rep6["problems"])
ac7 = new_order("AC7")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, ac7, "rt_ordered", rt_order_ref="RT000900007", toysi_ttn="20451500000007")
    c.commit()
fk = AC([{"confirmed": False, "unknown_status": True, "problems": ["невідомий"]}])
with orders_db.get_connection() as c:
    rep7 = rr.process_order(c, get(ac7), SUP, apply=True, deps=fk)
chk("невідомий статус у кабінеті: НЕ позначено підтвердженим, problems/rt_error, стан rt_ordered", get(ac7)["rt_confirmed_at"] is None and get(ac7)["rt_state"] == "rt_ordered" and rep7["problems"])
ac8 = new_order("AC8")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, ac8, "rt_ordered", rt_order_ref="ttn-20451500000008", toysi_ttn="20451500000008")
    c.commit()
fk = AC([])
with orders_db.get_connection() as c:
    rr.process_order(c, get(ac8), SUP, apply=True, deps=fk)
chk("rt_order_ref без номера RT («ttn-…») -> автопідтвердження не пробується (ручна звірка), не падає", fk.conf_calls == [] and get(ac8)["rt_confirmed_at"] is None)
ac9 = new_order("AC9")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, ac9, "rt_ordered", rt_order_ref="RT000900009", toysi_ttn="20451500000009")
    c.commit()
fk = AC([])
with orders_db.get_connection() as c:
    rr.process_order(c, get(ac9), SUP, apply=False, deps=fk)
chk("dry-run (apply=False) при ввімкненій брамі: підтвердження недосяжне", fk.conf_calls == [])
_with_gate(None)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
