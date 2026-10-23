#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_site_mixed_split.py — ЗМІШАНИЙ кошик (Toysi + RoyalToys) приймається й ділиться на ДВА замовлення (рішення власника 22.10.2026: вікно
«кількома посилками» + одна оплата + два замовлення; спершу за прапорцем SITE_MIXED_ORDERS).

ІНВАРІАНТИ:
  - прапорець ВИМКНЕНО (за замовчуванням) → поведінка як раніше: змішаний кошик 400, у БД нічого, подія mixed_toysi_rt в обліку;
  - прапорець увімкнено + prepaid → 200: батьківський order_id (для LiqPay/сторінки подяки), parcels[2], у БД РІВНО два рядки (Toysi і RT) з власними позиціями й
    site_charged_total, сума частин = total, рядка з батьківським id НЕМА; дочірні id детерміновані й відповідають ORDER_ID_RE;
  - змішаний + COD, змішаний із zb-, неоднозначна сума частин → відмова, у БД нічого;
  - колбек LiqPay за БАТЬКІВСЬКИМ id: сума має дорівнювати СУМІ частин (інакше 400, нічого не підтверджено); при збігу — обидва підтверджені, payment_id у обох,
    повтор колбека ідемпотентний (без повторних сповіщень); якщо одного з дочірніх нема → 404;
  - /api/order/paid за батьківським id: paid=true лише коли ОБИДВІ частини оплачені (сума = сума частин, позиції обʼєднані); sandbox/неоплачене → false;
  - звичайні кошики (лише Toysi, лише rt-) — як раніше: один рядок, без parcels.
Тимчасова БД, реальний HTTP-сервер, без мережі, LiqPay підмінено.
"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["SITE_REJECTED_BASKETS_FILE"] = os.path.join(tempfile.mkdtemp(), "rej.jsonl")
os.environ.pop("SITE_MIXED_ORDERS", None)
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "site"))
import orders_db  # noqa: E402
import site_order_api as api  # noqa: E402
import site_rejected_baskets as rb  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


api.price_map = lambda: {"100": {"price": 250, "name": "Іграшка Toysi"}, "200": {"price": 120, "name": "Друга Toysi"},
                         "rt-101": {"price": 400, "name": "Іграшка RT"}, "zb-5": {"price": 90, "name": "Іграшка ZB"}}
api.stock_snapshot = lambda: ({"100": 9, "200": 9, "rt-101": 9, "zb-5": 9}, None)
orders_db.init_db()

# LiqPay: підміна (без ключів і мережі)
CB = {}
api.liqpay_client.is_configured = lambda: True
api.liqpay_client.mode_problem = lambda: ""
api.liqpay_client.build_checkout = lambda **kw: {"action_url": "https://liqpay.test", "data": "d", "signature": "s", "sandbox": False, "_kw": kw}
api.liqpay_client.verify_callback = lambda data, sig: CB["res"]
notes = []
api._notify_new_order = lambda order, total, test=False, note="": notes.append((order.get("order_id"), total, test, note))

srv = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"


