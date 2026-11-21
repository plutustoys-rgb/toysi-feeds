#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_kv_orders_b1.py — етап Б1 модуля замовлень KIDDISVIT (21.11.2026): БД-стани kv_* (orders_db) і розширення np_ttn.py (відправник «Дніпро №28», номер замовлення в ТТН, габарити kv-позицій, PDF накладної).
ІНВАРІАНТИ: (1) міграція kv_* колонок ідемпотентна; (2) get_kv_orders_pending бере лише оплачені prepaid-замовлення сайту з kv-/kvu-, не скасовані/не повернені, kv_state не термінальний; kv_unknown лишається видимим;
(3) set_kv_state/claim_kv_state: невідомий стан/поле → ValueError, claim атомарний (другий claim → False); (4) build_ttn_payload: extra_props лише з allowlist (AdditionalInformation, InfoRegClientBarcodes), обрізання довжин;
(5) resolve_sender('kv') = відділення №28 Дніпро, 'rt' — як раніше, кеші окремі; (6) with_supplier_dims бере габарити kv-/kvu- із supplier_kv; без габаритів → TtnError; rt поведінка без змін;
(7) fetch_document_pdf: лише GUID, лише %PDF, ключ не в винятках. Без мережі. `python test_kv_orders_b1.py` → exit 0/1.
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ.setdefault("NP_API_KEY", "TESTKEY-SECRET")
TMP = tempfile.mkdtemp(prefix="kvb1_")
os.environ["ORDERS_DB_PATH"] = os.path.join(TMP, "orders.db")
sys.path.insert(0, str(HERE))
import orders_db  # noqa: E402
import np_ttn as t  # noqa: E402
import nova_poshta as np_  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


# ── 1. міграція
orders_db.init_db()
orders_db.init_db()
with orders_db.get_connection() as c:
    cols = {r[1] for r in c.execute("PRAGMA table_info(orders)")}
chk("міграція: усі 7 kv_* колонок створено, повторний init_db не падає", {"kv_state", "kv_order_number", "kv_order_id", "kv_ttn_ref", "kv_docs_path", "kv_error", "kv_updated_at"} <= cols)

KV_ITEMS = [{"toysi_code": "kv-201", "name": "Т", "qty": 1, "price": 900}]


def new(oid, items=KV_ITEMS, pm="prepaid", confirmed=1, platform="site", **upd):
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": platform, "payment_method": pm, "payment_confirmed": 0, "customer_name": "Іван Петренко", "phone": "380671112233",
                                   "email": "b@example.com", "np_branch": "Київ, Відділення №1", "np_warehouse_number": "1", "carrier": "nova_poshta", "items": items})
        iid = f"{platform}_{oid}"
        c.execute("UPDATE orders SET payment_confirmed=?, created_at=? WHERE internal_order_id=?", (confirmed, "2026-11-21T10:00:00", iid))
        for k, v in upd.items():
            c.execute(f"UPDATE orders SET {k}=? WHERE internal_order_id=?", (v, iid))
        c.commit()
    return iid


def pending():
    with orders_db.get_connection() as c:
        return {r["internal_order_id"] for r in orders_db.get_kv_orders_pending(c)}


a = new("a1")                                             # придатне
b = new("b1", items=[{"toysi_code": "kvu-9", "name": "У", "qty": 1, "price": 100}])      # kvu- теж
n_cod = new("c1", pm="cod")
n_unpaid = new("d1", confirmed=0)
n_toysi = new("e1", items=[{"toysi_code": "12345", "name": "Т", "qty": 1, "price": 100}])
n_rt = new("f1", items=[{"toysi_code": "rt-77", "name": "Т", "qty": 1, "price": 100}])
n_cancel = new("g1", cancelled_at="2026-11-21T11:00:00")
n_refund = new("h1", refund_receipt_at="2026-11-21T11:00:00")
n_claim = new("i1", refund_receipt_claim_at="2026-11-21T11:00:00")
n_ret = new("j1", delivery_status="returned")
n_sand = new("k1", status="sandbox_test")
n_old = new("l1")
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET created_at='2026-11-10T10:00:00' WHERE internal_order_id=?", (n_old,))
    c.commit()
n_eva = new("m1", platform="eva")
p = pending()
chk("get_kv_orders_pending: лише оплачені prepaid сайту з kv-/kvu-, не скасовані/не повернені, не старші за KV_RECEIPT_SINCE", p == {a, b})
for state, vis in (("kv_unknown", True), ("kv_creating", True), ("kv_created", True), ("docs_sent", True), ("shipped", True), ("delivered", False), ("returned", False), ("manual", False)):
    with orders_db.get_connection() as c:
        c.execute("UPDATE orders SET kv_state=? WHERE internal_order_id=?", (state, a))
        c.commit()
    chk(f"kv_state={state}: замовлення {'лишається' if vis else 'зникає'} у pending", (a in pending()) is vis)
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET kv_state=NULL WHERE internal_order_id=?", (a,))
    c.commit()

