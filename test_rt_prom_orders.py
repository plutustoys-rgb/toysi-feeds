#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rt_prom_orders.py — Prom-замовлення з товарами RoyalToys у конвеєрі RT (22.11.2026, брама RT_PROM_ORDERS_AUTO). Офлайн: усі зовнішні виклики (НП, Prom, Checkbox, кабінет RoyalToys) підмінені, тимчасова БД.
Інваріанти: (1) брама ВИМКНЕНА за замовчуванням → Prom не в черзі, позначка «виконати ВРУЧНУ» бітово як було; (2) з брамою повний цикл: передперевірка скасування → чек → ТТН на ПІБ/телефон/відділення з Prom → наліпка → RoyalToys → «Прийнято» + ЕН у Prom;
(3) FAIL-CLOSED: накладений платіж / схоже на нього / невідомий спосіб оплати / змішане / не НП / скасоване / не оплачене → без чека й ТТН + алерт; (4) ідемпотентність (claim, повторний запуск не дублює ТТН/замовлення/ЕН);
(5) чек Prom — лише в реальному режимі; (6) «delivered» у Prom + ретрай; (7) site/EVA не зачеплені."""
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
for _k in ("RT_AUTO_CONFIRM", "RT_PROM_ORDERS_AUTO", "RT_RUNNER_APPLY", "NP_TTN_APPLY", "RT_ORDER_SUBMIT", "RT_RUNNER_SINCE", "RT_ORDER_MODE", "RT_RETURN_NOTICE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.pop(_k, None)
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
sys.path.insert(0, str(Path(__file__).parent))
import orders_db  # noqa: E402
import orders_watcher as ow  # noqa: E402
import rt_order_runner as rr  # noqa: E402
import order_status_tracker as ost  # noqa: E402
import order_router as orr  # noqa: E402
import telegram_triage as tt  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


SUP = {"rt-1": {"vendor_code": "200513", "cost": 155, "dims_cm": [10, 10, 10], "weight_kg": 0.3, "stock": 9}}
rr.load_supplier = lambda path=None: SUP
os.environ["RT_MARKING_DIR"] = tempfile.mkdtemp()
rr.MARKING_DIR = Path(os.environ["RT_MARKING_DIR"])
orders_db.init_db()
REF = "7b422fc7-aaaa-bbbb-cccc-000000000001"
RT_ITEM = {"toysi_code": "rt-1", "name": "Бульбашки", "qty": 2, "price": 300}
TOYSI_ITEM = {"toysi_code": "100", "name": "Toysi", "qty": 1, "price": 100}
_alerts = []
rr._alert = lambda oid, key, text, cooldown=0: _alerts.append((oid, key, text))


def new_prom(oid, items=None, paid=1, pm="prepaid", pname="Пром-оплата", carrier="nova_poshta", city="CITY", wh="5", status=None, ref=None, db=None):
    with (orders_db.get_connection(db) if db else orders_db.get_connection()) as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": "prom", "payment_method": pm, "payment_confirmed": 0, "customer_name": "Іван Петренко", "phone": "0671112233",
                                   "np_branch": "Київ, Відділення №5", "np_city_ref": city, "np_warehouse_number": wh, "np_ref_id": ref, "carrier": carrier, "payment_option_name": pname,
                                   "items": items or [dict(RT_ITEM)]})
        iid = f"prom_{oid}"
        c.execute("UPDATE orders SET payment_confirmed=?, status=COALESCE(?, status) WHERE internal_order_id=?", (paid, status, iid))
        c.commit()
    return iid


def get(iid, db=None):
    with (orders_db.get_connection(db) if db else orders_db.get_connection()) as c:
        return orders_db._row_to_dict(c.execute("SELECT * FROM orders WHERE internal_order_id=?", (iid,)).fetchone())


class PromFake:
    def __init__(self, precheck=True, cancelled_live=False, ttn_info=None, push_ok=True, confirm=None):
        self.calls = []
        self.precheck = precheck
        self.cancelled_live = cancelled_live
        self.ttn_info = ttn_info
        self.push_ok = push_ok
        self.confirm = confirm
        self.created = []
        self.rt_orders = []
        self.pushes = []
        self.accepts = []
        self.delivered_pushes = []

    def tracking(self, ttn):
        self.calls.append("tracking")
        return self.ttn_info

    def np_warehouse_by_ref(self, ref):
        self.calls.append("wh_by_ref")
        return {"city_ref": "CITY-FROM-REF", "number": "5"}

    def ttn_dry_run(self, o):
        self.calls.append("ttn_dry")
        return {"cost": 140}

    def rt_dry_run(self, items, recipient=None):
        self.calls.append("rt_dry")
        return {"ready": True, "cart_total": 310.0}

    def ensure_receipt(self, conn, o):
        self.calls.append("receipt")
        return True

    def create_ttn(self, o):
        self.calls.append("create_ttn")
        self.created.append(o)
        return {"number": "20451500000777", "ref": "REF-P1"}

    def save_marking(self, ref, path):
        self.calls.append("marking")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4 x")

    def rt_order(self, items, ttn, pdf, recipient=None):
        self.calls.append("rt_order")
        self.rt_orders.append({"ttn": ttn, "recipient": recipient})
        return {"submitted": True, "rt_order_ref": "RT000900500", "total_on_checkout": 310.0}

    def rt_confirm(self, rt_ref, total):
        self.calls.append("rt_confirm")
        return self.confirm if self.confirm is not None else {"confirmed": True, "balance_before": 1000.0, "balance_after": 690.0}

    def prom_precheck(self, conn, o):
        self.calls.append("prom_precheck")
        return self.precheck

    def prom_cancelled_live(self, o):
        self.calls.append("prom_cancelled_live")
        return self.cancelled_live

    def prom_accept(self, o):
        self.calls.append("prom_accept")
        self.accepts.append(o["internal_order_id"])

    def prom_accept_and_push_ttn(self, conn, o, ttn, accept=True):
        self.calls.append("prom_push" if accept else "prom_push_retry")
        self.pushes.append((o["internal_order_id"], ttn, accept))
        if self.push_ok:
            conn.execute("UPDATE orders SET prom_ttn_pushed_at='2026-11-22T10:00:00' WHERE internal_order_id=?", (o["internal_order_id"],))
            conn.commit()
        return self.push_ok

    def prom_delivered_push(self, conn, o):
        self.calls.append("prom_delivered")
        self.delivered_pushes.append(o["internal_order_id"])
        conn.execute("UPDATE orders SET prom_delivered_pushed_at='2026-11-22T11:00:00' WHERE internal_order_id=?", (o["internal_order_id"],))
        conn.commit()


def open_gates(on=True):
    for k in ("RT_RUNNER_APPLY", "NP_TTN_APPLY", "RT_ORDER_SUBMIT"):
        if on:
            os.environ[k] = "1"
        else:
            os.environ.pop(k, None)
    if on:
        os.environ["RT_RUNNER_SINCE"] = "2026-01-01"
    else:
        os.environ.pop("RT_RUNNER_SINCE", None)


def prom_gate(on):
    if on:
        os.environ["RT_PROM_ORDERS_AUTO"] = "1"
    else:
        os.environ.pop("RT_PROM_ORDERS_AUTO", None)


# ---------------------------------------------------------------- 1. брама вимкнена за замовчуванням
p1 = new_prom("1001")
with orders_db.get_connection() as c:
    pend_default = {o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c)}
    pend_gate_off_env = {o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c)}
chk("1. брама RT_PROM_ORDERS_AUTO вимкнена за замовчуванням: Prom+rt НЕ в черзі конвеєра", orders_db.rt_prom_auto_enabled() is False and p1 not in pend_default)
open_gates(True)
fk = PromFake()
reps = rr.run(apply=True, deps=fk)
chk("1. без брами run(apply=True) нічого не робить для Prom (жодного чека/ТТН/виклику Prom), стан NULL", not fk.calls and get(p1)["rt_state"] is None)
open_gates(False)
note_off = ow._prom_rt_note({"platform": "prom", "items": [dict(RT_ITEM)], "payment_method": "prepaid", "payment_confirmed": True, "carrier": "nova_poshta", "payment_option_name": "Пром-оплата"})
chk("1. без брами позначка бітово як була: «виконати ВРУЧНУ»", note_off == "⚠️ є rt-позиції (RoyalToys): у Toysi НЕ передається, виконати ВРУЧНУ")
chk("1. не-Prom / не-rt: позначки нема", ow._prom_rt_note({"platform": "eva", "items": [dict(RT_ITEM)]}) == "" and ow._prom_rt_note({"platform": "prom", "items": [dict(TOYSI_ITEM)]}) == "")

# ---------------------------------------------------------------- 2. повний цикл з брамою
prom_gate(True)
open_gates(True)
with orders_db.get_connection() as c:
    pend_on = {o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c, include_prom=True)}
    pend_default_on = {o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c)}
chk("2. з брамою Prom+rt (оплачено) у черзі; виклик без include_prom поводиться як раніше", p1 in pend_on and p1 not in pend_default_on)
ok_a, why_a = rr.apply_enabled()
chk("2. ворота реального режиму відкриті (тест)", ok_a is True)
_alerts.clear()
fk = PromFake()
with orders_db.get_connection() as c:
    rep = rr.process_order(c, get(p1), SUP, apply=True, deps=fk)
st = get(p1)
chk("2. ланцюг: передперевірка Prom → чек → ТТН → наліпка → RoyalToys → «Прийнято»+ЕН у Prom; стан rt_ordered",
    fk.calls == ["prom_precheck", "receipt", "ttn_dry", "rt_dry", "create_ttn", "marking", "rt_order", "prom_push"] and st["rt_state"] == "rt_ordered" and st["rt_order_ref"] == "RT000900500" and st["toysi_ttn"] == "20451500000777" and not rep["problems"])
chk("2. ЕН у Prom передано з нашою ТТН, з виставленням «Прийнято» (accept=True), прапорець prom_ttn_pushed_at стоїть", fk.pushes == [(p1, "20451500000777", True)] and st["prom_ttn_pushed_at"])
chk("2. ПІБ і телефон отримувача з Prom-замовлення передано в RoyalToys і в ТТН; відділення з np_city_ref/np_warehouse_number", fk.rt_orders[0]["recipient"] == ("Іван Петренко", "+380671112233") and fk.created[0]["np_city_ref"] == "CITY" and fk.created[0]["np_warehouse_number"] == "5" and fk.created[0]["phone"] == "0671112233")
# ідемпотентність
fk2 = PromFake()
with orders_db.get_connection() as c:
    rr.process_order(c, get(p1), SUP, apply=True, deps=fk2)
chk("2. повторний запуск ідемпотентний: ТТН/RoyalToys/ЕН вдруге НЕ створюються (лише трекінг + перевірка скасування)", "create_ttn" not in fk2.calls and "rt_order" not in fk2.calls and "receipt" not in fk2.calls and not fk2.pushes and get(p1)["rt_state"] == "rt_ordered")
# claim: інший запуск уже взяв ttn_creating
p_claim = new_prom("1002")
with orders_db.get_connection() as c:
    orders_db.claim_rt_state(c, p_claim, None, "ttn_creating")
fk3 = PromFake()
with orders_db.get_connection() as c:
    rc = rr.process_order(c, get(p_claim), SUP, apply=True, deps=fk3)
chk("2. claim ttn_creating (результат невідомий) → конвеєр сам НЕ повторює: жодного створення, причина в problems", "create_ttn" not in fk3.calls and rc["problems"] and get(p_claim)["rt_state"] == "ttn_creating")
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET rt_state='manual' WHERE internal_order_id=?", (p_claim,))
    c.commit()
# ЕН не передалась → ретрай без повторного «Прийнято», далі — раз
p_retry = new_prom("1003")
fk4 = PromFake(push_ok=False)
with orders_db.get_connection() as c:
    rr.process_order(c, get(p_retry), SUP, apply=True, deps=fk4)
chk("2. ЕН у Prom не передалась → стан rt_ordered, дія «повтор наступного циклу»", get(p_retry)["rt_state"] == "rt_ordered" and not get(p_retry)["prom_ttn_pushed_at"] and fk4.pushes[0][2] is True)
fk5 = PromFake(push_ok=True)
with orders_db.get_connection() as c:
    rr.process_order(c, get(p_retry), SUP, apply=True, deps=fk5)
chk("2. ретрай ЕН: без повторного «Прийнято» (accept=False), далі прапорець стоїть", fk5.pushes == [(p_retry, "20451500000777", False)] and get(p_retry)["prom_ttn_pushed_at"])
fk6 = PromFake()
with orders_db.get_connection() as c:
    rr.process_order(c, get(p_retry), SUP, apply=True, deps=fk6)
chk("2. після успішної ЕН більше жодних викликів Prom-push", not fk6.pushes)

# ---------------------------------------------------------------- 3. Prom-поля одержувача й відділення/поштомат через реальний конвертер
raw = {"id": 555001, "status": "pending", "payment_option": {"name": "Пром-оплата"}, "payment_data": {"status": "paid"}, "client_first_name": "Марія", "client_last_name": "Коваленко", "phone": "0501112233",
       "delivery_option": {"name": "Нова Пошта"}, "delivery_provider_data": {"provider": "nova_poshta", "recipient_address": {"city_id": "CITY-P", "warehouse_id": REF}},
       "delivery_address": "Київ, Поштомат №6166", "products": [{"sku": "rt-1", "name": "Бульбашки", "quantity": 2, "price": "300 грн"}]}
_orig_wh = ow.nova_poshta.warehouse_by_ref
ow.nova_poshta.warehouse_by_ref = lambda r: {"city_ref": "CITY-P", "number": "6166"}
conv = ow._convert_prom_order(raw)
ow.nova_poshta.warehouse_by_ref = _orig_wh
chk("3. _convert_prom_order зберігає сирий спосіб оплати й оплату від Prom", conv["payment_option_name"] == "Пром-оплата" and conv["payment_confirmed"] is True and conv["payment_method"] == "prepaid")
norm = ow.normalize_order(conv)
chk("3. normalize_order протягує payment_option_name (раніше відкидав би невідомі ключі)", norm["payment_option_name"] == "Пром-оплата")
with orders_db.get_connection() as c:
    orders_db.insert_order(c, norm)
    c.commit()
pc = get("prom_555001")
chk("3. у БД: payment_option_name, np_ref_id (поштомат), ПІБ «Марія Коваленко», оплата підтверджена Prom", pc["payment_option_name"] == "Пром-оплата" and pc["np_ref_id"] == REF and pc["customer_name"] == "Марія Коваленко" and pc["payment_confirmed"] == 1 and orders_db.classify_rt_prom_order(pc)[0] == "ok")
fk = PromFake()
with orders_db.get_connection() as c:
    rr.process_order(c, pc, SUP, apply=True, deps=fk)
chk("3. поштомат/відділення: місто й номер беруться з довідника НП за np_ref_id (як у EVA), np_ref_id іде в ТТН; одержувач = клієнт Prom",
    fk.created and fk.created[0]["np_city_ref"] == "CITY-FROM-REF" and fk.created[0]["np_ref_id"] == REF and fk.rt_orders[0]["recipient"] == ("Марія Коваленко", "+380501112233"))
rawn = dict(raw, id=555002, delivery_provider_data={"provider": "nova_poshta"}, delivery_address="")
ow.nova_poshta.warehouse_by_ref = lambda r: None
convn = ow.normalize_order(ow._convert_prom_order(rawn))
ow.nova_poshta.warehouse_by_ref = _orig_wh
with orders_db.get_connection() as c:
    orders_db.insert_order(c, convn)
    c.execute("UPDATE orders SET payment_confirmed=1 WHERE internal_order_id='prom_555002'")
    c.commit()
fk = PromFake()
with orders_db.get_connection() as c:
    rn = rr.process_order(c, get("prom_555002"), SUP, apply=True, deps=fk)
chk("3. відділення не визначено (нема np_ref_id/np_city_ref) → стоп ДО ТТН і RoyalToys; чек вже був (оплачене), стан NULL", "create_ttn" not in fk.calls and "rt_order" not in fk.calls and rn["problems"] and get("prom_555002")["rt_state"] is None)
# Prom COD через реальний конвертер: payment_data нема, назва «Накладений платіж»
rawc = dict(raw, id=555003, payment_option={"name": "Накладений платіж"}, payment_data=None)
convc = ow.normalize_order(ow._convert_prom_order(rawc))
chk("3. накладений платіж Prom: payment_method=cod, не підтверджено", convc["payment_method"] == "cod" and not convc["payment_confirmed"])

# ---------------------------------------------------------------- 4. FAIL-CLOSED
def blocked(iid, key_part, label, **kw):
    _alerts.clear()
    fkb = PromFake()
    with orders_db.get_connection() as c:
        r = rr.process_order(c, get(iid), SUP, apply=True, deps=fkb)
    chk(f"4. {label}: ЖОДНОГО чека/ТТН/RoyalToys/Prom-push, стан NULL, алерт prom_{key_part} (🟠), причина в problems",
        not any(x in fkb.calls for x in ("receipt", "create_ttn", "rt_order", "prom_push", "marking")) and get(iid)["rt_state"] is None and r["problems"] and any(k == f"prom_{key_part}" for _, k, _t in _alerts))


b_cod = new_prom("2001", pname="Cash on delivery")
blocked(b_cod, "cod", "prepaid+підтверджено, але підпис оплати схожий на накладений платіж")
b_cod2 = new_prom("2002", pname="Оплата при отриманні")
blocked(b_cod2, "cod", "«Оплата при отриманні»")
b_pm = new_prom("2003", pm="cod", paid=1)
blocked(b_pm, "cod", "payment_method=cod")
b_unk = new_prom("2004", pname=None)
blocked(b_unk, "unknown_payment", "спосіб оплати невідомий (payment_option_name порожній) — fail-closed")
b_mix = new_prom("2005", items=[dict(RT_ITEM), dict(TOYSI_ITEM)])
blocked(b_mix, "mixed", "змішане замовлення (rt+Toysi)")
b_up = new_prom("2006", carrier="ukrposhta")
blocked(b_up, "carrier", "перевізник Укрпошта")
b_unp = new_prom("2007", paid=0)
with orders_db.get_connection() as c:
    pend_all = {o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c, include_prom=True)}
    cods = {o["internal_order_id"] for o in orders_db.get_rt_cod_orders(c, include_prom=True)}
    cods_default = {o["internal_order_id"] for o in orders_db.get_rt_cod_orders(c)}
    ready = {o["internal_order_id"] for o in orders_db.get_orders_ready_to_forward(c)}
chk("4. оплата не підтверджена → не в черзі; COD-замовлення видно get_rt_cod_orders(include_prom) (алерт rt_cod), без include_prom — ні", b_unp not in pend_all and b_pm in cods and b_pm not in cods_default and b_pm not in pend_all)
chk("4. змішане Prom-замовлення НЕ форвардиться в Toysi автоматично (get_orders_ready_to_forward його не бачить), Toysi-частина чекає людини", b_mix not in ready and b_cod not in ready)
b_can = new_prom("2008")
fk = PromFake(precheck=False)
_alerts.clear()
with orders_db.get_connection() as c:
    rcn = rr.process_order(c, get(b_can), SUP, apply=True, deps=fk)
chk("4. скасовано покупцем на Prom ДО оформлення → ні чека, ні ТТН (передперевірка першою)", fk.calls == ["prom_precheck"] and get(b_can)["rt_state"] is None and rcn["problems"])
b_st = new_prom("2009", status="prom_cancelled_before_forward")
b_st2 = new_prom("2010", status="canceled")
with orders_db.get_connection() as c:
    pend_st = {o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c, include_prom=True)}
chk("4. статуси prom_cancelled_before_forward / canceled виключено з черги", b_st not in pend_st and b_st2 not in pend_st)
# gate закрито, але process_order викликали напряму (захист у глибину)
prom_gate(False)
fk = PromFake()
b_g = new_prom("2011")
with orders_db.get_connection() as c:
    rg = rr.process_order(c, get(b_g), SUP, apply=True, deps=fk)
chk("4. брама закрита, а process_order викликано напряму → відмова ДО будь-якої дії (захист у глибину)", not fk.calls and rg["problems"] and "RT_PROM_ORDERS_AUTO" in rg["problems"][0])
prom_gate(True)

# ---------------------------------------------------------------- 5. чек Prom — лише в реальному режимі
for label, apply_, gates in (("dry-run (apply=False)", False, True), ("apply=True, але ворота НП/RoyalToys закриті", True, False)):
    open_gates(gates)
    if apply_ and not gates:
        os.environ["RT_RUNNER_APPLY"] = "1"
    pid = new_prom(f"3{'1' if apply_ else '0'}01")
    fk = PromFake()
    with orders_db.get_connection() as c:
        rr.process_order(c, get(pid), SUP, apply=apply_, deps=fk)
    chk(f"5. {label}: чек НЕ видається, ТТН/RoyalToys/Prom не чіпаємо, стан NULL", "receipt" not in fk.calls and "create_ttn" not in fk.calls and "rt_order" not in fk.calls and "prom_push" not in fk.calls and get(pid)["rt_state"] is None)
open_gates(True)

# ---------------------------------------------------------------- 6. скасовано ПІСЛЯ оформлення; автопідтвердження
p_ca = new_prom("4001")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, p_ca, "rt_ordered", rt_order_ref="RT000900600", toysi_ttn="20451500000888")
    c.commit()
os.environ["RT_AUTO_CONFIRM"] = "1"
_alerts.clear()
fk = PromFake(cancelled_live=True)
with orders_db.get_connection() as c:
    rr.process_order(c, get(p_ca), SUP, apply=True, deps=fk)
chk("6. Prom скасовано ПІСЛЯ оформлення → 🟠 prom_cancelled; «З балансу» і ЕН НЕ виконуються", "rt_confirm" not in fk.calls and not fk.pushes and any(k == "prom_cancelled" for _, k, _t in _alerts))
p_ac = new_prom("4002")
fk = PromFake()
with orders_db.get_connection() as c:
    rr.process_order(c, get(p_ac), SUP, apply=True, deps=fk)
chk("6. автопідтвердження: після резерву спершу «З балансу», ТІЛЬКИ ПОТІМ ЕН у Prom", fk.calls.index("rt_confirm") < fk.calls.index("prom_push") and len(fk.pushes) == 1)
p_lo = new_prom("4003")
fk = PromFake(confirm={"confirmed": False, "low_balance": True, "problems": ["мало"]})
with orders_db.get_connection() as c:
    rr.process_order(c, get(p_lo), SUP, apply=True, deps=fk)
chk("6. мало балансу: ЕН у Prom НЕ передається (посилка не йде), але «Прийнято» виставлено одразу", not fk.pushes and fk.accepts == [p_lo])
os.environ.pop("RT_AUTO_CONFIRM", None)

# ---------------------------------------------------------------- 7. delivered у Prom + ретрай
p_dl = new_prom("5001")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, p_dl, "shipped", rt_order_ref="RT000900700", toysi_ttn="20451500000999")
    c.commit()
fk = PromFake(ttn_info={"status": "Відправлення отримано", "status_code": "9", "delivered": True})
with orders_db.get_connection() as c:
    rr.process_order(c, get(p_dl), SUP, apply=True, deps=fk)
chk("7. НП: отримано → rt_state delivered і «delivered» у Prom тим самим механізмом", get(p_dl)["rt_state"] == "delivered" and fk.delivered_pushes == [p_dl])
p_dl2 = new_prom("5002")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, p_dl2, "delivered", rt_order_ref="RT000900701", toysi_ttn="20451500000998", delivery_status="delivered")
    c.commit()
fk = PromFake()
with orders_db.get_connection() as c:
    n_retry = rr._prom_delivered_retry(c, fk)
    n_retry2 = rr._prom_delivered_retry(c, fk)
chk("7. ретрай: доставлене, але «delivered» у Prom ще не передано → допередаємо; вдруге — ні", n_retry >= 1 and p_dl2 in fk.delivered_pushes and n_retry2 == 0)
p_old = new_prom("5003")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, p_old, "delivered", toysi_ttn="20451500000997")
    c.execute("UPDATE orders SET rt_updated_at='2026-01-01T00:00:00' WHERE internal_order_id=?", (p_old,))
    c.commit()
    old_list = [o["internal_order_id"] for o in orders_db.get_rt_prom_delivered_unpushed(c, "2026-11-19T00:00:00")]
chk("7. стару (>3 днів) доставлену не ретраїмо вічно", p_old not in old_list)
_alerts.clear()
p_rt = new_prom("5004")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, p_rt, "shipped", rt_order_ref="RT000900702", toysi_ttn="20451500000996")
    c.commit()
fk = PromFake(ttn_info={"status": "Відмова одержувача", "status_code": "103", "delivered": False})
with orders_db.get_connection() as c:
    rr.process_order(c, get(p_rt), SUP, apply=True, deps=fk)
chk("7. повернення Prom: стан returned, 🟠 алерт згадує Prom (а не LiqPay)", get(p_rt)["rt_state"] == "returned" and any(k == "returned" and "Prom" in t and "LiqPay" not in t for _, k, t in _alerts))

# ---------------------------------------------------------------- 8. run(): окрема БД, інші платформи не зачеплені
RUN_DB = os.path.join(tempfile.mkdtemp(), "run.db")
orders_db.init_db(RUN_DB)
r_prom = new_prom("6001", db=RUN_DB)
r_cod = new_prom("6002", pm="cod", db=RUN_DB)
with orders_db.get_connection(RUN_DB) as c:
    orders_db.insert_order(c, {"order_id": "S6001", "platform": "site", "payment_method": "prepaid", "payment_confirmed": 0, "customer_name": "Іван Петренко", "phone": "380671112233", "email": "b@e.com", "np_branch": "Київ, №5",
                               "np_city_ref": "CITY", "np_warehouse_number": "5", "carrier": "nova_poshta", "items": [dict(RT_ITEM)]})
    c.execute("UPDATE orders SET payment_confirmed=1, checkbox_ettn_registered_at='2026-10-20T10:00:00' WHERE internal_order_id='site_S6001'")
    c.commit()
_alerts.clear()
fk = PromFake()
fk.accept_site = True
reps = rr.run(apply=True, deps=fk, db_path=RUN_DB)
chk("8. run() з брамою: Prom-замовлення і сайтове оформлені в одному циклі; Prom — із «Прийнято»+ЕН, сайт — без Prom-викликів",
    get(r_prom, RUN_DB)["rt_state"] == "rt_ordered" and get("site_S6001", RUN_DB)["rt_state"] == "rt_ordered" and [p[0] for p in fk.pushes] == [r_prom])
chk("8. run(): для Prom COD+rt іде алерт rt_cod, Prom-COD не оформлено", any(oid == r_cod and k == "rt_cod" for oid, k, _t in _alerts) and get(r_cod, RUN_DB)["rt_state"] is None)
prom_gate(False)
r_prom2 = new_prom("6003", db=RUN_DB)
fk = PromFake()
rr.run(apply=True, deps=fk, db_path=RUN_DB)
chk("8. брама закрита: нові Prom не оформлюються, site/EVA-логіка та сама", get(r_prom2, RUN_DB)["rt_state"] is None and not fk.pushes)
prom_gate(True)

# ---------------------------------------------------------------- 9. реальний Deps.prom_* на моках API (без мережі)
import checkbox_client as cbc  # noqa: E402
calls = []
orig = (ost.attach_prom_declaration_id, ow.update_prom_order_status, ost.update_prom_order_status, ost.nova_poshta.get_tracking_status, orr.check_prom_order_status, orr.send_telegram_message)
ost.attach_prom_declaration_id = lambda oid, ttn, delivery_type="nova_poshta": calls.append(("attach", oid, ttn, delivery_type))
ow.update_prom_order_status = lambda oid, status="received": calls.append(("status", oid, status))
ost.update_prom_order_status = lambda oid, status="received": calls.append(("status", oid, status))
ost.nova_poshta.get_tracking_status = lambda ttn: {"delivered": True, "status": "Отримано"}
orr.send_telegram_message = lambda *a, **k: calls.append(("tg",))
d = rr._Deps()
p_real = new_prom("7001")
with orders_db.get_connection() as c:
    o = get(p_real)
    r1 = d.prom_accept_and_push_ttn(c, o, "20451500001111")
    o2 = get(p_real)
    r2 = d.prom_accept_and_push_ttn(c, o2, "20451500001111")
    o3 = get(p_real)
chk("9. Deps.prom_accept_and_push_ttn: «Прийнято» (received) → ЕН через існуючий attach_prom_declaration_id(order_id, ttn, nova_poshta); прапорець; повтор нічого не шле",
    r1 is True and r2 is True and calls == [("status", "7001", "received"), ("attach", "7001", "20451500001111", "nova_poshta")] and o3["prom_ttn_pushed_at"])
calls.clear()
p_real2 = new_prom("7002")


def _boom(*a, **k):
    raise ow.PromAPIError("Prom відхилив")


ost.attach_prom_declaration_id = _boom
with orders_db.get_connection() as c:
    r3 = d.prom_accept_and_push_ttn(c, get(p_real2), "20451500002222", False)
chk("9. збій Prom API при ЕН → False, прапорець НЕ ставиться (ретрай наступного циклу), виняток не летить", r3 is False and not get(p_real2)["prom_ttn_pushed_at"])
ost.attach_prom_declaration_id = orig[0]
calls.clear()
with orders_db.get_connection() as c:
    o4 = get(p_real)
    o4["toysi_ttn"] = "20451500001111"
    d.prom_delivered_push(c, o4)
chk("9. Deps.prom_delivered_push → існуючий _maybe_push_delivered_to_prom: Prom set_status delivered + прапорець", ("status", "7001", "delivered") in calls and get(p_real)["prom_delivered_pushed_at"])
orr.check_prom_order_status = lambda oid, date_from=None: "canceled"
chk("9. Deps.prom_cancelled_live: живий статус canceled → True", (setattr(ow, "check_prom_order_status", orr.check_prom_order_status) or d.prom_cancelled_live(get(p_real))) is True)
orr.check_prom_order_status = lambda oid, date_from=None: "paid"
ow.check_prom_order_status = orr.check_prom_order_status
chk("9. Deps.prom_cancelled_live: інший статус / збій → False", d.prom_cancelled_live(get(p_real)) is False)
with orders_db.get_connection() as c:
    okp = d.prom_precheck(c, get(p_real))
chk("9. Deps.prom_precheck: не скасовано → True", okp is True)
orr.check_prom_order_status = lambda oid, date_from=None: "canceled"
with orders_db.get_connection() as c:
    okc = d.prom_precheck(c, get(p_real2))
    c.commit()
chk("9. Deps.prom_precheck: скасовано → False, status=prom_cancelled_before_forward (той самий механізм, що перед форвардом у Toysi)", okc is False and get(p_real2)["status"] == "prom_cancelled_before_forward")
(ost.attach_prom_declaration_id, ow.update_prom_order_status, ost.update_prom_order_status, ost.nova_poshta.get_tracking_status, orr.check_prom_order_status, orr.send_telegram_message) = orig

# ---------------------------------------------------------------- 10. реальний чек Prom (Checkbox підмінено)
rc_calls = []
_orig_cr = cbc.create_receipt
cbc.create_receipt = lambda **kw: (rc_calls.append(kw) or {"id": "rcpt-1"})
try:
    p_rc = new_prom("8001")
    with orders_db.get_connection() as c:
        r_def = orr._ensure_site_prepaid_receipt(c, get(p_rc))
        n_def = len(rc_calls)
        r_eva = orr._ensure_site_prepaid_receipt(c, get(p_rc), include_eva=True)
        n_eva = len(rc_calls)
        r_prom = orr._ensure_site_prepaid_receipt(c, get(p_rc), include_eva=True, include_prom=True)
        n_prom = len(rc_calls)
        r_again = orr._ensure_site_prepaid_receipt(c, get(p_rc), include_eva=True, include_prom=True)
        n_again = len(rc_calls)
    chk("10. чек Prom: за замовчуванням і з include_eva чек НЕ видається (Prom-потік Toysi — чек від трекера, як було); лише include_prom=True (конвеєр RT) видає ОДИН CASHLESS-чек", r_def is True and n_def == 0 and n_eva == 0 and r_prom is True and n_prom == 1 and rc_calls[0]["payment_type"] == "CASHLESS")
    chk("10. чек Prom: footer «Prom замовлення …», без LiqPay-шапки; повторний виклик ДРУГОГО чека не видає (прапорець)", "Prom замовлення 8001" in rc_calls[0]["footer"] and rc_calls[0].get("header") is None and n_again == 1 and r_again is True)
    p_rc2 = new_prom("8002", paid=0)
    with orders_db.get_connection() as c:
        orr._ensure_site_prepaid_receipt(c, get(p_rc2), include_eva=True, include_prom=True)
    chk("10. неоплачене Prom-замовлення чека не отримує", len(rc_calls) == 1)
finally:
    cbc.create_receipt = _orig_cr

# ---------------------------------------------------------------- 11. текст сповіщення в orders_watcher
base = {"platform": "prom", "items": [dict(RT_ITEM)], "payment_method": "prepaid", "payment_confirmed": True, "carrier": "nova_poshta", "payment_option_name": "Пром-оплата"}
MAN = "⚠️ є rt-позиції (RoyalToys): у Toysi НЕ передається, виконати ВРУЧНУ"
prom_gate(True)
os.environ.pop("RT_RUNNER_APPLY", None)
n1 = ow._prom_rt_note(base)
chk("11. брама ввімкнена, але конвеєр у dry-run (RT_RUNNER_APPLY≠1) → «ВРУЧНУ» з поясненням", n1.startswith(MAN) and "dry-run" in n1)
os.environ["RT_RUNNER_APPLY"] = "1"
n2 = ow._prom_rt_note(base)
chk("11. брама+apply, оплачено → «обробляється АВТОМАТИЧНО», слова «ВРУЧНУ» нема", "АВТОМАТИЧНО" in n2 and "ВРУЧНУ" not in n2)
n3 = ow._prom_rt_note(dict(base, payment_confirmed=False))
chk("11. очікує оплати → «автоматично після підтвердження оплати»", "АВТОМАТИЧНО" in n3 and "після підтвердження оплати" in n3)
n4 = ow._prom_rt_note(dict(base, items=[dict(RT_ITEM), dict(TOYSI_ITEM)]))
n5 = ow._prom_rt_note(dict(base, payment_method="cod", payment_confirmed=False, payment_option_name="Накладений платіж"))
n6 = ow._prom_rt_note(dict(base, payment_option_name=None))
n7 = ow._prom_rt_note(dict(base, carrier="ukrposhta"))
chk("11. змішане / накладений платіж / невідома оплата / Укрпошта → «ВРУЧНУ» з причиною", all(n.startswith(MAN) and "автоматично НЕ обробляється" in n for n in (n4, n5, n6, n7)) and "змішане" in n4 and "накладений" in n5 and "невідом" in n6 and "Нову Пошту" in n7)
open_gates(False)
prom_gate(False)
n8 = ow._prom_rt_note(base)
chk("11. брама закрита → рівно стара позначка (бітово)", n8 == MAN)

# ---------------------------------------------------------------- 12. Telegram-сито та приймання інших платформ
chk("12. 🟠 RoyalToys prom_… іде власнику (виняток №2 розширено на Prom); site_PT- як раніше; ⚪ і чужі «🟠» — ні",
    tt.is_rt_chain_alert("🟠 RoyalToys prom_555001: збій") and tt.is_rt_chain_alert("🟠 RoyalToys site_PT-20261020-153620-193ef9: резерв") and not tt.is_rt_chain_alert("⚪ RoyalToys prom_555001: чекає") and not tt.is_rt_chain_alert("🟠 Щось інше"))
site_raw = {"order_id": "ST-1", "platform": "site", "payment_method": "prepaid", "items": [dict(TOYSI_ITEM)]}
nrm = ow.normalize_order(site_raw)
with orders_db.get_connection() as c:
    ins = orders_db.insert_order(c, nrm)
    c.commit()
chk("12. приймання інших платформ не зламано: site-замовлення вставляється, payment_option_name NULL", ins and get("site_ST-1")["payment_option_name"] is None)
rz = ow.normalize_order(ow._mock_rozetka_orders()[0])
eva = ow.normalize_order(ow._mock_eva_orders()[0])
chk("12. normalize_order Rozetka/EVA: payment_option_name=None, решта полів як було", rz["payment_option_name"] is None and eva["payment_option_name"] is None and rz["platform"] == "rozetka" and eva["platform"] == "eva")

print()
print(f"РАЗОМ: {'УСЕ OK' if not F else 'ПРОВАЛИ: ' + str(len(F))}")
for f in F:
    print("  FAIL:", f)
sys.exit(1 if F else 0)
