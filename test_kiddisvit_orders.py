#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_kiddisvit_orders.py — етап А модуля замовлень KIDDISVIT (21.11.2026): kiddisvit_orders.py (чиста логіка) + методи запису в kiddisvit_api.py.
ІНВАРІАНТИ: (1) запис (orders/create, ttn-data, assembly) ЛИШЕ за KV_ORDERS_LIVE=1 — інакше KiddisvitError ДО мережі; orders/search (читання) без воріт; (2) POST без автоповторів, рівно ОДИН запит: невизначене (5xx, таймаут, обрив, не-JSON, 200 без
status=success, 2xx крім 200, 3xx, 408/409) → KiddisvitUncertain; однозначна відмова до обробки (400/401/403/404/405/415/422/429) → звичайний KiddisvitError; редірект не виконується;
ворота діють і на рівні _request (будь-який POST orders/* крім search); (3) assembly/ttn-data парсяться без обгортки status/data; (4) map_items: лише kv-/kvu-, є в постачальника, собівартість, ОДИН склад,
залишок ≥ кількості, ціна клієнта ≥ собівартості, однакові коди зливаються; (5) recipient/платіж/чек/платформа — перевірки до побудови запиту; (6) payload: my_ttn, без redelivery, Ref міста й відділення;
(7) interpret_create: status_OK 0 → reserved=False; нема number/id → OrderBlocked. Без мережі. `python test_kiddisvit_orders.py` → exit 0/1.
"""
import io
import json
import os
import sys
import urllib.error
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ.pop("KV_ORDERS_LIVE", None)
sys.path.insert(0, str(HERE))
import kiddisvit_api as api  # noqa: E402
import kiddisvit_orders as ko  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


class Resp:
    def __init__(self, body, status=200):
        self._b, self.status = body if isinstance(body, bytes) else body.encode("utf-8"), status

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code):
    class H(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)
    return urllib.error.HTTPError("u", code, "x", H(), io.BytesIO(b"SECRET-BODY"))


class Fake:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def __call__(self, req, timeout):
        self.calls.append((req.get_method(), req.full_url, req.data))
        x = self.script.pop(0)
        if isinstance(x, Exception):
            raise x
        return x


def client(script):
    f = Fake(script)
    return api.KiddisvitClient(key="K", opener=f, sleep=lambda s: None, clock=lambda: 0.0), f


# ── 1. ворота запису
c, f = client([])
for name, fn in (("orders_create", lambda: c.orders_create({"x": 1})), ("orders_ttn_data", lambda: c.orders_ttn_data("1", [])), ("orders_assembly", lambda: c.orders_assembly("1", []))):
    try:
        fn()
        ok = False
    except api.KiddisvitError:
        ok = True
    chk(f"{name} без KV_ORDERS_LIVE=1 → KiddisvitError ДО мережі (0 запитів)", ok and not f.calls)
os.environ["KV_ORDERS_LIVE"] = "true"
chk("KV_ORDERS_LIVE лише значення «1» вмикає (true — ні)", api.KiddisvitClient.orders_live() is False)
os.environ["KV_ORDERS_LIVE"] = "1"
chk("KV_ORDERS_LIVE=1 вмикає", api.KiddisvitClient.orders_live() is True)

# ── 2. orders_create: один POST, без автоповторів
c, f = client([Resp(json.dumps({"status": "success", "data": {"number": "КЗ-1", "id": "abc", "status_OK": 1}}))])
res = c.orders_create({"a": 1})
chk("orders_create: один POST на orders/create, повертає data", len(f.calls) == 1 and f.calls[0][0] == "POST" and f.calls[0][1].endswith("/orders/create") and res["number"] == "КЗ-1")
for label, script, exc in (("HTTP 500", [http_error(500)], api.KiddisvitUncertain), ("HTTP 429", [http_error(429)], api.KiddisvitError), ("таймаут", [TimeoutError()], api.KiddisvitUncertain),
                           ("HTTP 200 не-JSON", [Resp("<html>")], api.KiddisvitUncertain), ("обрив з'єднання", [urllib.error.URLError("x")], api.KiddisvitUncertain)):
    c, f = client(script + [Resp(json.dumps({"status": "success", "data": {}}))])
    try:
        c.orders_create({"a": 1})
        got = None
    except api.KiddisvitError as e:
        got = e
    chk(f"orders_create {label}: помилка {exc.__name__} (точний тип), РІВНО 1 запит (автоповтору нема)", type(got) is exc and len(f.calls) == 1)
for code in (201, 202, 204):
    c, f = client([Resp(json.dumps({"status": "success", "data": {"number": "КЗ-1", "id": "x"}}), status=code)])
    try:
        c.orders_create({"a": 1})
        got = None
    except api.KiddisvitError as e:
        got = e
    chk(f"orders_create HTTP {code} (не чистий 200): KiddisvitUncertain, 1 запит (раніше ставало «точно не створено»)", type(got) is api.KiddisvitUncertain and len(f.calls) == 1)
for code in (301, 302, 307, 308, 408, 409, 502, 524):
    c, f = client([http_error(code)])
    try:
        c.orders_create({"a": 1})
        got = None
    except api.KiddisvitError as e:
        got = e
    chk(f"orders_create HTTP {code}: KiddisvitUncertain, 1 запит", type(got) is api.KiddisvitUncertain and len(f.calls) == 1)
for code in (400, 401, 403, 404, 405, 415, 422, 429):
    c, f = client([http_error(code)])
    try:
        c.orders_create({"a": 1})
        got = None
    except api.KiddisvitError as e:
        got = e
    chk(f"orders_create HTTP {code}: однозначна відмова KiddisvitError (не Uncertain), 1 запит, без тіла в повідомленні", type(got) is api.KiddisvitError and len(f.calls) == 1 and "SECRET" not in str(got))
chk("POST із ключем НЕ йде за редіректом (NoRedirect.redirect_request → None)", api._NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://x") is None)
c, f = client([Resp(json.dumps({"status": "success", "data": {}}))])
try:
    c.orders_create({"a": 1})
    got = None
except api.KiddisvitError as e:
    got = e
chk("orders_create: success з порожнім data → KiddisvitUncertain (раніше мовчки {})", type(got) is api.KiddisvitUncertain)
c, f = client([http_error(400)])
try:
    c.orders_create({"a": 1})
    got = None
except api.KiddisvitError as e:
    got = e
chk("HTTP 400 (валідація): звичайний KiddisvitError, НЕ Uncertain (замовлення точно не створене)", got is not None and not isinstance(got, api.KiddisvitUncertain) and "SECRET" not in str(got))
c, f = client([Resp(json.dumps({"status": "error", "data": {}}))])
try:
    c.orders_create({"a": 1})
    got = None
except api.KiddisvitError as e:
    got = e
chk("запис: 200 зі status≠success → KiddisvitUncertain (НЕ «точно не створено»), 1 запит", type(got) is api.KiddisvitUncertain and len(f.calls) == 1)
# ворота на рівні _request
os.environ.pop("KV_ORDERS_LIVE", None)
c, f = client([Resp(json.dumps({"status": "success", "data": {"x": 1}}))])
try:
    c._request("POST", "orders/create", {"a": 1})
    got = None
except api.KiddisvitError as e:
    got = e
chk("_request('POST','orders/create') напряму БЕЗ KV_ORDERS_LIVE=1 → відмова ДО мережі", got is not None and not f.calls)
c, f = client([http_error(503), Resp(json.dumps({"status": "success", "data": {}}))])
os.environ["KV_ORDERS_LIVE"] = "1"
try:
    c._request("POST", "orders/anything", {"a": 1}, retry=True)       # навіть із retry=True запис orders/* без автоповторів
    got = None
except api.KiddisvitError as e:
    got = e
chk("_request POST orders/*: retry=True ігнорується — 503 → Uncertain, 1 запит", type(got) is api.KiddisvitUncertain and len(f.calls) == 1)
c, f = client([http_error(503), Resp(json.dumps({"status": "success", "data": {"products": []}}))])
c.prices_by_brand("c", "k", "b")
chk("читальний POST products/rests/prices ЛИШАЄТЬСЯ з автоповтором (503 → 200, 2 запити)", len(f.calls) == 2)
os.environ["KV_ORDERS_LIVE"] = "1"

# ── 3. assembly / ttn-data без обгортки; search — читання без воріт
c, f = client([Resp(json.dumps({"success": True}))])
chk("orders_assembly: {'success': true} без обгортки status/data розібрано", c.orders_assembly("77", [{"type": "marking", "BinaryData": "AA=="}]).get("success") is True and json.loads(f.calls[0][2])["ref_Order"] == "77")
c, f = client([Resp(json.dumps([{"id": "77", "status": "success", "errorMessage": ""}]))])
r = c.orders_ttn_data("77", [{"type": "document", "BinaryData": "AA=="}])
chk("orders_ttn_data: масив [{id,status,errorMessage}] розібрано; тіло {id, Documents}", r and r[0]["status"] == "success" and set(json.loads(f.calls[0][2])) == {"id", "Documents"})
os.environ.pop("KV_ORDERS_LIVE", None)
c, f = client([Resp(json.dumps({"status": "success", "data": {"Orders": [{"number": "КЗ-1", "TTN": "20451"}]}}))])
chk("orders_search (читання) працює БЕЗ воріт", c.orders_search("c", "k", "20261101", "20261121")[0]["TTN"] == "20451" and len(f.calls) == 1)

# ── 4. map_items
SUP = {"kv-100": {"cost": 100.0, "warehouse": "Dnepr", "stock": 50, "name": "Фігурка", "condition": "new"},
       "kv-101": {"cost": 80.0, "warehouse": "Dnepr", "stock": 3, "name": "Плюш", "condition": "new"},
       "kvu-200": {"cost": 60.0, "warehouse": "NTV20", "stock": 10, "name": "Уцінка", "condition": "damaged_packaging"}}


def order(items, **kw):
    o = {"internal_order_id": "site_77", "platform": "site", "payment_method": "prepaid", "payment_confirmed": 1, "checkbox_ettn_registered_at": "2026-11-21T10:00:00", "customer_name": "Іваненко Марія", "phone": "0971234567",
         "np_city_ref": "city-ref", "np_warehouse_number": "28", "items": items}
    o.update(kw)
    return o


def blocked(fn):
    try:
        fn()
        return None
    except ko.OrderBlocked as e:
        return str(e)


it = lambda code, qty=1, price=150: {"toysi_code": code, "qty": qty, "price": price, "name": "Т"}
m = ko.map_items(order([it("kv-100", 2), it("kv-100", 1)]), SUP)
chk("map_items: однакові коди зливаються (2+1=3), product_id 100, склад Dnepr", len(m) == 1 and m[0]["qty"] == 3 and m[0]["product_id"] == "100" and m[0]["warehouse"] == "Dnepr")
chk("map_items: kvu- (NTV20) → product_id без префікса", ko.map_items(order([it("kvu-200")]), SUP)[0]["product_id"] == "200")
chk("змішаний кошик (rt-/числовий id) → OrderBlocked «змішане»", "змішане" in (blocked(lambda: ko.map_items(order([it("kv-100"), it("rt-5")]), SUP)) or "") and "змішане" in (blocked(lambda: ko.map_items(order([it("12345")]), SUP)) or ""))
chk("нема в supplier_kv.json → OrderBlocked", "нема в supplier_kv" in (blocked(lambda: ko.map_items(order([it("kv-999")]), SUP)) or ""))
chk("залишок 3 < замовлено 4 → OrderBlocked", "залишок" in (blocked(lambda: ko.map_items(order([it("kv-101", 4)]), SUP)) or ""))
chk("склади Dnepr+NTV20 в одному замовленні → OrderBlocked (не підтверджено менеджером)", "складів" in (blocked(lambda: ko.map_items(order([it("kv-100"), it("kvu-200")]), SUP)) or ""))
chk("ціна клієнта 90 < собівартості 100 → OrderBlocked (збиток)", "нижча за собівартість" in (blocked(lambda: ko.map_items(order([it("kv-100", 1, 90)]), SUP)) or ""))
chk("кількість 0, 51 і «x» → OrderBlocked", all(blocked(lambda q=q: ko.map_items(order([it("kv-100", q)]), SUP)) for q in (0, 51, "x")))
chk("порожнє замовлення → OrderBlocked", blocked(lambda: ko.map_items(order([]), SUP)) is not None)
chk("кількість True/2.5 → OrderBlocked (раніше тихо 1/2); 2.0 → 2", blocked(lambda: ko.map_items(order([it("kv-100", True)]), SUP)) and blocked(lambda: ko.map_items(order([it("kv-100", 2.5)]), SUP))
    and ko.map_items(order([it("kv-100", 2.0)]), SUP)[0]["qty"] == 2)
chk("ціна NaN / inf / 0 → OrderBlocked", all(blocked(lambda p=p: ko.map_items(order([it("kv-100", 1, p)]), SUP)) for p in (float("nan"), float("inf"), 0)))
chk("злиття 30+30=60 > 50 → OrderBlocked (ліміт після злиття)", "сумарна" in (blocked(lambda: ko.map_items(order([it("kv-100", 30), it("kv-100", 30)]), SUP)) or ""))
bad = dict(SUP, **{"kv-300": {"cost": 0, "warehouse": "Dnepr", "stock": 5}, "kv-301": {"cost": 5, "warehouse": "Kyiv", "stock": 5}})
chk("нульова собівартість / невідомий склад → OrderBlocked", blocked(lambda: ko.map_items(order([it("kv-300")]), bad)) and blocked(lambda: ko.map_items(order([it("kv-301")]), bad)))
chk("product_id: kv-, kvu-, сміття", ko.product_id("KV-12") == "12" and ko.product_id("kvu-9") == "9" and ko.product_id("rt-1") == "" and ko.product_id("kv-../x") == "")

# ── 5. recipient / перевірки замовлення
chk("recipient: «0971234567» → +380971234567", ko.recipient(order([]))[1] == "+380971234567" and ko.recipient(order([]))[0] == "Іваненко Марія")
chk("recipient: ім'я з одного слова / короткий телефон → OrderBlocked", blocked(lambda: ko.recipient(order([], customer_name="Марія"))) and blocked(lambda: ko.recipient(order([], phone="12345"))))
IDS = {"OrganizationID": "o", "ClientID": "c", "ContractID": "k", "shopID": "s"}
LOOK = lambda city, num: "wh-ref" if (city, num) == ("city-ref", "28") else None
ok_rep = ko.dry_run(order([it("kv-100", 2)]), SUP, IDS, LOOK)
chk("dry_run: придатне замовлення → ok, payload, собівартість 200", ok_rep["ok"] and ok_rep["payload"] and ok_rep["cost_total"] == 200.0 and ok_rep["items"] == [("kv-100", 2, "Dnepr")])
p = ok_rep["payload"]
chk("payload: my_ttn=true, БЕЗ redelivery, delivery_payer=recipient, NewPost/DeliveryServiceWarehouse, Ref міста/відділення, Items[Product,Warehouse,Quantity]",
    p["my_ttn"] is True and "redelivery" not in p and p["delivery_payer"] == "recipient" and p["deliveryService"] == "NewPost" and p["Shipment"] == "DeliveryServiceWarehouse"
    and p["DeliveryCity"] == "city-ref" and p["DeliveryWarehouse"] == "wh-ref" and p["Items"] == [{"Product": "100", "Warehouse": "Dnepr", "Quantity": 2}] and p["recipient_phone"] == "+380971234567")
for label, kw, frag in (("COD", {"payment_method": "cod"}, "передоплат"), ("не підтверджено", {"payment_confirmed": 0}, "передоплат"), ("не з сайту", {"platform": "rozetka"}, "платформа"),
                        ("скасоване", {"cancelled_at": "2026-11-21"}, "скасоване"), ("sandbox", {"status": "sandbox_test"}, "скасоване"), ("без чека", {"checkbox_ettn_registered_at": None}, "чек"),
                        ("чек повернення", {"refund_receipt_at": "2026-11-21"}, "повернен"), ("claim чека повернення", {"refund_receipt_claim_at": "2026-11-21"}, "повернен"),
                        ("доставка returned", {"delivery_status": "returned"}, "повернен"), ("доставка cancelled", {"delivery_status": "cancelled"}, "повернен"),
                        ("eva_cancelled_before_forward", {"status": "eva_cancelled_before_forward"}, "скасоване"),
                        ("без номера відділення", {"np_warehouse_number": ""}, "відділення НП"), ("без Ref міста", {"np_city_ref": None}, "відділення НП")):
    r = ko.dry_run(order([it("kv-100")], **kw), SUP, IDS, LOOK)
    chk(f"dry_run: {label} → НЕ ok, причина містить «{frag}»", (not r["ok"]) and frag in " ".join(r["problems"]) and r["payload"] is None)
chk("dry_run: нема ClientID у clients/list → не ok", not ko.dry_run(order([it("kv-100")]), SUP, dict(IDS, ClientID=""), LOOK)["ok"])
chk("dry_run нічого не кидає назовні (порожнє замовлення, None, items-рядок, ids=None)", ko.dry_run({}, SUP, IDS)["ok"] is False and ko.dry_run(None, SUP, IDS)["ok"] is False
    and ko.dry_run(order("рядок"), SUP, IDS)["ok"] is False and ko.dry_run(order([it("kv-100")]), SUP, None, LOOK)["ok"] is False)
# Ref відділення: сайтове замовлення без np_ref_id
r_nol = ko.dry_run(order([it("kv-100")]), SUP, IDS)
chk("сайтове замовлення БЕЗ np_ref_id і без lookup → заблоковано чесною причиною (резолв — етап Б)", (not r_nol["ok"]) and "резолв" in " ".join(r_nol["problems"]))
chk("lookup повернув None → «не знайдено в довіднику НП»", "не знайдено" in " ".join(ko.dry_run(order([it("kv-100")]), SUP, IDS, lambda c, n: None)["problems"]))
def _boom(c, n):
    raise RuntimeError("down")
chk("lookup кинув виняток → блок «довідник НП недоступний», без винятку назовні", "недоступний" in " ".join(ko.dry_run(order([it("kv-100")]), SUP, IDS, _boom)["problems"]))
chk("є np_ref_id (Rozetka/Prom/EVA) → береться він, lookup не потрібен", ko.dry_run(order([it("kv-100")], np_ref_id="given-ref"), SUP, IDS)["payload"]["DeliveryWarehouse"] == "given-ref")
before = json.dumps(order([it("kv-100")]), sort_keys=True)
o = order([it("kv-100")])
ko.dry_run(o, SUP, IDS, LOOK)
chk("dry_run не мутує вхідне замовлення", json.dumps(o, sort_keys=True) == before)

# ── 6. interpret_create
chk("interpret_create: status_OK=1 → reserved", ko.interpret_create({"number": "КЗ-1128886", "id": "i1", "status_OK": 1}) == {"number": "КЗ-1128886", "id": "i1", "reserved": True, "comment": ""})
r0 = ko.interpret_create({"number": "КЗ-2", "id": "i2", "status_OK": 0, "Comment": "Замовлення створено, але не проведено і товар не зарезервовано"})
chk("interpret_create: status_OK=0 → reserved=False + Comment (ручне рішення)", r0["reserved"] is False and "не зарезервовано" in r0["comment"])
def uncertain(fn):
    try:
        fn()
        return False
    except api.KiddisvitUncertain:
        return True
chk("interpret_create: без number або id → KiddisvitUncertain (НЕ OrderBlocked: create міг пройти, повтор заборонено)",
    uncertain(lambda: ko.interpret_create({"id": "x"})) and uncertain(lambda: ko.interpret_create({"number": "КЗ-1"})) and uncertain(lambda: ko.interpret_create(None)))

print()
if F:
    print(f"FAIL: {len(F)}")
sys.exit(1 if F else 0)
