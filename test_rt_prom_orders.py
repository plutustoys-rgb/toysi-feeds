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


def new_prom(oid, items=None, paid=1, pm="prepaid", pname="Пром-оплата", carrier="nova_poshta", city="CITY", wh="5", status=None, ref=None, db=None, pstatus="auto", fio="Петренко Іван", phone="0671112233", created=None):
    with (orders_db.get_connection(db) if db else orders_db.get_connection()) as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": "prom", "payment_method": pm, "payment_confirmed": 0, "customer_name": "Іван Петренко", "phone": phone,
                                   "np_branch": "Київ, Відділення №5", "np_city_ref": city, "np_warehouse_number": wh, "np_ref_id": ref, "carrier": carrier, "payment_option_name": pname,
                                   "prom_payment_status": ("paid" if paid else None) if pstatus == "auto" else pstatus, "recipient_fio": fio, "created_at": created,
                                   "items": items or [dict(RT_ITEM)]})
        iid = f"prom_{oid}"
        c.execute("UPDATE orders SET payment_confirmed=?, status=COALESCE(?, status) WHERE internal_order_id=?", (paid, status, iid))
        c.commit()
    return iid


def get(iid, db=None):
    with (orders_db.get_connection(db) if db else orders_db.get_connection()) as c:
        return orders_db._row_to_dict(c.execute("SELECT * FROM orders WHERE internal_order_id=?", (iid,)).fetchone())


class PromFake:
    def __init__(self, live="pending", ttn_info=None, push_ok=True, confirm=None, live_seq=None):
        self.calls = []
        self.live = live
        self.live_seq = list(live_seq or [])      # послідовні відповіді живого статусу (потім — self.live)
        self.marked = []
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

    def prom_live_status(self, o):
        self.calls.append("prom_live")
        return self.live_seq.pop(0) if self.live_seq else self.live

    def prom_mark_cancelled(self, conn, o):
        self.calls.append("prom_mark_cancelled")
        self.marked.append(o["internal_order_id"])
        conn.execute("UPDATE orders SET status='prom_cancelled_before_forward' WHERE internal_order_id=?", (o["internal_order_id"],))

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


GATE_DATE = "2020-01-01T00:00"


def prom_gate(on):
    if on:
        os.environ["RT_PROM_ORDERS_AUTO"] = GATE_DATE
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
chk("2. ланцюг: живий статус Prom → (валідації, кабінет) → чек ПЕРЕД ТТН → ТТН → наліпка → RoyalToys → «Прийнято»+ЕН у Prom; стан rt_ordered",
    fk.calls == ["prom_live", "ttn_dry", "rt_dry", "receipt", "create_ttn", "marking", "rt_order", "prom_live", "prom_push"] and st["rt_state"] == "rt_ordered" and st["rt_order_ref"] == "RT000900500" and st["toysi_ttn"] == "20451500000777" and not rep["problems"])
chk("2. ЕН у Prom передано з нашою ТТН, з виставленням «Прийнято» (accept=True), прапорець prom_ttn_pushed_at стоїть", fk.pushes == [(p1, "20451500000777", True)] and st["prom_ttn_pushed_at"])
chk("2. ПІБ і телефон отримувача з Prom-замовлення передано в RoyalToys і в ТТН; відділення з np_city_ref/np_warehouse_number", fk.rt_orders[0]["recipient"] == ("Петренко Іван", "+380671112233") and fk.created[0]["customer_name"] == "Петренко Іван" and fk.created[0]["np_city_ref"] == "CITY" and fk.created[0]["np_warehouse_number"] == "5" and fk.created[0]["phone"] == "0671112233")
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
chk("3. у БД: payment_option_name, np_ref_id (поштомат), customer_name без змін «Марія Коваленко», recipient_fio «Коваленко Марія», prom_payment_status=paid", pc["payment_option_name"] == "Пром-оплата" and pc["np_ref_id"] == REF and pc["customer_name"] == "Марія Коваленко" and pc["recipient_fio"] == "Коваленко Марія" and pc["prom_payment_status"] == "paid" and pc["payment_confirmed"] == 1 and orders_db.classify_rt_prom_order(pc)[0] == "ok")
fk = PromFake()
with orders_db.get_connection() as c:
    rr.process_order(c, pc, SUP, apply=True, deps=fk)
