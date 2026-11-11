#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_order_attribution.py — звідки прийшло замовлення сайту (10.11.2026: SEO «UTM/gclid в orders.db не зберігаються», власник «усе всліпу»).
Перевіряє: білий список ключів, обмеження довжини, landing = лише шлях, referrer = лише хост, сміття не ламає; end-to-end: POST /api/order із attribution → колонка orders.attribution (COD і передоплата); без міток → NULL.
`python test_site_order_attribution.py` → exit 0/1."""
import json
import os
import sqlite3
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
os.environ["LIQPAY_PUBLIC_KEY"] = "sandbox_i0000000000"
os.environ["LIQPAY_PRIVATE_KEY"] = "sandbox_testprivatekey"
import site_order_api as api  # noqa: E402
import orders_db  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


c = api._clean_attribution
chk("нема/не dict → None", c(None) is None and c("x") is None and c([]) is None and c({}) is None)
r = json.loads(c({"gclid": "CjwKCAjw_abc-123", "utm_source": "google", "utm_medium": "cpc", "utm_campaign": "shopping_toys", "evil": "x", "utm_term": True, "utm_content": None}))
chk("білий список: лише відомі ключі; bool/None пропущено", set(r) == {"gclid", "utm_source", "utm_medium", "utm_campaign"} and r["gclid"] == "CjwKCAjw_abc-123")
r = json.loads(c({"gclid": "A" * 500}))
chk("довжина значення ≤ 200", len(r["gclid"]) == 200)
r = json.loads(c({"utm_source": "go\x00og\nle x"}))
chk("керуючі символи прибрано", all(ch not in r["utm_source"] for ch in "\x00\n "))
r = json.loads(c({"landing": "/product-y1.html?phone=380501112233&email=a@b.c#frag", "referrer": "https://www.google.com/search?q=іграшки+Київ"}))
chk("landing — лише шлях (без query/якоря/ПІБ), referrer — лише хост", r == {"landing": "/product-y1.html", "referrer": "www.google.com"})
chk("landing не з «/» → відкинуто; referrer без схеми → перший сегмент", "landing" not in json.loads(c({"landing": "https://x.com/a", "utm_source": "s"})) and json.loads(c({"referrer": "google.com/x?y"})) == {"referrer": "google.com"})
chk("referrer без схеми: query/якір/e-mail не потрапляють", json.loads(c({"referrer": "google.com?q=Ivan+0501234567", "utm_source": "s"})) == {"referrer": "google.com", "utm_source": "s"} and "referrer" not in json.loads(c({"referrer": "ivan@x.com", "utm_source": "s"})))
chk("сміттєві типи (список/dict усередині) не кидають і не потрапляють", json.loads(c({"gclid": ["a"], "utm_source": {"x": 1}, "utm_medium": 5}))  == {"utm_medium": "5"})

orders_db.init_db()
api.price_map = lambda: {"1": {"name": "Тестовий товар", "price": 100}}
api.SITE_COD_PHONE_DAILY_LIMIT = 100


def post(pm, phone, attribution="__none__"):
    out = []
    h = api.Handler.__new__(api.Handler)
    h.command, h.path = "POST", "/api/order"
    body = {"items": [{"id": "1", "qty": 1}], "name": "Іван Петренко", "phone": phone, "city_name": "Київ", "warehouse_name": "Відділення №1", "np_warehouse_number": "1", "payment_method": pm}
    if attribution != "__none__":
        body["attribution"] = attribution
    h._read_json = lambda: body
    h._json = lambda code, obj: out.append((code, obj))
    h._handle_order()
    return out[0]


def row(oid):
    cn = sqlite3.connect(os.environ["ORDERS_DB_PATH"])
    r = cn.execute("select attribution from orders where internal_order_id = ?", (f"site_{oid}",)).fetchone()
    cn.close()
    return r[0] if r else "NO ROW"


code, resp = post("cod", "+380671110001", {"gclid": "G1", "utm_source": "google", "utm_medium": "cpc", "landing": "/product-y1.html?x=1", "referrer": "https://www.google.com/"})
a = json.loads(row(resp["order_id"]) or "{}")
chk("COD із мітками: 200 і orders.attribution збережено (gclid, utm, landing без query, referrer-хост)", code == 200 and a == {"gclid": "G1", "landing": "/product-y1.html", "referrer": "www.google.com", "utm_medium": "cpc", "utm_source": "google"})
code, resp = post("prepaid", "+380671110002", {"fbclid": "FB9", "utm_source": "facebook"})
chk("передоплата із мітками: збережено", code == 200 and json.loads(row(resp["order_id"])) == {"fbclid": "FB9", "utm_source": "facebook"})
code, resp = post("cod", "+380671110003")
chk("без міток: замовлення прийнято, attribution = NULL (не вигадуємо «direct»)", code == 200 and row(resp["order_id"]) is None)
code, resp = post("cod", "+380671110004", "рядок замість dict")
chk("сміття в attribution не ламає прийом замовлення", code == 200 and row(resp["order_id"]) is None)
# змішаний кошик (Toysi + RT) → два замовлення, мітки на обох
os.environ["SITE_MIXED_ORDERS"] = "1"
api.price_map = lambda: {"1": {"name": "Тестовий товар", "price": 100}, "rt-7": {"name": "Тест RT", "price": 300}}
api.stock_snapshot = lambda: ({"1": 9, "rt-7": 9}, None)
api.liqpay_client.is_configured = lambda: True
api.liqpay_client.mode_problem = lambda: ""
api.liqpay_client.build_checkout = lambda **kw: {"action_url": "https://liqpay.test", "data": "d", "signature": "s", "sandbox": False}
out = []
h = api.Handler.__new__(api.Handler)
h.command, h.path = "POST", "/api/order"
h._read_json = lambda: {"items": [{"id": "1", "qty": 1}, {"id": "rt-7", "qty": 1}], "name": "Іван Петренко", "phone": "+380671110009", "city_name": "Київ",
                        "warehouse_name": "Відділення №1", "np_warehouse_number": "1", "payment_method": "prepaid", "attribution": {"gclid": "MX1", "utm_source": "google"}}
h._json = lambda code, obj: out.append((code, obj))
h._handle_order()
cn = sqlite3.connect(os.environ["ORDERS_DB_PATH"])
n_with = cn.execute("select count(*) from orders where attribution like '%MX1%'").fetchone()[0]
cn.close()
chk("змішаний кошик: 200 і мітки на ОБОХ дочірніх замовленнях", bool(out) and out[0][0] == 200 and n_with == 2)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
