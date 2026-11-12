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
from datetime import datetime

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


chk("постачальник за кодами: rt- → RoyalToys, dropt- → Dropt, zb- → ZooBaza, число → Toysi; змішане через « + »; порожнє → ?",
    SP("rt-68654") == "RoyalToys" and SP("dropt-57114") == "Dropt" and SP("zb-5") == "ZooBaza" and SP("308212") == "Toysi" and SP("100", "rt-1") == "Toysi + RoyalToys" and SP() == "?" and SP("RT-9") == "RoyalToys")
chk("payload несе supplier для рядків", oa.payload(7, runner=lambda *a, **k: type("R", (), {"returncode": 0, "stdout": json.dumps({"generated": "x", "days": 7, "rows": [{"platform": "eva", "order_id": "1", "items": [{"code": "rt-1", "name": "n", "qty": 1, "price": 100}], "payment_method": "prepaid", "payment_confirmed": 1, "created_at": "2026-11-10T08:00:00"}]}).encode(), "stderr": b""})(), now=NOW)["rows"][0]["supplier"] == "RoyalToys")
RO = {**EVA, "rt_error": None, "rt_state": "rt_ordered", "created_at": "2026-11-10T10:00:00", "items": [{"code": "rt-68654", "name": "n", "qty": 1, "price": 100}]}
c2, l2, a2 = oa.stage(RO, NOW)
chk("rt_ordered без rt_confirmed_at, >1 год → «ЧЕКАЄ ОПЛАТИ З БАЛАНСУ» + увага", c2 == "rt_wait_pay" and "БАЛАНСУ" in l2 and a2 is True)
chk("rt_ordered без rt_confirmed_at, <1 год → без тривоги", oa.stage({**RO, "created_at": "2026-11-10T14:30:00"}, NOW)[2] is False)
DR = {**RO, "items": [{"code": "dropt-57114", "name": "n", "qty": 1, "price": 100}]}
chk("Dropt у rt_ordered без rt_confirmed_at (оплата з балансу на оформленні) → НЕ «чекає оплати», без тривоги (аудит #1244)", oa.stage(DR, NOW)[0] == "rt_rt_ordered" and oa.stage(DR, NOW)[2] is False)
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
    items = items if items is not None else [{"toysi_code": "dropt-57114", "name": "Іграшка для котів", "qty": 1, "price": 199}]
    c.execute("insert into orders (internal_order_id, order_id, platform, status, payment_method, payment_confirmed, customer_name, phone, items, created_at) values (?,?,?,?,?,?,?,?,?,?)",
              (f"{plat}_{oid}", oid, plat, st, pay, 0, name, phone, json.dumps(items, ensure_ascii=False), created))
ins("PT-1", "site", "2026-11-10T07:27:15", st="awaiting_manual_confirmation")
ins("PT-0", "site", "2026-09-01T07:27:15")                      # старе — поза вікном 7 днів
ins("414", "prom", "2026-11-09T12:00:00", pay="cod", items=[{"toysi_code": "1", "name": "X" * 300, "qty": 2, "price": 10}] * 40)
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
srv.shutdown(); oa.fetch = orig
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