chk("3. поштомат/відділення: місто й номер беруться з довідника НП за np_ref_id (як у EVA), np_ref_id іде в ТТН; одержувач = клієнт Prom",
    fk.created and fk.created[0]["np_city_ref"] == "CITY-FROM-REF" and fk.created[0]["np_ref_id"] == REF and fk.rt_orders[0]["recipient"] == ("Коваленко Марія", "+380501112233"))
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
chk("3. відділення не визначено (нема np_ref_id/np_city_ref) → стоп ДО чека, ТТН і RoyalToys, стан NULL (Б6)", "receipt" not in fk.calls and "create_ttn" not in fk.calls and "rt_order" not in fk.calls and rn["problems"] and get("prom_555002")["rt_state"] is None)
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
fk = PromFake(live="canceled")
_alerts.clear()
with orders_db.get_connection() as c:
    rcn = rr.process_order(c, get(b_can), SUP, apply=True, deps=fk)
chk("4. скасовано покупцем на Prom ДО оформлення → ні чека, ні ТТН; позначено prom_cancelled_before_forward (виходить із черги), алерт", fk.calls == ["prom_live", "prom_mark_cancelled"] and get(b_can)["status"] == "prom_cancelled_before_forward" and rcn["problems"] and any(k == "prom_cancelled" for _, k, _t in _alerts))
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
chk("4. брама закрита, а process_order викликано напряму → жодної дії й жодного виклику Prom (захист у глибину, тихо)", not fk.calls and not rg["problems"] and any("RT_PROM_ORDERS_AUTO" in a for a in rg["actions"]) and get(b_g)["rt_state"] is None)
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
fk = PromFake(live="canceled")
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
orig_cps = ow.check_prom_order_status
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
ow.check_prom_order_status = lambda oid, date_from=None: "canceled"
chk("9. Deps.prom_live_status: повертає живий статус Prom (orders_watcher.check_prom_order_status)", d.prom_live_status(get(p_real)) == "canceled")
ow.check_prom_order_status = lambda oid, date_from=None: None
chk("9. Deps.prom_live_status: нема даних → None (невідомо)", d.prom_live_status(get(p_real)) is None)


def _raise(*a, **k):
    raise RuntimeError("API")


ow.check_prom_order_status = _raise
chk("9. Deps.prom_live_status: збій API → None (fail-closed, не виняток)", d.prom_live_status(get(p_real)) is None)
with orders_db.get_connection() as c:
    d.prom_mark_cancelled(c, get(p_real2))
    c.commit()
chk("9. Deps.prom_mark_cancelled: status=prom_cancelled_before_forward (той самий маркер, що order_router)", get(p_real2)["status"] == "prom_cancelled_before_forward")
(ost.attach_prom_declaration_id, ow.update_prom_order_status, ost.update_prom_order_status, ost.nova_poshta.get_tracking_status, orr.check_prom_order_status, orr.send_telegram_message) = orig
ow.check_prom_order_status = orig_cps

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
base = {"platform": "prom", "items": [dict(RT_ITEM)], "payment_method": "prepaid", "payment_confirmed": True, "carrier": "nova_poshta", "payment_option_name": "Пром-оплата", "prom_payment_status": "paid"}
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

# ---------------------------------------------------------------- 13. АУДИТ #2013 (Б1–Б8)
OLD_PENDING_SQL = """
        SELECT * FROM orders
        WHERE platform IN ('site', 'eva') AND payment_method = 'prepaid' AND payment_confirmed = 1
          AND items LIKE '%' || ? || '%'
          AND (status IS NULL OR status NOT IN ('sandbox_test', 'cancelled', 'eva_cancelled_before_forward'))
          AND cancelled_at IS NULL
          AND refund_receipt_at IS NULL AND refund_receipt_claim_at IS NULL
          AND (rt_state IS NULL OR rt_state NOT IN ('delivered', 'returned', 'manual'))
          AND (rt_state IS NOT NULL OR ((toysi_ttn IS NULL OR toysi_ttn = '') AND forwarded_to_toysi_at IS NULL))
        ORDER BY created_at
        """
GATE_NEW = "2026-11-23T10:00"
open_gates(True)