# ── 3. set_kv_state / claim_kv_state
with orders_db.get_connection() as c:
    orders_db.set_kv_state(c, a, "kv_created", kv_order_number="КЗ-1128886", kv_order_id="uuid-1")
    c.commit()
    r = dict(c.execute("SELECT kv_state, kv_order_number, kv_order_id, kv_updated_at FROM orders WHERE internal_order_id=?", (a,)).fetchone())
chk("set_kv_state: стан і поля записано, kv_updated_at проставлено", r["kv_state"] == "kv_created" and r["kv_order_number"] == "КЗ-1128886" and r["kv_order_id"] == "uuid-1" and r["kv_updated_at"])
with orders_db.get_connection() as c:
    orders_db.set_kv_state(c, a, None, kv_error="x")
    c.commit()
    st = c.execute("SELECT kv_state, kv_error FROM orders WHERE internal_order_id=?", (a,)).fetchone()
chk("set_kv_state(state=None): стан НЕ міняється, поле пишеться", st[0] == "kv_created" and st[1] == "x")
for bad in (lambda c: orders_db.set_kv_state(c, a, "nope"), lambda c: orders_db.set_kv_state(c, a, None, evil="1"), lambda c: orders_db.claim_kv_state(c, a, None, "nope")):
    with orders_db.get_connection() as c:
        try:
            bad(c)
            ok = False
        except ValueError:
            ok = True
    chk("невідомий стан/поле → ValueError", ok)
with orders_db.get_connection() as c:
    first = orders_db.claim_kv_state(c, b, None, "kv_creating")
    second = orders_db.claim_kv_state(c, b, None, "kv_creating")
    third = orders_db.claim_kv_state(c, b, "kv_creating", "kv_unknown")
chk("claim_kv_state атомарний: 1-й True, 2-й False (вже взято), перехід creating→unknown True", first is True and second is False and third is True)
with orders_db.get_connection() as c:
    orders_db.set_kv_state(c, a, "delivered", delivery_status="delivered")
    c.commit()
    d = c.execute("SELECT delivered_at FROM orders WHERE internal_order_id=?", (a,)).fetchone()[0]
chk("delivered → delivered_at проставлено", bool(d))

# ── 4. build_ttn_payload extra_props
sender = {"ref": "S", "contact_ref": "SC", "phone": "0971112233", "city_ref": "CS", "address_ref": "AS"}
recipient = {"ref": "R", "contact_ref": "RC", "phone": "0971112244", "city_ref": "CR", "address_ref": "AR"}
order = {"internal_order_id": "site_77", "items": [{"toysi_code": "kv-1", "qty": 1, "price": 300, "dims_cm": [20, 10, 5], "weight_kg": 0.4}]}
base = t.build_ttn_payload(order, sender, recipient)
chk("без extra_props: InfoRegClientBarcodes = internal_order_id, AdditionalInformation нема (як було)", base["InfoRegClientBarcodes"] == "site_77" and "AdditionalInformation" not in base)
ex = t.build_ttn_payload(order, sender, recipient, extra_props={"InfoRegClientBarcodes": "КЗ-1128886", "AdditionalInformation": "КЗ-1128886"})
chk("extra_props: номер замовлення в обох полях (як просила менеджер)", ex["InfoRegClientBarcodes"] == "КЗ-1128886" and ex["AdditionalInformation"] == "КЗ-1128886")
ex2 = t.build_ttn_payload(order, sender, recipient, extra_props={"InfoRegClientBarcodes": "Z" * 80, "AdditionalInformation": "Y" * 300})
chk("extra_props: довжини обрізано (36 і 100)", len(ex2["InfoRegClientBarcodes"]) == 36 and len(ex2["AdditionalInformation"]) == 100)
for badk in ("PayerType", "Cost", "Sender", "RecipientAddress"):
    try:
        t.build_ttn_payload(order, sender, recipient, extra_props={badk: "x"})
        ok = False
    except t.TtnError:
        ok = True
    chk(f"extra_props: поле {badk} НЕ з allowlist → TtnError (не можна підмінити платника/вартість/адресу)", ok)

# ── 5. resolve_sender
calls = []


def fake_call(model, method, props=None, api_key=None):
    calls.append((model, method))
    if method == "getCounterparties":
        return [{"Ref": "CP-REF"}]
    if method == "getCounterpartyContactPersons":
        return [{"Ref": "CONTACT-REF", "Phones": "380971112233"}]
    return []


real_call = np_._call
np_._call = fake_call
t._SENDER_CACHE.clear()
t._SENDER_CACHE_KV.clear()
try:
    kv = t.resolve_sender("kv")
    rt = t.resolve_sender()
    kv2 = t.resolve_sender("kv")
finally:
    np_._call = real_call