def call(method, path, body=None, form=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if form is not None:
        data = ("&".join(f"{k}={v}" for k, v in form.items())).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw.startswith("{") else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        return e.code, (json.loads(raw) if raw.startswith("{") else raw)


def payload(items, pm="prepaid"):
    return {"items": items, "name": "Іван Петренко", "phone": "0501234567", "city_name": "Київ", "warehouse_name": "Відділення 5", "payment_method": pm}


def rows():
    with orders_db.get_connection() as c:
        return c.execute("select internal_order_id, items, site_charged_total, payment_confirmed, status, public_number, liqpay_payment_id from orders where platform='site' order by internal_order_id").fetchall()


MIXED = [{"id": "100", "qty": 2}, {"id": "200", "qty": 1}, {"id": "rt-101", "qty": 1}]
TOYSI_SUM, RT_SUM = 250 * 2 + 120, 400
TOTAL = TOYSI_SUM + RT_SUM

# --- прапорець вимкнено (за замовчуванням)
s, j = call("POST", "/api/order", payload(MIXED))
chk("прапорець ВИМКНЕНО: змішаний → 400, у БД нічого", s == 400 and rows() == [])
ev = [json.loads(x) for x in Path(os.environ["SITE_REJECTED_BASKETS_FILE"]).read_text(encoding="utf-8").splitlines()]
chk("прапорець ВИМКНЕНО: подія mixed_toysi_rt в обліку (регресійний гард)", len(ev) == 1 and ev[0]["kind"] == rb.KIND_MIXED)

# --- прапорець увімкнено
os.environ["SITE_MIXED_ORDERS"] = "1"
s, j = call("POST", "/api/order", payload(MIXED, "cod"))
chk("прапорець ON + COD змішаний → 400 (лише передоплата), у БД нічого", s == 400 and rows() == [] and "передоплат" in j.get("error", ""))
s, j = call("POST", "/api/order", payload(MIXED + [{"id": "zb-5", "qty": 1}]))
chk("прапорець ON + змішаний із zb- → 400, у БД нічого", s == 400 and rows() == [])
s, j = call("POST", "/api/order", payload([{"id": "rt-101", "qty": 1}, {"id": "zb-5", "qty": 1}]))
chk("прапорець ON + rt- і zb- без Toysi → 400 (ZooBaza окремо)", s == 400 and rows() == [])

s, j = call("POST", "/api/order", payload(MIXED))
PARENT = j.get("order_id")
chk("прапорець ON + prepaid змішаний → 200, order_id (батьківський) відповідає формату, total = сума обох частин", s == 200 and api.ORDER_ID_RE.fullmatch(PARENT or "") and j["total"] == TOTAL)
r = rows()
chk("у БД РІВНО два рядки; рядка з батьківським id НЕМА", len(r) == 2 and f"site_{PARENT}" not in [x[0] for x in r])
kid_t, kid_r = api.mixed_child_ids(PARENT)
chk("дочірні id детерміновані від батьківського й відповідають ORDER_ID_RE", {x[0] for x in r} == {f"site_{kid_t}", f"site_{kid_r}"} and api.ORDER_ID_RE.fullmatch(kid_t) and api.ORDER_ID_RE.fullmatch(kid_r) and kid_t != kid_r)
by = {x[0]: x for x in r}
it_t = json.loads(by[f"site_{kid_t}"][1])
it_r = json.loads(by[f"site_{kid_r}"][1])
chk("рядок Toysi має лише Toysi-позиції, рядок RT — лише rt-позиції", {i["toysi_code"] for i in it_t} == {"100", "200"} and {i["toysi_code"] for i in it_r} == {"rt-101"})
chk("site_charged_total частин: Toysi=620, RT=400, сума = total; обидва неоплачені (payment_confirmed=0)", by[f"site_{kid_t}"][2] == TOYSI_SUM and by[f"site_{kid_r}"][2] == RT_SUM and by[f"site_{kid_t}"][2] + by[f"site_{kid_r}"][2] == j["total"]
    and by[f"site_{kid_t}"][3] == 0 and by[f"site_{kid_r}"][3] == 0)
chk("parcels: два елементи (toysi, rt) з сумами, у відповіді є liqpay для БАТЬКІВСЬКОГО id і повна сума",
    [p["kind"] for p in j["parcels"]] == ["toysi", "rt"] and [p["total"] for p in j["parcels"]] == [TOYSI_SUM, RT_SUM]
    and j["liqpay"]["action_url"] == "https://liqpay.test")
with orders_db.get_connection() as c:
    grp = c.execute("select internal_order_id, site_group_id from orders where platform='site' and site_group_id is not null").fetchall()
chk("створення змішаного прописує site_group_id = БАТЬКІВСЬКИЙ номер в обох дочірніх", {g[0] for g in grp} == {f"site_{kid_t}", f"site_{kid_r}"} and all(g[1] == PARENT for g in grp))
chk("публічні номери призначені дочірнім (PT-NNNNNN)", all(x[5] and orders_db.PUBLIC_NUMBER_RE.fullmatch(x[5]) for x in r) and j.get("public_number") == by[f"site_{kid_t}"][5])

# --- paid до колбека
s, jp = call("GET", f"/api/order/paid?o={PARENT}")
chk("/api/order/paid ДО колбека → paid=false", s == 200 and jp == {"paid": False})

# --- колбек: неправильна сума
CB["res"] = {"valid": True, "status": "success", "paid": True, "currency": "UAH", "order_id": PARENT, "amount": TOYSI_SUM, "data": {"payment_id": 777}}
s, t = call("POST", "/api/liqpay/callback", form={"data": "x", "signature": "y"})
chk("колбек із сумою лише однієї частини → 400 amount mismatch, нічого не підтверджено", s == 400 and "mismatch" in str(t) and all(x[3] == 0 for x in rows()) and notes == [])

# --- колбек: правильна сума
CB["res"]["amount"] = TOTAL
s, t = call("POST", "/api/liqpay/callback", form={"data": "x", "signature": "y"})
r = rows()
chk("колбек із сумою обох частин → 200; ОБИДВА підтверджені, payment_id у обох", s == 200 and all(x[3] == 1 for x in r) and all(str(x[6]) == "777" for x in r))
chk("сповіщення власнику: по одному на кожну частину (2), з позначкою «ЗМІШАНЕ»", len(notes) == 2 and all("ЗМІШАНЕ" in n[3] for n in notes) and {n[1] for n in notes} == {TOYSI_SUM, RT_SUM})
s, t = call("POST", "/api/liqpay/callback", form={"data": "x", "signature": "y"})
chk("повтор колбека ідемпотентний: 200, без нових сповіщень", s == 200 and len(notes) == 2)
s, jp = call("GET", f"/api/order/paid?o={PARENT}")
chk("/api/order/paid ПІСЛЯ колбека → paid=true, value = сума частин, позиції обʼєднані (3 рядки)", s == 200 and jp["paid"] is True and jp["value"] == TOTAL and len(jp["items"]) == 3)
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET payment_confirmed=0 WHERE internal_order_id=?", (f"site_{kid_r}",))
    c.commit()
s, jp = call("GET", f"/api/order/paid?o={PARENT}")
chk("/api/order/paid: оплачена лише одна частина → paid=false", jp == {"paid": False})
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET payment_confirmed=1, status='sandbox_test' WHERE internal_order_id=?", (f"site_{kid_r}",))
    c.commit()
s, jp = call("GET", f"/api/order/paid?o={PARENT}")
chk("/api/order/paid: одна з частин sandbox_test → paid=false", jp == {"paid": False})

# --- нема одного з дочірніх → колбек 404
s, j2 = call("POST", "/api/order", payload(MIXED))
P2 = j2["order_id"]
k2t, k2r = api.mixed_child_ids(P2)
with orders_db.get_connection() as c:
    c.execute("DELETE FROM orders WHERE internal_order_id=?", (f"site_{k2r}",))
    c.commit()
CB["res"] = {"valid": True, "status": "success", "paid": True, "currency": "UAH", "order_id": P2, "amount": TOTAL, "data": {"payment_id": 888}}
n_before = len(notes)
s, t = call("POST", "/api/liqpay/callback", form={"data": "x", "signature": "y"})
chk("колбек за батьківським id, коли одного з дочірніх нема → 404 (не підтверджуємо половину)", s == 404 and len(notes) == n_before)
s, jp = call("GET", f"/api/order/paid?o={P2}")
chk("/api/order/paid при неповній парі → paid=false", jp == {"paid": False})

# --- звичайні кошики не змінились
before = len(rows())
s, jt = call("POST", "/api/order", payload([{"id": "100", "qty": 1}]))
s2, jr = call("POST", "/api/order", payload([{"id": "rt-101", "qty": 2}]))
chk("звичайні кошики (лише Toysi, лише rt-): 200, по ОДНОМУ рядку, без parcels", s == 200 and s2 == 200 and "parcels" not in jt and "parcels" not in jr and len(rows()) == before + 2)
CB["res"] = {"valid": True, "status": "success", "paid": True, "currency": "UAH", "order_id": jt["order_id"], "amount": 250, "data": {"payment_id": 999}}
s, t = call("POST", "/api/liqpay/callback", form={"data": "x", "signature": "y"})
chk("колбек звичайного замовлення працює як раніше", s == 200)
CB["res"] = {"valid": True, "status": "success", "paid": True, "currency": "UAH", "order_id": "PT-20260101-000000-abcdef", "amount": 1, "data": {}}
s, t = call("POST", "/api/liqpay/callback", form={"data": "x", "signature": "y"})
chk("колбек для невідомого id → 404, як раніше", s == 404)

# --- unit: split із промокодом і розбіжність сум
order = {"order_id": "PT-20261023-120000-abcdef", "items": [{"toysi_code": "100", "qty": 2, "price": 237, "list_price": 250, "name": "a"},
                                                             {"toysi_code": "rt-101", "qty": 1, "price": 380, "list_price": 400, "name": "b"}],
         "promo_code": "PLUTUS5", "promo_discount_total": 46}
(ct, tt), (cr, tr_) = api.split_mixed_children(order, 237 * 2 + 380)
chk("split з промокодом: суми частин 474+380, знижка по частинах 26 і 20 (сума 46)", (tt, tr_) == (474, 380) and ct["promo_discount_total"] == 26 and cr["promo_discount_total"] == 20)
try:
    api.split_mixed_children(order, 999)
    bad = False
except api.OrderError:
    bad = True
chk("split: сума частин ≠ total → OrderError (fail-closed)", bad)
only_t = {"order_id": "PT-20261023-120000-abcdef", "items": [{"toysi_code": "100", "qty": 1, "price": 5}]}
try:
    api.split_mixed_children(only_t, 5)
    bad = False
except api.OrderError:
    bad = True
chk("split: кошик не змішаний → OrderError", bad)
chk("mixed_child_ids детермінований і не залежить від порядку викликів", api.mixed_child_ids(PARENT) == (kid_t, kid_r))
chk("mixed_children_rows відхиляє сміттєвий id (SQL/довгий/порожній)", all(api.mixed_children_rows(None, x) == [] for x in ("", "PT-1; DROP", "x" * 200, None)))

srv.shutdown()
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