# --- Б8b/бітова незмінність: черги site/eva з брамою й без неї = старий запит
B_DB = os.path.join(tempfile.mkdtemp(), "b.db")
orders_db.init_db(B_DB)
with orders_db.get_connection(B_DB) as c:
    for plat, oid, st in (("site", "S-A", None), ("site", "S-B", "canceled"), ("eva", "E-A", None), ("eva", "E-B", "prom_cancelled_before_forward"), ("eva", "E-C", "eva_cancelled_before_forward")):
        orders_db.insert_order(c, {"order_id": oid, "platform": plat, "payment_method": "prepaid", "payment_confirmed": 0, "customer_name": "Іван Петренко", "phone": "380671112233", "np_branch": "x", "items": [dict(RT_ITEM)], "status": st or "new"})
        c.execute("UPDATE orders SET payment_confirmed=1, status=? WHERE internal_order_id=?", (st or "new", f"{plat}_{oid}"))
    c.commit()
new_prom("B8P", db=B_DB)
new_prom("B8Q", db=B_DB, status="canceled")
with orders_db.get_connection(B_DB) as c:
    old_ids = [r["internal_order_id"] for r in c.execute(OLD_PENDING_SQL, (orders_db.RT_ITEM_MARK,)).fetchall()]
    new_off = [o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c)]
    new_on = [o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c, include_prom=True)]
chk("Б8b/бітово: без брами черга == старий запит (site/eva); Prom без rt_state не потрапляє", new_off == old_ids and not any(i.startswith("prom_") for i in new_off))
chk("Б8b: з include_prom черги site/eva ті самі (canceled/prom_cancelled_before_forward виключено лише для Prom-рядків: site_S-B і eva_E-B лишаються), Prom canceled — ні, Prom pending — так",
    [i for i in new_on if not i.startswith("prom_")] == old_ids and "site_S-B" in new_on and "eva_E-B" in new_on and "prom_B8P" in new_on and "prom_B8Q" not in new_on)

# --- Б1: дата вмикання
os.environ["RT_PROM_ORDERS_AUTO"] = "1"
chk("Б1: RT_PROM_ORDERS_AUTO=1 без дати → брама «відкрита», але дата INVALID", orders_db.rt_prom_auto_enabled() is True and orders_db.rt_prom_auto_since() == "INVALID" and orders_db.rt_prom_auto_active() is False)
os.environ["RT_PROM_ORDERS_AUTO"] = "0"
chk("Б1: «0» і порожнє = вимкнено", orders_db.rt_prom_auto_enabled() is False and orders_db.rt_prom_auto_since() is None)
os.environ["RT_PROM_ORDERS_AUTO"] = "2026-11-23T10:00+02:00"
chk("Б1: дата з часовою зоною → INVALID (created_at наївний)", orders_db.rt_prom_auto_since() == "INVALID")
os.environ["RT_PROM_ORDERS_AUTO"] = "2026-11-23"
chk("Б1: дата без часу → північ", orders_db.rt_prom_auto_since() == "2026-11-23T00:00:00")
os.environ["RT_PROM_ORDERS_AUTO"] = GATE_NEW
chk("Б1: ISO-значення приймається", orders_db.rt_prom_auto_since() == "2026-11-23T10:00:00")

R2 = os.path.join(tempfile.mkdtemp(), "r2.db")
orders_db.init_db(R2)
old_recv = new_prom("B101", db=R2, created="2026-11-20T09:00:00")          # виконане вручну, ще «pending» у знімку, але створене ДО дати
new_ok = new_prom("B102", db=R2, created="2026-11-23T10:05:00")
_alerts.clear()
fk = PromFake()
rr.run(apply=True, deps=fk, db_path=R2)
chk("Б1: старе (до дати вмикання) Prom-замовлення НЕ виконується вдруге: жодного виклику Prom/чека/ТТН для нього; нове (після дати) — оформлено",
    get(old_recv, R2)["rt_state"] is None and get(new_ok, R2)["rt_state"] == "rt_ordered" and all(p[0] != old_recv for p in fk.pushes) and fk.calls.count("create_ttn") == 1 and not any(oid == old_recv for oid, _k, _t in _alerts))
