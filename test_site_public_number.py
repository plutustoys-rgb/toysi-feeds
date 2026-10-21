#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_public_number.py — короткий номер замовлення для ПОКУПЦЯ «PT-123456» (власник 21.10.2026: «літери та 6 цифр»; довгий PT-YYYYMMDD-HHMMSS-hex лишається внутрішнім ключем).
Інваріанти: (1) assign_public_number: формат PT-[1-9]NNNNN, ідемпотентно, колізія → інше число, вичерпання → None, UNIQUE-індекс; get_internal_id_by_public — лише валідний формат і лише сайт;
(2) /api/order: у відповіді public_number, у БД записано, два замовлення мають різні; сповіщення власнику містить «№ для клієнта»; опис LiqPay і result_url (thanks.html?o=<довгий>&n=<короткий>) використовують короткий лише для показу;
(3) бот: «PT-123456 + 4 цифри телефону» знаходить замовлення, відповідь і підписка показують короткий номер, довгий (старий) формат і /start-payload працюють; невідомий короткий номер = та сама відповідь «не знайдено»;
(4) стара БД без колонки не ламає бот."""
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
os.environ["LIQPAY_PUBLIC_KEY"] = "sandbox_i0000000000"
os.environ["LIQPAY_PRIVATE_KEY"] = "sandbox_testprivatekey"
os.environ["SITE_CHAT_DB"] = os.path.join(tempfile.mkdtemp(), "chat.db")
os.environ.pop("SITE_CHAT_BOT_TOKEN", None)
sys.path.insert(0, str(Path(__file__).parent))
import orders_db  # noqa: E402
import site_order_api as api  # noqa: E402
import site_chat_bot as sb  # noqa: E402
import base64  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


orders_db.init_db()


def mk(oid, platform="site", phone="380671112233"):
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": platform, "payment_method": "prepaid", "payment_confirmed": 0, "customer_name": "Іван Петренко", "phone": phone, "email": "a@b.c",
                                   "np_branch": "Київ, Відділення №5", "np_city_ref": "C", "np_warehouse_number": "5", "carrier": "nova_poshta",
                                   "items": [{"toysi_code": "1", "name": "Іграшка", "qty": 1, "price": 100}]})
        c.commit()
    return f"{platform}_{oid}"


# ── 1) assign_public_number
i1 = mk("PT-20261021-100000-aaaaaa")
i2 = mk("PT-20261021-100001-bbbbbb")
i3 = mk("PT-20261021-100002-cccccc")
with orders_db.get_connection() as c:
    n1 = orders_db.assign_public_number(c, i1)
    n1b = orders_db.assign_public_number(c, i1)
    n2 = orders_db.assign_public_number(c, i2, rand=iter([int(n1[3:]), int(n1[3:]), 654321]).__next__)
    n_none = orders_db.assign_public_number(c, "site_нема")
    c.commit()
chk("формат PT-[1-9]NNNNN (6 цифр, перша не нуль), ідемпотентно (другий виклик той самий)", bool(re.fullmatch(r"PT-[1-9]\d{5}", n1)) and n1 == n1b)
chk("колізія (rand двічі дає вже зайнятий номер) → береться інший (654321); номери різні", n2 == "PT-654321" and n2 != n1)
chk("нема такого замовлення → None", n_none is None)
with orders_db.get_connection() as c:
    allbusy = orders_db.assign_public_number(c, i3, rand=lambda: int(n1[3:]), tries=5)
    try:
        c.execute("UPDATE orders SET public_number=? WHERE internal_order_id=?", (n1, i3))
        dup_ok = True
    except sqlite3.IntegrityError:
        dup_ok = False
chk("усі спроби — колізії → None (замовлення лишається з довгим номером); UNIQUE-індекс не пускає дубль", allbusy is None and dup_ok is False)
with orders_db.get_connection() as c:
    chk("get_internal_id_by_public: валідний → internal id; невідомий/кривий формат/не рядок → None", orders_db.get_internal_id_by_public(c, n1) == i1 and orders_db.get_internal_id_by_public(c, "PT-111111") is None
        and all(orders_db.get_internal_id_by_public(c, x) is None for x in ("PT-12345", "PT-0123456", "PT-1234567", "pt-654321", "654321", None, "PT-6543 21")))
    eid = mk("E-1", platform="eva")
    c.execute("UPDATE orders SET public_number='PT-777777' WHERE internal_order_id=?", (eid,))
    c.commit()
    chk("лише ПЛАТФОРМА САЙТУ: eva/prom з public_number не повертаються", orders_db.get_internal_id_by_public(c, "PT-777777") is None)

# ── 2) /api/order
api.price_map = lambda: {"1": {"name": "Тестовий товар", "price": 100}}
api.SITE_COD_PHONE_DAILY_LIMIT = 100
posts = []
api._NO_TELEGRAM = False
import telegram_notify as tn  # noqa: E402
tn.TELEGRAM_BOT_TOKEN, tn.TELEGRAM_CHAT_ID = "T", "C"
tn.ALERTS_LOG_FILE = Path(tempfile.mkdtemp()) / "alerts.md"
tn.ALERT_THROTTLE_FILE = tn.ALERTS_LOG_FILE.parent / ".t.json"


class _R:
    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True}


tn.requests.post = lambda url, json=None, timeout=0: (posts.append(json["text"]) or _R())
os.environ.pop("TELEGRAM_ORDERS_ONLY", None)


def post_order(pm, phone):
    out = []
    h = api.Handler.__new__(api.Handler)
    h.command, h.path = "POST", "/api/order"
    h._read_json = lambda: {"items": [{"id": "1", "qty": 1}], "name": "Іван Петренко", "phone": phone, "city_name": "Київ", "warehouse_name": "Відділення №1", "np_warehouse_number": "1", "payment_method": pm}
    h._json = lambda code, obj: out.append((code, obj))
    h._handle_order()
    return out[0]


posts.clear()
code, r1 = post_order("cod", "+380671112201")
code2, r2 = post_order("prepaid", "+380671112202")
chk("відповідь /api/order містить public_number PT-NNNNNN (COD і передоплата), довгий order_id теж", code == 200 and code2 == 200 and re.fullmatch(r"PT-[1-9]\d{5}", r1.get("public_number", "")) and re.fullmatch(r"PT-[1-9]\d{5}", r2.get("public_number", ""))
    and r1["public_number"] != r2["public_number"] and r1["order_id"].startswith("PT-20"))
with orders_db.get_connection() as c:
    row = orders_db.get_order(c, f"site_{r1['order_id']}")
chk("у БД public_number записано і збігається з відповіддю", row["public_number"] == r1["public_number"])
chk("сповіщення власнику (COD) містить «№ для клієнта PT-NNNNNN»", any(f"№ для клієнта {r1['public_number']}" in p for p in posts))
lq = r2.get("liqpay") or {}
desc = json.loads(base64.b64decode(lq["data"]).decode("utf-8")) if lq.get("data") else {}
chk("LiqPay: опис платежу = «Замовлення <короткий номер>», order_id для LiqPay — довгий (ключ колбека)", r2["public_number"] in desc.get("description", "") and desc.get("order_id") == r2["order_id"]
    and f"thanks.html?o={r2['order_id']}&n={r2['public_number']}" in desc.get("result_url", ""))
chk("thanks_url: &n= лише для валідного короткого; для кривого/без — як раніше; кривий o — без параметрів", api.thanks_url("PT-20261021-100000-aaaaaa", "PT-123456").endswith("?o=PT-20261021-100000-aaaaaa&n=PT-123456")
    and api.thanks_url("PT-20261021-100000-aaaaaa", "x").endswith("?o=PT-20261021-100000-aaaaaa") and api.thanks_url("PT-20261021-100000-aaaaaa").endswith("?o=PT-20261021-100000-aaaaaa") and api.thanks_url("evil").endswith("/thanks.html"))

# ── 3) бот
class FakeAPI:
    def __init__(self):
        self.sent = []

    def call(self, method, **p):
        if method == "sendMessage":
            self.sent.append((p["chat_id"], p["text"]))
            return {"message_id": 1}
        return []


sb.ORDERS_DB = Path(os.environ["ORDERS_DB_PATH"])
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET status='forwarded_to_supplier', delivery_status='shipped', toysi_ttn='20451565972503', payment_confirmed=1 WHERE internal_order_id=?", (f"site_{r2['order_id']}",))
    c.commit()
now = datetime(2026, 10, 21, 18, 0, 0)


def newbot():
    a = FakeAPI()
    return sb.Bot(a, sb.ChatDB(os.path.join(tempfile.mkdtemp(), "c.db")), 111, now_fn=lambda: now), a


def upd(chat, text, uid=1):
    return {"update_id": uid, "message": {"chat": {"id": chat, "type": "private"}, "from": {"first_name": "О", "username": "o"}, "text": text}}


chk("order_status_request: «PT-123456 4567» → ('PT-123456', {'4567'}); довгий формат працює як раніше; сміття → None",
    sb.order_status_request("статус PT-123456 4567") == ("PT-123456", {"4567"}) and sb.order_status_request("PT-20261021-100000-AAAAAA тел 4567") == ("PT-20261021-100000-aaaaaa", {"4567"})
    and sb.order_status_request("PT-12345 4567") is None and sb.order_status_request("PT-1234567 4567") is None and sb.order_status_request("PT-20261021 4567") is None)
b, a = newbot()
last4 = "112202"[-4:]
b.handle_update(upd(701, f"Статус {r2['public_number']} телефон {last4}"))
txt = [t for c, t in a.sent if c == 701]
chk("бот: короткий номер + 4 цифри телефону → статус «відправлено» з ТТН; у тексті ТІЛЬКИ короткий номер (довгий не показується)", len(txt) == 1 and "відправлено" in txt[0] and "20451565972503" in txt[0] and txt[0].startswith(f"Замовлення {r2['public_number']}:") and r2["order_id"] not in txt[0])
b2, a2 = newbot()
b2.handle_update(upd(702, f"Статус {r2['order_id']} телефон {last4}"))
t2 = [t for c, t in a2.sent if c == 702]
chk("бот: довгий (старий) формат + 4 цифри працює як раніше, але у відповіді — вже короткий номер", len(t2) == 1 and t2[0].startswith(f"Замовлення {r2['public_number']}:"))
b3, a3 = newbot()
b3.handle_update(upd(703, f"Статус {r2['public_number']} телефон 0000"))
b3.handle_update(upd(703, "Статус PT-999999 телефон 4567", uid=2))
t3 = [t for c, t in a3.sent if c == 703]
chk("хибні 4 цифри і неіснуючий короткий номер: ОДНАКОВА відповідь «не знайдено» (існування не розкривається)", len(t3) == 2 and t3[0] == t3[1] == sb.STATUS_NOT_FOUND)
b4, a4 = newbot()
b4.handle_update(upd(704, f"/start {r2['order_id']}"))
t4 = [t for c, t in a4.sent if c == 704]
chk("/start з довгим ключем (кнопка на thanks): підписка працює, у привітанні короткий номер", t4 and r2["public_number"] in t4[0] and r2["order_id"] not in t4[0])
b4.db.run("UPDATE subscriptions SET last_text='' WHERE 1=1")
b4.notify_changes()
t4b = [t for c, t in a4.sent if c == 704]
chk("розсилка зміни стану підписникам — з коротким номером", any(x.startswith(f"Замовлення {r2['public_number']}:") for x in t4b))
dig = sb.digest(b.db, 1, now)
chk("digest: для короткого номера 4 цифри телефону маскуються", "[4 цифри]" in dig and last4 not in dig)

# ── 4) стара БД без колонки
old = os.path.join(tempfile.mkdtemp(), "old.db")
oc = sqlite3.connect(old)
oc.execute("CREATE TABLE orders (internal_order_id TEXT, platform TEXT, status TEXT, delivery_status TEXT, payment_method TEXT, payment_confirmed INTEGER, toysi_ttn TEXT, cancelled_at TEXT, phone TEXT)")
oc.execute("INSERT INTO orders VALUES('site_PT-20261021-100000-aaaaaa','site','forwarded_to_supplier','shipped','prepaid',1,'20451565972503',NULL,'380671112233')")
oc.commit()
oc.close()
sb.ORDERS_DB = Path(old)
b5, a5 = newbot()
b5.handle_update(upd(705, "Статус PT-123456 телефон 2233"))
b5.handle_update(upd(705, "Статус PT-20261021-100000-aaaaaa телефон 2233", uid=2))
t5 = [t for c, t in a5.sent if c == 705]
chk("стара БД без public_number: короткий → «не знайдено» без падіння; довгий → статус, у тексті довгий номер", len(t5) == 2 and t5[0] == sb.STATUS_NOT_FOUND and t5[1].startswith("Замовлення PT-20261021-100000-aaaaaa:"))


# --- аудит #955: B1 (гонка rowcount), B2 (збій БД не валить), A1 (індекс після міграцій), C1 (ліміт за internal id) ---
i4 = mk("PT-20261021-100003-dddddd")
with orders_db.get_connection() as c:
    def _race():
        c.execute("UPDATE orders SET public_number='PT-888888' WHERE internal_order_id=?", (i4,))     # «інший процес» призначив номер між SELECT і UPDATE
        return 123457
    got = orders_db.assign_public_number(c, i4, rand=_race)
    in_db = c.execute("SELECT public_number FROM orders WHERE internal_order_id=?", (i4,)).fetchone()[0]
chk("B1: гонка — номер уже призначено між SELECT і UPDATE → повертається те, що реально в БД (PT-888888), а не вигаданий", got == "PT-888888" == in_db)


class _Boom:
    def execute(self, sql, *a):
        if sql.lstrip().upper().startswith("SELECT"):
            class _R:
                def fetchone(self_inner):
                    return (None,)
            return _R()
        raise sqlite3.OperationalError("database is locked")


chk("B2: «database is locked» (будь-який sqlite3.Error, окрім IntegrityError) → None, замовлення не валиться", orders_db.assign_public_number(_Boom(), "site_X") is None)
import inspect  # noqa: E402
_src = inspect.getsource(orders_db.init_db)
chk("A1: CREATE UNIQUE INDEX idx_orders_public_number стоїть ПІСЛЯ rebuild-міграцій у init_db", _src.index("idx_orders_public_number") > _src.index("_migrate_carrier_allow_rozetka_delivery(conn)"))
with orders_db.get_connection() as c:
    idxs = {r[1] for r in c.execute("PRAGMA index_list(orders)")}
chk("A1: індекс існує після init_db", "idx_orders_public_number" in idxs)
# C1: короткий і довгий формат одного замовлення діляться лімітом невдалих звірок
sb.ORDERS_DB = Path(os.environ["ORDERS_DB_PATH"])
ci = mk("PT-20261021-100005-eeeeee", phone="380671119999")
with orders_db.get_connection() as c:
    pn_ci = orders_db.assign_public_number(c, ci, rand=lambda: 246810)
    c.commit()
bz, az = newbot()
for i, txt in enumerate((f"Статус {pn_ci} телефон 0001", f"Статус {pn_ci} телефон 0002", f"Статус PT-20261021-100005-eeeeee телефон 0003", f"Статус PT-20261021-100005-eeeeee телефон 0004", f"Статус {pn_ci} телефон 0005")):
    bz.handle_update(upd(790, txt, uid=i + 1))
bz.handle_update(upd(790, f"Статус {pn_ci} телефон 9999", uid=10))        # 6-а спроба (навіть із правильними цифрами — вимкнено)
tz = [t for c, t in az.sent if c == 790]
chk("C1: ліміт невдалих звірок ключується за internal id: 5 хибних спроб (3 за коротким + 2 за довгим номером) → 6-а (навіть із правильними цифрами) статусом НЕ відповідає — лише загальне «повідомлення отримано» (піде людині)",
    len(tz) == 6 and all(t == sb.STATUS_NOT_FOUND for t in tz[:5]) and "повідомлення отримано" in tz[5] and "відправлено" not in tz[5] and "20451565972503" not in tz[5])
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
