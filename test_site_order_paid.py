#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_order_paid.py — GET /api/order/paid (21.10.2026): серверне підтвердження ОПЛАТИ для GA4 purchase / конверсії Google Ads за передоплатою (SEO/власник).
Інваріанти: (1) paid=true ЛИШЕ для сайтового ПЕРЕДПЛАЧЕНОГО замовлення з payment_confirmed=1, не sandbox_test, не скасованого; COD, неоплачене, чуже (eva/prom), порожні позиції, сума ≤0 → paid=false;
(2) сума = site_charged_total (реально списана), без нього — сума позицій; (3) у відповіді НЕМАЄ ПІБ/телефону/email/адреси; (4) некоректний/невідомий номер → ті самі {"paid": false} (існування не розкриваємо);
(5) ліміт запитів на IP (429); (6) Cache-Control: no-store; (7) реальний HTTP-сервер на тимчасовій БД: замовлення створено/оплачено через orders_db, колбек-стан відображається."""
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
sys.path.insert(0, str(Path(__file__).parent))
import orders_db  # noqa: E402
import site_order_api as api  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


P = api.paid_purchase_payload
base = {"platform": "site", "payment_method": "prepaid", "payment_confirmed": 1, "status": "new", "cancelled_at": None, "site_charged_total": 936,
        "items": [{"toysi_code": "235193", "name": "Картина", "qty": 2, "price": 468}]}
r = P(base)
chk("paid=true для оплаченого передплатного: сума = site_charged_total, UAH, позиції id/name/price/qty", r == {"paid": True, "currency": "UAH", "value": 936.0, "items": [{"id": "235193", "name": "Картина", "price": 468.0, "qty": 2}]})
chk("сума — зафіксована списана (936), а не сума позицій, коли вони різні (знижка/промокод)", P({**base, "site_charged_total": 900})["value"] == 900.0)
chk("давнє замовлення без site_charged_total → сума позицій", P({**base, "site_charged_total": None})["value"] == 936.0)
bad = {"COD": {"payment_method": "cod"}, "неоплачене": {"payment_confirmed": 0}, "sandbox_test": {"status": "sandbox_test"}, "скасоване (status)": {"status": "cancelled"}, "скасоване (cancelled_at)": {"cancelled_at": "2026-10-21T10:00:00"},
       "EVA": {"platform": "eva"}, "Prom": {"platform": "prom"}, "порожні позиції": {"items": []}, "сума 0": {"site_charged_total": 0, "items": [{"toysi_code": "1", "name": "x", "qty": 1, "price": 0}]},
       "позиція без toysi_code": {"items": [{"name": "x", "qty": 1, "price": 5}]}, "кривий qty": {"items": [{"toysi_code": "1", "name": "x", "qty": "abc", "price": 5}]}}
for name, patch in bad.items():
    chk(f"paid=false: {name}", P({**base, **patch}) == {"paid": False})
chk("paid=false: замовлення нема (None) / порожній dict", P(None) == {"paid": False} and P({}) == {"paid": False})
chk("у відповіді нема ПІБ/телефону/email/адреси, навіть якщо вони є в замовленні", "customer_name" not in json.dumps(P({**base, "customer_name": "Іван Петренко", "phone": "380671112233", "email": "a@b.c", "np_branch": "Київ"}), ensure_ascii=False)
    and "380671112233" not in json.dumps(P({**base, "phone": "380671112233"})) and set(P(base).keys()) == {"paid", "currency", "value", "items"})

rl = api.RateLimiter(3, 60)
chk("RateLimiter: 3 запити пройшли, 4-й заблоковано; інший ключ окремо; після вікна знову можна", [rl.allow("a", 100.0), rl.allow("a", 101.0), rl.allow("a", 102.0), rl.allow("a", 103.0)] == [True, True, True, False]
    and rl.allow("b", 103.0) is True and rl.allow("a", 161.0) is True)
rl2 = api.RateLimiter(1, 60, max_keys=5)
for i in range(20):
    rl2.allow(f"k{i}", 1000.0 + i)
chk("RateLimiter: словник обмежений (max_keys), не росте безмежно", len(rl2._hits) <= 6)
chk("ORDER_ID_RE: приймає PT-YYYYMMDD-HHMMSS-<6 hex малими>, відхиляє решту (великі, довші, SQL, пробіли)", api.ORDER_ID_RE.fullmatch("PT-20261021-123456-0a1b2c") and not any(api.ORDER_ID_RE.fullmatch(x) for x in
    ("PT-20261021-123456-0A1B2C", "PT-20261021-123456-0a1b2c3", "PT-20261021-123456-0a1b2c ", "pt-20261021-123456-0a1b2c", "PT-1; DROP", "", "../etc")))

# ── HTTP-інтеграція на тимчасовій БД ──
orders_db.init_db()


def mk(oid, pm, paid, status=None, charged=500, items=None):
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": "site", "payment_method": pm, "payment_confirmed": 0, "customer_name": "Секретне Імя", "phone": "380671112233", "email": "secret@e.com",
                                   "np_branch": "Київ, Відділення №5", "np_city_ref": "C", "np_warehouse_number": "5", "carrier": "nova_poshta",
                                   "items": items or [{"toysi_code": "100", "name": "Іграшка", "qty": 1, "price": 500}]})
        c.execute("UPDATE orders SET payment_confirmed=?, status=COALESCE(?, status), site_charged_total=? WHERE internal_order_id=?", (paid, status, charged, f"site_{oid}"))
        c.commit()


O_PAID, O_UNPAID, O_COD, O_TEST = "PT-20261021-100000-aaaaaa", "PT-20261021-100001-bbbbbb", "PT-20261021-100002-cccccc", "PT-20261021-100003-dddddd"
mk(O_PAID, "prepaid", 1)
mk(O_UNPAID, "prepaid", 0)
mk(O_COD, "cod", 0)
mk(O_TEST, "prepaid", 1, status="sandbox_test")
api._PAID_LIMITER = api.RateLimiter(1000, 60)
srv = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()


def get(q, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/order/paid{q}", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), resp.headers.get("Cache-Control")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8")), e.headers.get("Cache-Control")


s, j, cc = get(f"?o={O_PAID}")
chk("HTTP: оплачене передплатне → 200 paid=true, value 500, позиції; Cache-Control: no-store; без ПІБ/телефону", s == 200 and j["paid"] is True and j["value"] == 500.0 and j["items"][0]["id"] == "100" and cc == "no-store"
    and "Секретне" not in json.dumps(j, ensure_ascii=False) and "380671112233" not in json.dumps(j))
chk("HTTP: неоплачене / COD / sandbox_test → paid=false", all(get(f"?o={o}")[1] == {"paid": False} for o in (O_UNPAID, O_COD, O_TEST)))
chk("HTTP: невідомий номер і некоректний формат → ТІ САМІ {paid:false} (існування не розкривається)", get("?o=PT-20261021-999999-ffffff")[1] == {"paid": False} and get("?o=abc")[1] == {"paid": False} and get("")[1] == {"paid": False})
api._PAID_LIMITER = api.RateLimiter(3, 60)
codes = [get(f"?o={O_PAID}", {"X-Forwarded-For": "1.2.3.4, 10.0.0.1"})[0] for _ in range(5)]
other = get(f"?o={O_PAID}", {"X-Forwarded-For": "5.6.7.8"})[0]
chk("HTTP: ліміт на IP (X-Forwarded-For): 3 пройшли, далі 429; інший IP — 200", codes == [200, 200, 200, 429, 429] and other == 200)
srv.shutdown()

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