with orders_db.get_connection(R2) as c:
    fk = PromFake()
    r_old = rr.process_order(c, get(old_recv, R2), SUP, apply=True, deps=fk)
chk("Б1: захист у глибину: process_order напряму для старого → жодних викликів, без алерту й problems", not fk.calls and not r_old["problems"] and get(old_recv, R2)["rt_state"] is None)
# живий статус не нове
for lv in ("received", "delivered", "custom-5", "draft"):
    oid = new_prom(f"B11{lv[:2]}", db=R2, created="2026-11-23T11:00:00")
    _alerts.clear()
    fk = PromFake(live=lv)
    with orders_db.get_connection(R2) as c:
        rr.process_order(c, get(oid, R2), SUP, apply=True, deps=fk)
    chk(f"Б1: живий статус Prom «{lv}» (не pending/paid) → блок: ні чека, ні ТТН, стан manual (виходить із черги), алерт prom_foreign_status",
        not any(x in fk.calls for x in ("receipt", "create_ttn", "rt_order", "prom_push")) and get(oid, R2)["rt_state"] == "manual" and any(k == "prom_foreign_status" for _, k, _t in _alerts))
for lv in ("pending", "paid"):
    oid = new_prom(f"B12{lv[:2]}", db=R2, created="2026-11-23T11:30:00")
    fk = PromFake(live=lv)
    with orders_db.get_connection(R2) as c:
        rr.process_order(c, get(oid, R2), SUP, apply=True, deps=fk)
    chk(f"Б1: живий статус «{lv}» → можна стартувати", get(oid, R2)["rt_state"] == "rt_ordered")
# брама без дати
os.environ["RT_PROM_ORDERS_AUTO"] = "1"
R3 = os.path.join(tempfile.mkdtemp(), "r3.db")
orders_db.init_db(R3)
nd = new_prom("B103", db=R3)
_alerts.clear()
fk = PromFake()
rr.run(apply=True, deps=fk, db_path=R3)
chk("Б1: брама «1» без дати → Prom не обробляється (жодних викликів), 🟠 алерт prom_gate «вкажіть дату»", not fk.calls and get(nd, R3)["rt_state"] is None and any(oid == "prom_gate" and k == "prom_no_date" and "дат" in t for oid, k, t in _alerts))
with orders_db.get_connection(R3) as c:
    fk = PromFake()
    rd = rr.process_order(c, get(nd, R3), SUP, apply=True, deps=fk)
chk("Б1: process_order напряму при брамі без дати → блок no_date, без викликів Prom/чека", not fk.calls and rd["problems"] and "дат" in rd["problems"][0])
os.environ["RT_PROM_ORDERS_AUTO"] = GATE_NEW

# --- Б2: невідомий живий статус / скасування у вікні перед «З балансу» й ЕН
b2 = new_prom("B201", created="2026-11-23T12:00:00")
_alerts.clear()
fk = PromFake(live=None)
with orders_db.get_connection() as c:
    rw = rr.process_order(c, get(b2), SUP, apply=True, deps=fk)
chk("Б2: живий статус невідомий на старті → НЕ виконуємо (жодних чека/ТТН), без алерту й без problems, повтор наступного циклу", fk.calls == ["prom_live"] and get(b2)["rt_state"] is None and not rw["problems"] and not _alerts)
os.environ["RT_AUTO_CONFIRM"] = "1"
b2b = new_prom("B202", created="2026-11-23T12:01:00")
fk = PromFake(live_seq=["pending", None])
with orders_db.get_connection() as c:
    rr.process_order(c, get(b2b), SUP, apply=True, deps=fk)
chk("Б2: після резерву статус невідомий → «З балансу» і ЕН НЕ виконуються цього циклу; стан rt_ordered", "rt_confirm" not in fk.calls and not fk.pushes and get(b2b)["rt_state"] == "rt_ordered" and not get(b2b)["rt_confirmed_at"])
fk = PromFake(live="pending")
with orders_db.get_connection() as c:
    rr.process_order(c, get(b2b), SUP, apply=True, deps=fk)
