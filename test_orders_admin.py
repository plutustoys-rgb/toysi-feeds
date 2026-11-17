#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_orders_admin.py — огляд замовлень у локальній панелі (власник 10.11.2026: «усе проходить всліпу», без Telegram).
Перевіряє: чисті функції stage/total/summarize; ФІКСОВАНИЙ віддалений скрипт на справжній схемі orders.db (читання, фільтр днів, PII лише в рядках, режим mode=ro); fetch() з підміненим ssh
(успіх / збій / таймаут / не-JSON, ін'єкція в days неможлива); маршрут /api/orders панелі (CSRF 403 без X-Panel; з заголовком — 200 і дані). `python test_orders_admin.py` → exit 0/1."""
import http.client
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TMP = tempfile.mkdtemp()
os.environ["ORDERS_DB_PATH"] = os.path.join(TMP, "orders.db")
import orders_admin as oa  # noqa: E402
import orders_db  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


NOW = datetime(2026, 11, 10, 15, 0, 0)

# ── stage
def S(**kw):
    base = {"platform": "site", "status": "awaiting_manual_confirmation", "payment_method": "prepaid", "payment_confirmed": 0, "created_at": "2026-11-10T14:50:00"}
    base.update(kw)
    return oa.stage(base, NOW)


chk("передоплата без оплати 10 хв → «ЧЕКАЄ ОПЛАТИ», без «зависло»", S()[0] == "unpaid" and "10 хв" in S()[1] and "зависло" not in S()[1] and S()[2] is False)
chk("передоплата без оплати 450 хв (як PT-163750) → зависло + увага", S(created_at="2026-11-10T07:27:15")[0] == "unpaid" and "зависло" in S(created_at="2026-11-10T07:27:15")[1] and S(created_at="2026-11-10T07:27:15")[2])
chk("sandbox_test → ТЕСТ", S(status="sandbox_test")[0] == "test")
chk("COD прийнято, не передано → accepted (не «чекає оплати»)", S(payment_method="cod", status="new")[0] == "accepted")
chk("передано в Toysi з ТТН", S(forwarded_to_toysi_at="2026-11-10T08:00:00", toysi_order_id="100", toysi_ttn="204")[1] == "передано в Toysi #100 · ТТН 204")
chk("delivery_status: shipped/delivered/cancelled/returned", [S(delivery_status=x)[0] for x in ("shipped", "delivered", "cancelled", "returned")] == ["shipped", "delivered", "cancelled", "returned"])
chk("скасовано з причиною", "причина" in S(cancelled_at="2026-11-10T09:00:00", cancel_reason="причина X")[1] or "причина X" in S(cancelled_at="2026-11-10T09:00:00", cancel_reason="причина X")[1])

chk("toysi_error → помилка + увага (не «прийнято»)", S(status="toysi_error")[0] == "error" and S(status="toysi_error")[2] is True)
chk("*_cancelled_before_forward (prom/eva/rozetka) → скасовано", all(S(status=f"{p}_cancelled_before_forward")[0] == "cancelled" for p in ("prom", "eva", "rozetka")))
chk("delivery_status expired / unknown_77 → увага, не «передано в Toysi»", S(delivery_status="expired")[0] == "expired" and S(delivery_status="expired")[2] and S(delivery_status="unknown_77")[0] == "unknown" and S(delivery_status="unknown_77")[2])
chk("помилка має пріоритет над «чекає оплати» (prepaid + toysi_error)", S(status="toysi_error", payment_method="prepaid")[0] == "error")

# ── 12.11: замовлення, що стоять (eva_8-084262173: оплачене, RoyalToys, rt_error «City not found», у панелі було просто «прийнято»)
EVA = dict(platform="eva", status="new", payment_method="prepaid", payment_confirmed=1, created_at="2026-11-10T08:00:00", rt_state=None,
           rt_error="збій NovaPoshtaAPIError: API повернув помилку: ['City not found']")
c1, l1, a1 = oa.stage(EVA, NOW)
chk("оплачене з rt_error → «ЗУПИНЕНО конвеєром» + причина + увага (не «прийнято»)", c1 == "stuck" and "City not found" in l1 and a1 is True)
chk("rt_error зникає після успіху → звичайний стан, без тривоги", oa.stage({**EVA, "rt_error": None, "rt_state": "rt_ordered", "toysi_ttn": "2045", "rt_confirmed_at": "2026-11-10T09:00:00"}, NOW)[:1] == ("rt_rt_ordered",) and oa.stage({**EVA, "rt_error": None, "rt_state": "rt_ordered", "rt_confirmed_at": "2026-11-10T09:00:00"}, NOW)[2] is False)
chk("rt_state ttn_creating/rt_ordering (claim, результат невідомий) → увага; ttn_created/marking_ready — ні", oa.stage({**EVA, "rt_error": None, "rt_state": "ttn_creating"}, NOW)[2] is True and oa.stage({**EVA, "rt_error": None, "rt_state": "ttn_created"}, NOW)[2] is False)
chk("delivered/cancelled/returned мають пріоритет над старим rt_error (історія не кричить)", oa.stage({**EVA, "delivery_status": "delivered"}, NOW)[0] == "delivered" and oa.stage({**EVA, "cancelled_at": "2026-11-10T23:30:00"}, NOW)[0] == "cancelled")
chk("оплачене, не передане ніким >1 год, без помилки → «нікуди не передано» + увага", oa.stage({**EVA, "rt_error": None}, NOW)[0] == "stalled" and oa.stage({**EVA, "rt_error": None}, NOW)[2] is True)
chk("оплачене, не передане <1 год → ще «прийнято» (не шумимо)", oa.stage({**EVA, "rt_error": None, "created_at": "2026-11-10T14:30:00"}, NOW)[0] == "accepted")
chk("давнє (>3 діб) непередане → підпис є, тривоги немає (історія)", oa.stage({**EVA, "rt_error": None, "created_at": "2026-11-01T10:00:00"}, NOW)[0] == "stalled" and oa.stage({**EVA, "rt_error": None, "created_at": "2026-11-01T10:00:00"}, NOW)[2] is False)
chk("передано в Toysi — не «stalled»", oa.stage({**EVA, "rt_error": None, "forwarded_to_toysi_at": "2026-11-10T23:10:00"}, NOW)[0] == "forwarded")
sm = oa.summarize([EVA, {**EVA, "order_id": "ok", "rt_error": None, "rt_state": "rt_ordered", "rt_confirmed_at": "2026-11-10T09:00:00"}], NOW)
chk("summarize: attention=1 і рядок із причиною для банера панелі", sm["attention"] == 1 and sm["attention_rows"][0]["stage"] == "stuck" and "City not found" in sm["attention_rows"][0]["label"])

# ── 12.11: постачальник у панелі + очікування оплати з балансу + застарілий rt_error
def SP(*codes):
    return oa.supplier_of({"items": [{"code": c} for c in codes]})


chk("постачальник за кодами: rt- → RoyalToys, zb- → ZooBaza, число → Toysi; змішане через « + »; порожнє → ?",
    SP("rt-68654") == "RoyalToys" and SP("zb-5") == "ZooBaza" and SP("308212") == "Toysi" and SP("100", "rt-1") == "Toysi + RoyalToys" and SP() == "?" and SP("RT-9") == "RoyalToys")
chk("payload несе supplier для рядків", oa.payload(7, runner=lambda *a, **k: type("R", (), {"returncode": 0, "stdout": json.dumps({"generated": "x", "days": 7, "rows": [{"platform": "eva", "order_id": "1", "items": [{"code": "rt-1", "name": "n", "qty": 1, "price": 100}], "payment_method": "prepaid", "payment_confirmed": 1, "created_at": "2026-11-10T08:00:00"}]}).encode(), "stderr": b""})(), now=NOW)["rows"][0]["supplier"] == "RoyalToys")
RO = {**EVA, "rt_error": None, "rt_state": "rt_ordered", "created_at": "2026-11-10T10:00:00", "items": [{"code": "rt-68654", "name": "n", "qty": 1, "price": 100}]}
c2, l2, a2 = oa.stage(RO, NOW)
chk("rt_ordered без rt_confirmed_at, >1 год → «ЧЕКАЄ ОПЛАТИ З БАЛАНСУ» + увага", c2 == "rt_wait_pay" and "БАЛАНСУ" in l2 and a2 is True)
chk("rt_ordered без rt_confirmed_at, <1 год → без тривоги", oa.stage({**RO, "created_at": "2026-11-10T14:30:00"}, NOW)[2] is False)
chk("RoyalToys у rt_ordered без rt_confirmed_at: supplier визначається за rt- позиціями", oa.stage({**RO, "items": [{"code": "rt-68654"}]}, NOW)[0] == "rt_wait_pay")
chk("rt_ordered із rt_confirmed_at → звичайний стан «замовлено у постачальника», без тривоги", oa.stage({**RO, "rt_confirmed_at": "2026-11-10T11:00:00"}, NOW)[:1] == ("rt_rt_ordered",) and oa.stage({**RO, "rt_confirmed_at": "2026-11-10T11:00:00"}, NOW)[2] is False)
chk("застарілий rt_error після підтвердження (rt_confirmed_at) НЕ показується як «ЗУПИНЕНО»", oa.stage({**RO, "rt_error": "підтвердження не вдалось: статус невідомий", "rt_confirmed_at": "2026-11-10T11:00:00"}, NOW)[0] != "stuck")
chk("недостатній баланс (rt_error без rt_confirmed_at) → «ЗУПИНЕНО … недостатньо балансу» + увага", oa.stage({**RO, "rt_error": "недостатньо балансу RoyalToys: потрібно 72 — поповніть баланс, конвеєр підтвердить сам"}, NOW)[0] == "stuck" and oa.stage({**RO, "rt_error": "недостатньо балансу RoyalToys: потрібно 72"}, NOW)[2] is True)

# ── total
chk("сума: site_charged_total має пріоритет", oa.total({"site_charged_total": 84, "items": [{"price": 1, "qty": 1}]}) == 84.0)
chk("сума: з позицій, сміття не ламає", oa.total({"items": [{"price": 100, "qty": 2}, {"price": "x", "qty": 1}, {}]}) == 200.0)

# ── summarize
rows = [dict(platform="site", status="awaiting_manual_confirmation", payment_method="prepaid", payment_confirmed=0, created_at="2026-11-10T07:27:15", items=[]),
        dict(platform="site", status="forwarded_to_supplier", payment_method="cod", payment_confirmed=0, created_at="2026-11-09T21:06:29", forwarded_to_toysi_at="2026-11-09T21:09:00", items=[]),
        dict(platform="prom", status="new", payment_method="cod", payment_confirmed=0, created_at="2026-11-10T10:00:00", items=[])]
sm = oa.summarize(rows, NOW)
chk("лічильники: всього 3, site 2, prom 1, сьогодні site 1 / prom 1, неоплачених сайту 1 (зависло 1)",
    sm["total"] == 3 and sm["by_platform"] == {"site": 2, "prom": 1} and sm["today"] == {"site": 1, "prom": 1} and sm["site_unpaid"] == 1 and sm["site_unpaid_stale"] == 1)

# ── віддалений скрипт на справжній схемі
orders_db.init_db()
c = sqlite3.connect(os.environ["ORDERS_DB_PATH"])
def ins(oid, plat, created, pay="prepaid", st="new", name="Іван Петренко", phone="380671112233", items=None, extra=None):
    items = items if items is not None else [{"toysi_code": "57114", "name": "Іграшка для котів", "qty": 1, "price": 199}]
    c.execute("insert into orders (internal_order_id, order_id, platform, status, payment_method, payment_confirmed, customer_name, phone, items, created_at) values (?,?,?,?,?,?,?,?,?,?)",
              (f"{plat}_{oid}", oid, plat, st, pay, 0, name, phone, json.dumps(items, ensure_ascii=False), created))
# дати ВІДНОСНО «зараз»: віддалений скрипт фільтрує за реальним datetime.now() (вікно 7 діб) — фіксовані дати гнили (17.11.2026 «09.11» вийшло за вікно)
_ts = lambda days: (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
ins("PT-1", "site", _ts(2), st="awaiting_manual_confirmation")
ins("PT-0", "site", "2026-09-01T07:27:15")                      # старе — поза вікном 7 днів
ins("414", "prom", _ts(3), pay="cod", items=[{"toysi_code": "1", "name": "X" * 300, "qty": 2, "price": 10}] * 40)
c.commit(); c.close()
wd = TMP
res = subprocess.run([sys.executable, "-", "7"], input=oa.REMOTE_SCRIPT.encode("ascii"), cwd=wd, capture_output=True, timeout=60)
chk("віддалений скрипт: код 0 і чистий JSON в останньому рядку", res.returncode == 0)
data = json.loads(res.stdout.decode("utf-8").strip().splitlines()[-1]) if res.returncode == 0 else {"rows": []}
ids = [r["internal_order_id"] for r in data["rows"]]
chk("фільтр днів: 2 замовлення за 7 днів, старе (вересень) не потрапило; новіші першими", ids == ["site_PT-1", "prom_414"])
chk("рядок несе ПІБ/телефон (для локальної сторінки) і розібрані позиції; поле items не сире", data["rows"][0]["customer_name"] == "Іван Петренко" and data["rows"][0]["items"][0]["name"] == "Іграшка для котів" and isinstance(data["rows"][0]["items"], list))
chk("позицій ≤30, назва ≤100 символів (захист від роздутої відповіді)", len(data["rows"][1]["items"]) == 30 and len(data["rows"][1]["items"][0]["name"]) == 100)
chk("скрипт відкриває базу лише на читання (mode=ro) і ASCII", "mode=ro" in oa.REMOTE_SCRIPT and oa.REMOTE_SCRIPT.isascii())
ro = subprocess.run([sys.executable, "-c", "import sqlite3;c=sqlite3.connect('file:orders.db?mode=ro',uri=True);\ntry:\n c.execute(\"update orders set status='x'\");print('WROTE')\nexcept Exception as e:\n print('RO')"], cwd=wd, capture_output=True)
chk("контроль: у режимі mode=ro запис неможливий", ro.stdout.decode().strip() == "RO")

# ── fetch з підміненим ssh
class R:
    def __init__(self, rc=0, out=b"", err=b""):
        self.returncode, self.stdout, self.stderr = rc, out, err

seen = {}
def runner_ok(cmd, **kw):
    seen["cmd"], seen["input"] = cmd, kw.get("input")
    return R(0, json.dumps({"generated": "2026-11-10T15:00:00", "days": 7, "rows": [{"platform": "site", "created_at": "2026-11-10T07:27:15", "status": "x", "payment_method": "prepaid", "payment_confirmed": 0, "items": []}]}).encode())
ok = oa.payload("7; rm -rf /", runner=runner_ok, now=NOW)
chk("fetch: успіх, ін'єкція у days неможлива (шлях до числа 30), BatchMode", ok.get("ok") and seen["cmd"][-1].endswith("python3 - 30") and "BatchMode=yes" in seen["cmd"])
chk("payload: стан і сума обчислені, лічильники є", ok["rows"][0]["stage"] == "unpaid" and ok["summary"]["site_unpaid"] == 1)
chk("fetch: ssh код 255 → ok False з текстом", oa.fetch(7, runner=lambda c, **k: R(255, b"", b"Permission denied"))["ok"] is False)
def to(cmd, **kw): raise subprocess.TimeoutExpired(cmd, 1)
chk("fetch: таймаут → ok False", oa.fetch(7, runner=to)["ok"] is False)
chk("fetch: не-JSON → ok False", oa.fetch(7, runner=lambda c, **k: R(0, b"garbage"))["ok"] is False)

# ── маршрут панелі
import control_panel as cp  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402
orig = oa.fetch
oa.fetch = lambda days=30, runner=None: json.loads(json.dumps({"ok": True, "generated": "G", "days": days, "rows": []}))
srv = ThreadingHTTPServer(("127.0.0.1", 0), cp.Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
def post(headers):
    h = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h.request("POST", "/api/orders", body=json.dumps({"days": 7}), headers=headers)
    r = h.getresponse(); return r.status, r.read().decode("utf-8")
st, _ = post({"Content-Type": "application/json"})
chk("панель: POST /api/orders без X-Panel → 403 (CSRF)", st == 403)
st, body = post({"Content-Type": "application/json", "X-Panel": "1"})
chk("панель: з X-Panel → 200 і дані", st == 200 and json.loads(body).get("ok") is True)
h = http.client.HTTPConnection("127.0.0.1", port, timeout=10); h.request("GET", "/"); page = h.getresponse().read().decode("utf-8")
chk("сторінка панелі містить блок «Замовлення» і loadOrders", "loadOrders" in page and "id=otab" in page)
# ── статус НП (17.11.2026, власник: «є замовлення ще на НП не забрані, панель їх більше не показує»)
NOW2 = datetime(2026, 11, 17, 18, 0, 0)


def SN(now=NOW2, **kw):
    base = {"platform": "prom", "status": "forwarded_to_supplier", "payment_method": "prepaid", "payment_confirmed": 1, "created_at": "2026-11-10T10:00:00", "forwarded_to_toysi_at": "2026-11-10T10:05:00",
            "delivery_status": "shipped", "toysi_ttn": "20451580000001"}
    base.update(kw)
    return oa.stage(base, now)


c1 = SN(np_status="Прибув у відділення", np_code="7", np_arrived="2026-11-16 12:00:00")
chk("відправлено + НП «Прибув у відділення» 1,25 дн. → у підписі статус НП і дні, без тривоги", "НП: Прибув у відділення (1.2 дн.)" in c1[1] and c1[2] is False and c1[0] == "shipped")
c2 = SN(np_status="Прибув у відділення", np_code="7", np_arrived="2026-11-13 12:00:00")
chk("≥4 доби на відділенні → тривога «НЕ ЗАБРАНО, скоро повернення» (безкоштовне зберігання 5 діб)", c2[2] is True and "НЕ ЗАБРАНО" in c2[1])
c3 = SN(np_status="Відправлення отримано", np_code="9")
chk("НП каже «отримано», а наш delivery_status застряг на shipped → доставлено (за статусом НП), без тривоги", c3[0] == "delivered" and c3[2] is False)
c4 = SN(np_status="Відправлення отримано. Грошовий переказ видано одержувачу.", np_code="11", status="x")
chk("«отримано» з грошовим переказом (код 11) теж доставлено", c4[0] == "delivered")
c5 = SN(np_status="В дорозі", np_code="4")
chk("в дорозі (код 4): статус НП у підписі, без тривоги й без «дн.»", "НП: В дорозі" in c5[1] and c5[2] is False and "дн.)" not in c5[1])
chk("нема даних НП → підпис і тривога як раніше (зворотна сумісність)", SN()[1].startswith("відправлено") and "НП:" not in SN()[1] and SN()[2] is False)
# скасовані з посилкою
k1 = SN(delivery_status="cancelled", cancelled_at="2026-11-16T19:53:24", cancel_reason="EVA статус 9", np_status="Прибув у відділення", np_code="7", np_arrived="2026-11-17 15:54:10")
chk("СКАСОВАНЕ, але посилка ще на НП → тривога «⚠ посилка ще в НП»", k1[0] == "cancelled" and k1[2] is True and "посилка ще в НП" in k1[1])
k2 = SN(delivery_status="cancelled", cancelled_at="2026-11-16T19:53:24", np_status="Відправлення отримано", np_code="9")
chk("СКАСОВАНЕ, а посилку ОТРИМАНО → тривога «ПОСИЛКУ ОТРИМАНО … перевір гроші» (клас інциденту EVA 8-084428382)", k2[2] is True and "ОТРИМАНО" in k2[1])
k3 = SN(delivery_status="cancelled", cancelled_at="2026-11-16T19:53:24", np_status="Відправлення повертається", np_code="102")
chk("скасоване, посилка повертається — лише інформація, без тривоги", k3[2] is False and "НП: Відправлення повертається" in k3[1])
k4 = SN(delivery_status="cancelled", cancelled_at="2026-11-16T19:53:24")
chk("скасоване без даних НП — як раніше (без тривоги)", k4[2] is False)

# payload: carry-over, підтвердження власника
def fake_fetch(days=30, runner=None):
    base = {"platform": "eva", "status": "cancelled", "payment_method": "prepaid", "payment_confirmed": 1, "created_at": "2026-11-13T23:54:03", "cancelled_at": "2026-11-16T19:53:24", "delivery_status": "cancelled",
            "toysi_ttn": "20451588716656", "np_status": "Прибув у відділення", "np_code": "7", "np_arrived": "2026-11-17 15:54:10", "items": [{"code": "1", "name": "n", "qty": 1, "price": 668}]}
    old_ship = {"platform": "prom", "internal_order_id": "prom_1", "order_id": "1", "status": "forwarded_to_supplier", "payment_method": "prepaid", "payment_confirmed": 1, "created_at": "2026-11-05T10:00:00", "forwarded_to_toysi_at": "2026-11-05T10:05:00",
                "delivery_status": "shipped", "toysi_ttn": "2", "carry": True, "np_status": "Прибув у відділення", "np_code": "7", "np_arrived": "2026-11-13 10:00:00", "items": []}
    old_recv = dict(old_ship, internal_order_id="prom_2", order_id="2", toysi_ttn="3", np_status="Відправлення отримано", np_code="9")
    return {"ok": True, "generated": "2026-11-17T18:00:00", "days": days, "rows": [dict(base, internal_order_id="eva_8-1", order_id="8-1"), old_ship, old_recv]}


orig_fetch = oa.fetch
oa.fetch = fake_fetch
orig_ack = oa.ACK_FILE
with __import__("tempfile").TemporaryDirectory() as _td:
    oa.ACK_FILE = os.path.join(_td, "none.json")
    pl = oa.payload(7, now=NOW2)
    ids = {r["internal_order_id"]: r for r in pl["rows"]}
    chk("carry: стара відправлена посилка, що ще на НП, лишається в огляді з міткою carry і тривогою; стара вже ОТРИМАНА — відкидається", "prom_1" in ids and ids["prom_1"]["carry"] and ids["prom_1"]["attention"] and "prom_2" not in ids)
    chk("скасоване з посилкою на НП без підтвердження → у summary.attention", pl["summary"]["attention"] == 2 and ids["eva_8-1"]["attention"] is True)
    ackp = os.path.join(_td, "ack.json")
    open(ackp, "w", encoding="utf-8").write(json.dumps({"eva_8-1": "клієнт оплатив"}))
    oa.ACK_FILE = ackp
    pl2 = oa.payload(7, now=NOW2)
    e = {r["internal_order_id"]: r for r in pl2["rows"]}["eva_8-1"]
    chk("підтверджено власником (panel_ack.json) → без тривоги, нотатка в підписі; інші тривоги лишаються", e["attention"] is False and "підтверджено власником: клієнт оплатив" in e["stage_label"] and pl2["summary"]["attention"] == 1)
    oa.ACK_FILE = os.path.join(_td, "broken.json")
    open(oa.ACK_FILE, "w", encoding="utf-8").write("{не json")
    chk("битий panel_ack.json → порожньо, не падаємо", oa.load_ack() == {})
oa.ACK_FILE = orig_ack
oa.fetch = orig_fetch
chk("REMOTE_SCRIPT лишається ASCII (кирилиця через stdin ssh ламається) і містить carry + НП-статус", all(ord(ch) < 128 for ch in oa.REMOTE_SCRIPT) and "carry" in oa.REMOTE_SCRIPT and "getStatusDocuments" in oa.REMOTE_SCRIPT)
srv.shutdown(); oa.fetch = orig
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