chk("resolve_sender('kv'): місто Дніпро + відділення №28 (Ref з довідника НП 21.11)", kv["city_ref"] == "db5c88f0-391c-11dd-90d9-001a92567626" and kv["address_ref"] == "16922832-e1c2-11e3-8c4a-0050568002cf" and kv["ref"] == "CP-REF")
chk("resolve_sender() за замовчуванням — RoyalToys як раніше (Мила №2), кеші окремі", rt["city_ref"] == t.RT_SENDER_CITY_REF and rt["address_ref"] == t.RT_SENDER_WAREHOUSE_REF and kv2 == kv and rt["address_ref"] != kv["address_ref"])
try:
    t.resolve_sender("zzz")
    ok = False
except t.TtnError:
    ok = True
chk("resolve_sender(невідомий тип) → TtnError", ok)

# ── 6. габарити kv-позицій
sup_kv = Path(TMP) / "supplier_kv.json"
sup_kv.write_text(json.dumps({"items": {"kv-201": {"dims_cm": [30, 20, 10], "weight_kg": 0.8}, "kvu-9": {"dims_cm": [10, 10, 10], "weight_kg": 0.2}, "kv-300": {"weight_kg": 1}}}), encoding="utf-8")
o2 = t.with_supplier_dims({"items": [{"toysi_code": "kv-201", "qty": 1, "price": 900}, {"toysi_code": "KVU-9", "qty": 2, "price": 100}]}, kv_supplier_file=str(sup_kv))
chk("with_supplier_dims: kv-/kvu- (будь-який регістр) отримують dims_cm і weight_kg із supplier_kv", o2["items"][0]["dims_cm"] == [30, 20, 10] and o2["items"][1]["dims_cm"] == [10, 10, 10] and o2["items"][0]["weight_kg"] == 0.8)
for label, code in (("нема габаритів", "kv-300"), ("нема в файлі", "kv-999")):
    try:
        t.with_supplier_dims({"items": [{"toysi_code": code, "qty": 1, "price": 100}]}, kv_supplier_file=str(sup_kv))
        ok = False
    except t.TtnError:
        ok = True
    chk(f"with_supplier_dims: kv-позиція {label} → TtnError (ТТН не створюємо)", ok)
try:
    t.with_supplier_dims({"items": [{"toysi_code": "kv-201", "qty": 1, "price": 1}]}, kv_supplier_file=str(Path(TMP) / "nope.json"))
    ok = False
except t.TtnError:
    ok = True
chk("with_supplier_dims: файл supplier_kv.json недоступний → TtnError", ok)
sup_rt = Path(TMP) / "supplier_rt.json"
sup_rt.write_text(json.dumps({"items": {"rt-5": {"dims_cm": [5, 5, 5], "weight_kg": 0.1}}}), encoding="utf-8")
o3 = t.with_supplier_dims({"items": [{"toysi_code": "rt-5", "qty": 1, "price": 100}, {"toysi_code": "12345", "qty": 1, "price": 100}]}, supplier_file=str(sup_rt))
chk("with_supplier_dims: rt- як раніше, Toysi-позиція без змін", o3["items"][0]["dims_cm"] == [5, 5, 5] and "dims_cm" not in o3["items"][1])
o4 = t.with_supplier_dims({"items": [{"toysi_code": "kv-201", "qty": 1, "price": 1, "dims_cm": [1, 2, 3], "weight_kg": 9}]}, kv_supplier_file=str(Path(TMP) / "nope.json"))
chk("with_supplier_dims: якщо dims_cm уже в позиції — файл не читається", o4["items"][0]["dims_cm"] == [1, 2, 3])

# ── 7. fetch_document_pdf
class R:
    def __init__(self, code=200, body=b"%PDF-1.4 x"):
        self.status_code, self.content = code, body


GOOD = "16922832-e1c2-11e3-8c4a-0050568002cf"
real_get = t.requests.get
seen = []
t.requests.get = lambda url, timeout=0: (seen.append(url) or R())
try:
    pdf = t.fetch_document_pdf(GOOD)
finally:
    t.requests.get = real_get
chk("fetch_document_pdf: PDF отримано, URL printDocument/orders[]/{ref}/type/pdf", pdf.startswith(b"%PDF") and "printDocument/orders[]/" + GOOD in seen[0])
for label, getter in (("не GUID", None), ("HTTP 500", R(500)), ("не PDF", R(200, b"<html>"))):
    t.requests.get = (lambda url, timeout=0, g=getter: g)
    try:
        t.fetch_document_pdf("bad" if getter is None else GOOD)
        ok, msg = False, ""
    except t.TtnError as e:
        ok, msg = True, str(e)
    finally:
        t.requests.get = real_get
    chk(f"fetch_document_pdf: {label} → TtnError без ключа у тексті", ok and "TESTKEY" not in msg)


def boom(url, timeout=0):
    raise t.requests.RequestException("http://x?apiKey=TESTKEY-SECRET")


t.requests.get = boom
try:
    t.fetch_document_pdf(GOOD)
    ok, msg = False, ""
except t.TtnError as e:
    ok, msg = True, str(e)
finally:
    t.requests.get = real_get
chk("fetch_document_pdf: мережевий збій → TtnError, ключ НЕ витікає", ok and "TESTKEY" not in msg)

print()
if F:
    print(f"FAIL: {len(F)}")
sys.exit(1 if F else 0)