chk("Б2: наступного циклу статус відомий → «З балансу», потім ЕН (із повторною перевіркою статусу між ними)", fk.calls.index("rt_confirm") < fk.calls.index("prom_push_retry") and fk.calls.count("prom_live") >= 2 and get(b2b)["prom_ttn_pushed_at"])
b2c = new_prom("B203", created="2026-11-23T12:02:00")
_alerts.clear()
fk = PromFake(live_seq=["pending", "canceled"])
with orders_db.get_connection() as c:
    rr.process_order(c, get(b2c), SUP, apply=True, deps=fk)
chk("Б2: скасовано у вікні між стартом і «З балансу» → стоп: нема rt_confirm/ЕН, 🟠 «ручне скасування ТТН/резерву», автоскасування не викликається", "rt_confirm" not in fk.calls and not fk.pushes and any(k == "prom_cancelled" and "РУЧНЕ скасування" in t for _, k, t in _alerts) and not any("cancel" in x and x != "prom_mark_cancelled" for x in fk.calls) and "prom_mark_cancelled" not in fk.calls)
b2d = new_prom("B204", created="2026-11-23T12:03:00")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, b2d, "rt_ordered", rt_order_ref="RT000900801", toysi_ttn="20451500002001", rt_total=100.0)
    c.commit()
_alerts.clear()
fk = PromFake(live_seq=["pending", "canceled"])
with orders_db.get_connection() as c:
    rr.process_order(c, get(b2d), SUP, apply=True, deps=fk)
chk("Б2: у rt_ordered: статус «pending» перед підтвердженням, «canceled» перед ЕН (після confirm) → ЕН не передається, алерт", not fk.pushes and any(k == "prom_cancelled" for _, k, _t in _alerts))
os.environ.pop("RT_AUTO_CONFIRM", None)
b2e = new_prom("B205", created="2026-11-23T12:04:00")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, b2e, "shipped", rt_order_ref="RT000900802", toysi_ttn="20451500002002")
    c.commit()
fk = PromFake(live=None)
with orders_db.get_connection() as c:
    rr.process_order(c, get(b2e), SUP, apply=True, deps=fk)
chk("Б2: shipped без ЕН у Prom і статус невідомий → ЕН НЕ передаємо (fail-closed), трекінг триває", not fk.pushes and "tracking" in fk.calls)
b2f = new_prom("B206", created="2026-11-23T12:05:00")
with orders_db.get_connection() as c:
    orders_db.set_rt_state(c, b2f, "rt_ordered", rt_order_ref="RT000900803", toysi_ttn="20451500002003")
    c.execute("UPDATE orders SET prom_ttn_pushed_at='2026-11-23T12:10:00', rt_confirmed_at='2026-11-23T12:10:00' WHERE internal_order_id=?", (b2f,))
    c.commit()
fk = PromFake(live=None)
with orders_db.get_connection() as c:
    rr.process_order(c, get(b2f), SUP, apply=True, deps=fk)
chk("Б2: rt_ordered, усе вже зроблено, щоциклова перевірка скасування при невідомому статусі — лише лог, трекінг НП триває (fail-open без дій)", "tracking" in fk.calls and not fk.pushes)

# --- Б3: вимкнення брами не стоп-кран для замовлень у польоті
R4 = os.path.join(tempfile.mkdtemp(), "r4.db")
orders_db.init_db(R4)
fl = new_prom("B301", db=R4, created="2026-11-23T13:00:00")
fl_new = new_prom("B302", db=R4, created="2026-11-23T13:01:00")
with orders_db.get_connection(R4) as c:
    orders_db.set_rt_state(c, fl, "shipped", rt_order_ref="RT000900901", toysi_ttn="20451500003001")
    c.commit()
prom_gate(False)
os.environ.pop("RT_PROM_ORDERS_AUTO", None)
with orders_db.get_connection(R4) as c:
    pend_off = {o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c)}
chk("Б3: брама вимкнена: Prom у польоті (rt_state NOT NULL) лишається в черзі, нове (rt_state NULL) — ні", fl in pend_off and fl_new not in pend_off)
fk = PromFake(ttn_info={"status": "Відправлення отримано", "status_code": "9", "delivered": True})
rr.run(apply=True, deps=fk, db_path=R4)
chk("Б3: брама вимкнена: замовлення в польоті трекається до delivered і «delivered» іде в Prom; нове НЕ стартує (жодних create_ttn/receipt)",
    get(fl, R4)["rt_state"] == "delivered" and fk.delivered_pushes == [fl] and get(fl_new, R4)["rt_state"] is None and "create_ttn" not in fk.calls and "receipt" not in fk.calls)
fl2 = new_prom("B303", db=R4, created="2026-11-23T13:02:00")
with orders_db.get_connection(R4) as c:
    orders_db.set_rt_state(c, fl2, "marking_ready", rt_ttn_ref="REF-X", rt_marking_path=str(rr.MARKING_DIR / "x.pdf"), toysi_ttn="20451500003002")
    c.commit()
fk = PromFake()
rr.run(apply=True, deps=fk, db_path=R4)
chk("Б3: брама вимкнена, замовлення посередині ланцюга (marking_ready) доводиться до rt_ordered (кроки завершуються)", get(fl2, R4)["rt_state"] == "rt_ordered" and "rt_order" in fk.calls and "create_ttn" not in fk.calls)
prom_gate(True)
os.environ["RT_PROM_ORDERS_AUTO"] = GATE_NEW

# --- Б4: дозвільний список підписів оплати
for nm in ("Наложка", "Нал", "Оплата на місці", "Pay on delivery", "Оплата на карту", "Оплата частинами", "Оплата карткою", "", None):
    o = {"items": [dict(RT_ITEM)], "carrier": "nova_poshta", "payment_method": "prepaid", "payment_confirmed": 1, "payment_option_name": nm, "prom_payment_status": "paid"}
    v = orders_db.classify_rt_prom_order(o)
    chk(f"Б4: підпис «{nm}» → НЕ ok (блок)", v[0] in ("cod", "unknown_payment"))
for nm in ("Пром-оплата", "  Пром‑оплата​ ", "ПРОМ-ОПЛАТА", "Пром - оплата", "Пром-оплата"):
    o = {"items": [dict(RT_ITEM)], "carrier": "nova_poshta", "payment_method": "prepaid", "payment_confirmed": 1, "payment_option_name": nm, "prom_payment_status": "paid"}
    chk(f"Б4: «{nm!r}» (нормалізація пробілів/регістру/nbsp/zero-width/дефісів) → ok", orders_db.classify_rt_prom_order(o)[0] == "ok")
o_bank = {"items": [dict(RT_ITEM)], "carrier": "nova_poshta", "payment_method": "prepaid", "payment_confirmed": 1, "payment_option_name": "Пром-оплата", "prom_payment_status": None}
chk("Б4: оплату підтвердив не Prom (bank_check/вручну; prom_payment_status≠paid) → unpaid, не ok", orders_db.classify_rt_prom_order(o_bank)[0] == "unpaid")
for nm in ("Наложка", "Оплата на карту"):
    ub = new_prom("B4" + str(abs(hash(nm)) % 1000), pname=nm, created="2026-11-23T14:00:00")
    _alerts.clear()
    fk = PromFake()
    with orders_db.get_connection() as c:
        r4 = rr.process_order(c, get(ub), SUP, apply=True, deps=fk)
    chk(f"Б4: процес: «{nm}» → блок, жодного чека/ТТН, алерт prom_unknown_payment/prom_cod з підписом", not any(x in fk.calls for x in ("receipt", "create_ttn")) and r4["problems"] and any(k in ("prom_unknown_payment", "prom_cod") and nm.split()[0] in t for _, k, t in _alerts))
_alerts.clear()
ub2 = new_prom("B4X", pname="Оплата на карту", created="2026-11-23T14:01:00")
with orders_db.get_connection() as c:
    rr.process_order(c, get(ub2), SUP, apply=True, deps=PromFake())
chk("Б4: текст алерту «невідомий спосіб оплати Prom: «Оплата на карту»»", any(k == "prom_unknown_payment" and "невідомий спосіб оплати Prom: «Оплата на карту»" in t for _, k, t in _alerts))
# poll_once: Prom підтвердив «paid» пізніше — прапорець оновлюється
ow_fetch = (ow.fetch_new_orders_prom, ow.fetch_new_orders_rozetka, ow.fetch_new_orders_eva)
late_raw = dict(raw, id=777001, payment_data={"status": "waiting"})
ow.nova_poshta.warehouse_by_ref = lambda r: {"city_ref": "CITY-P", "number": "5"}
late1 = ow._convert_prom_order(late_raw)
late2 = ow._convert_prom_order(dict(raw, id=777001))
ow.nova_poshta.warehouse_by_ref = _orig_wh
ow.fetch_new_orders_rozetka = lambda: []
ow.fetch_new_orders_eva = lambda: []
ow.fetch_new_orders_prom = lambda: [late1]
ow.poll_once()
st1 = get("prom_777001")
ow.fetch_new_orders_prom = lambda: [late2]
ow.poll_once()
st2 = get("prom_777001")
ow.fetch_new_orders_prom, ow.fetch_new_orders_rozetka, ow.fetch_new_orders_eva = ow_fetch
chk("Б4: poll_once: спочатку оплата не підтверджена (waiting), потім Prom каже paid → payment_confirmed=1 і prom_payment_status=paid", st1["prom_payment_status"] == "waiting" and not st1["payment_confirmed"] and st2["prom_payment_status"] == "paid" and st2["payment_confirmed"] == 1)

# --- Б5: порядок ПІБ
import np_ttn  # noqa: E402
raw_fio = dict(raw, id=777002, client_first_name="Іван", client_last_name="Петренко", client_second_name="Іванович")
ow.nova_poshta.warehouse_by_ref = lambda r: {"city_ref": "CITY-P", "number": "5"}
cv = ow.normalize_order(ow._convert_prom_order(raw_fio))
cv2 = ow.normalize_order(ow._convert_prom_order(dict(raw, id=777003, client_first_name="Іван", client_last_name="Петренко")))
ow.nova_poshta.warehouse_by_ref = _orig_wh
chk("Б5: конвертер: customer_name лишився «Ім'я Прізвище» (інші потоки), recipient_fio = «Прізвище Ім'я [По батькові]»", cv["customer_name"] == "Іван Петренко" and cv["recipient_fio"] == "Петренко Іван Іванович" and cv2["recipient_fio"] == "Петренко Іван")
chk("Б5: np_ttn.split_fio(recipient_fio): LastName=Петренко, FirstName=Іван, MiddleName=Іванович", np_ttn.split_fio(cv["recipient_fio"]) == ("Петренко", "Іван", "Іванович") and np_ttn.split_fio(cv2["recipient_fio"]) == ("Петренко", "Іван", ""))
fio_o = new_prom("B501", fio="Петренко Іван Іванович", created="2026-11-23T15:00:00")
fk = PromFake()
with orders_db.get_connection() as c:
    rr.process_order(c, get(fio_o), SUP, apply=True, deps=fk)
chk("Б5: у ТТН і в RoyalToys ПІБ = «Петренко Іван Іванович» (а не «Іван Петренко»); customer_name у БД не змінено",
    fk.created[0]["customer_name"] == "Петренко Іван Іванович" and fk.rt_orders[0]["recipient"][0] == "Петренко Іван Іванович" and get(fio_o)["customer_name"] == "Іван Петренко"
    and np_ttn.split_fio(fk.created[0]["customer_name"])[:2] == ("Петренко", "Іван"))
nof = new_prom("B502", fio=None, created="2026-11-23T15:01:00")
fk = PromFake()
with orders_db.get_connection() as c:
    rnf = rr.process_order(c, get(nof), SUP, apply=True, deps=fk)
chk("Б5: нема recipient_fio → ТТН/чек НЕ створюються (не вгадуємо порядок імені)", not any(x in fk.calls for x in ("receipt", "create_ttn")) and rnf["problems"] and "recipient_fio" in rnf["problems"][0])

# --- Б6: чек лише після всіх валідацій
for label, kw, extra in (("невалідний телефон", {"phone": "12345"}, None), ("залишок у постачальника < замовлено", {"items": [{"toysi_code": "rt-1", "name": "x", "qty": 99, "price": 300}]}, None), ("нема відділення", {"city": None, "wh": None}, None)):
    ob = new_prom("B6" + str(abs(hash(label)) % 1000), created="2026-11-23T16:00:00", **kw)
    fk = PromFake()
    with orders_db.get_connection() as c:
        r6 = rr.process_order(c, get(ob), SUP, apply=True, deps=fk)
    chk(f"Б6: {label} → чека НЕМАЄ, ТТН немає, стан NULL", "receipt" not in fk.calls and "create_ttn" not in fk.calls and r6["problems"] and get(ob)["rt_state"] is None)


class CabNotReady(PromFake):
    def rt_dry_run(self, items, recipient=None):
        self.calls.append("rt_dry")
        return {"ready": False, "problems": ["кошик"]}


ob = new_prom("B607", created="2026-11-23T16:01:00")
fk = CabNotReady()
with orders_db.get_connection() as c:
    rr.process_order(c, get(ob), SUP, apply=True, deps=fk)
chk("Б6: кабінет RoyalToys не готовий → чека немає, ТТН немає", "receipt" not in fk.calls and "create_ttn" not in fk.calls)


class ReceiptFail(PromFake):
    def ensure_receipt(self, conn, o):
        self.calls.append("receipt")
        return False


ob = new_prom("B608", created="2026-11-23T16:02:00")
fk = ReceiptFail()
with orders_db.get_connection() as c:
    rr.process_order(c, get(ob), SUP, apply=True, deps=fk)
chk("Б6: чек не видано → ТТН НЕ створюється (як і було для сайту/EVA)", fk.calls[-1] == "receipt" and "create_ttn" not in fk.calls and get(ob)["rt_state"] is None)

# --- Б7: вихід із блоку після виправлення причини
ob = new_prom("B701", pname="Оплата на карту", created="2026-11-23T17:00:00")
fk = PromFake()
with orders_db.get_connection() as c:
    rr.process_order(c, get(ob), SUP, apply=True, deps=fk)
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET payment_option_name='Пром-оплата' WHERE internal_order_id=?", (ob,))
    c.commit()
fk = PromFake()
with orders_db.get_connection() as c:
    rr.process_order(c, get(ob), SUP, apply=True, deps=fk)
chk("Б7: після виправлення причини (підпис оплати) конвеєр підхоплює замовлення сам", get(ob)["rt_state"] == "rt_ordered")
mixc = new_prom("B702", items=[dict(RT_ITEM), dict(TOYSI_ITEM)], created="2026-11-23T17:01:00")
fk = PromFake(live="canceled")
with orders_db.get_connection() as c:
    rr.process_order(c, get(mixc), SUP, apply=True, deps=fk)
with orders_db.get_connection() as c:
    pend_mix = {o["internal_order_id"] for o in orders_db.get_rt_orders_pending(c, include_prom=True)}
chk("Б7: заблоковане (змішане) і СКАСОВАНЕ покупцем замовлення перестає алертити: скасування перевіряється ДО вердикту, статус → prom_cancelled_before_forward, із черги знято", mixc not in pend_mix and get(mixc)["status"] == "prom_cancelled_before_forward")

# --- повторна перевірка бітової незмінності при вимкненій брамі
prom_gate(False)
os.environ.pop("RT_PROM_ORDERS_AUTO", None)
RB = os.path.join(tempfile.mkdtemp(), "rb.db")
orders_db.init_db(RB)
new_prom("BIT1", db=RB, created="2026-11-23T18:00:00")
with orders_db.get_connection(RB) as c:
    orders_db.insert_order(c, {"order_id": "BS1", "platform": "site", "payment_method": "prepaid", "payment_confirmed": 0, "customer_name": "Іван Петренко", "phone": "380671112233", "np_branch": "x", "np_city_ref": "CITY", "np_warehouse_number": "5", "items": [dict(RT_ITEM)]})
    c.execute("UPDATE orders SET payment_confirmed=1, checkbox_ettn_registered_at='2026-11-20T10:00:00' WHERE internal_order_id='site_BS1'")
    c.commit()
fk = PromFake()
rr.run(apply=True, deps=fk, db_path=RB)
chk("бітово: брама вимкнена → Prom без rt_state не чіпаємо (жодного prom_live/receipt для нього), site-замовлення оформлюється як раніше, позначка «ВРУЧНУ» стара",
    get("prom_BIT1", RB)["rt_state"] is None and "prom_live" not in fk.calls and get("site_BS1", RB)["rt_state"] == "rt_ordered" and ow._prom_rt_note(base) == MAN)
open_gates(False)

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
