#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_site_mixed_group.py — ЗМІШАНИЙ кошик сайту: зв'язок «платіж LiqPay → два замовлення» у БД (колонка orders.site_group_id, 23.10.2026) і те, що на ньому тримається:
  - HTTP-створення змішаного замовлення прописує site_group_id = батьківський номер в ОБОХ дочірніх; get_group_children повертає їх у порядку створення (Toysi, RT);
  - mixed_children_rows шукає за колонкою (id дочірніх можуть НЕ збігатися з похідними), вимагає рівно одну Toysi- і одну RT-частину (інакше [] — fail-closed), має запасний шлях для старих дочірніх без колонки;
  - автоматика чеків повернення: для групи питає LiqPay за БАТЬКІВСЬКИМ id (один раз за прохід, а не за кожну частину), чеків повернення по частинах НЕ створює, шле ОДИН алерт людині з обома частинами
    і позначає обидві частини перевіреними; НЕ reversed → тиша; звичайні замовлення — як раніше (за власним order_id);
  - Telegram «Статус» зі сторінки подяки (батьківський id) знаходить першу частину групи; без колонки/без групи — як раніше;
  - алерт статусу LiqPay для батьківського id називає обидві частини.
Тимчасова БД, без мережі.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "site"))
import liqpay_client as lp  # noqa: E402
import orders_db  # noqa: E402
import site_chat_bot as bot  # noqa: E402
import site_order_api as api  # noqa: E402
import site_refund_receipts as srr  # noqa: E402

lp.LIQPAY_PUBLIC_KEY, lp.LIQPAY_PRIVATE_KEY, lp.LIQPAY_SANDBOX = "i1", "k1", False
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


orders_db.init_db()
with orders_db.get_connection() as c:
    cols = {r[1] for r in c.execute("PRAGMA table_info(orders)")}
    idx = {r[1] for r in c.execute("PRAGMA index_list(orders)")}
chk("міграція: колонка site_group_id і індекс існують; init_db повторно не падає", "site_group_id" in cols and "idx_orders_site_group_id" in idx)
orders_db.init_db()


def make(oid, items, group=None, total=100, receipt=True, confirmed=1, phone="+380501112233", pub=None):
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, dict(order_id=oid, platform="site", payment_method="prepaid", payment_confirmed=bool(confirmed), customer_name="Т Т", phone=phone,
                                       np_branch="Київ, Відділення №1", items=items, status="forwarded_to_supplier"))
        c.execute("UPDATE orders SET site_charged_total=?, site_group_id=?, checkbox_receipt_id=?, public_number=? WHERE internal_order_id=?",
                  (total, group, ("r-" + oid) if receipt else None, pub, f"site_{oid}"))
        c.commit()


T = [{"toysi_code": "100", "name": "Т", "qty": 1, "price": 60}]
R = [{"toysi_code": "rt-101", "name": "R", "qty": 1, "price": 40}]
P = "PT-20261023-090000-aaaaaa"      # батьківський номер
K_T, K_R = "PT-20261023-090000-111111", "PT-20261023-090000-222222"   # навмисно НЕ похідні від P
make(K_T, T, group=P, total=60, pub="PT-111111")
make(K_R, R, group=P, total=40, pub="PT-222222")

with orders_db.get_connection() as c:
    kids = orders_db.get_group_children(c, P)
    rows = api.mixed_children_rows(c, P)
chk("get_group_children: 2 частини в порядку створення (Toysi, RT)", [k["order_id"] for k in kids] == [K_T, K_R])
chk("mixed_children_rows шукає за колонкою (id дочірніх не похідні) і ставить Toysi перед RT", [k["order_id"] for k in rows] == [K_T, K_R])
with orders_db.get_connection() as c:
    chk("нема групи → [] (і для сміття/порожнього)", orders_db.get_group_children(c, "PT-20261023-090000-ffffff") == [] and orders_db.get_group_children(c, "") == [] and api.mixed_children_rows(c, "x") == [])

# група некоректна: дві Toysi-частини / одна частина → fail-closed
P2 = "PT-20261023-090001-bbbbbb"
make("PT-20261023-090001-aaaaa1", T, group=P2, total=60)
make("PT-20261023-090001-aaaaa2", T, group=P2, total=60)
P3 = "PT-20261023-090002-cccccc"
make("PT-20261023-090002-bbbbb1", T, group=P3, total=60)
with orders_db.get_connection() as c:
    chk("група з двох Toysi-частин → [] (fail-closed)", api.mixed_children_rows(c, P2) == [])
    chk("група з однієї частини → [] (fail-closed)", api.mixed_children_rows(c, P3) == [])

# запасний шлях: дочірні за похідними id БЕЗ колонки (створені до міграції)
P4 = "PT-20261023-090003-dddddd"
kt, kr = api.mixed_child_ids(P4)
make(kt, T, group=None, total=60)
make(kr, R, group=None, total=40)
with orders_db.get_connection() as c:
    chk("запасний шлях: дочірні за похідними id без site_group_id знаходяться", [k["order_id"] for k in api.mixed_children_rows(c, P4)] == [kt, kr])

# ── автоматика чеків повернення
alerts = []
srr._alert = lambda key, text: alerts.append((key, text))
asked, posted = [], []
STATUS = {}


def status_fn(oid):
    asked.append(oid)
    v = STATUS[oid]
    if isinstance(v, Exception):
        raise v
    return v


def run(**over):
    kw = dict(status_fn=status_fn, auth=lambda: "tok", get_receipt=lambda tok, rid: (_ for _ in ()).throw(AssertionError("get_receipt не має викликатись для групи")),
              fetch_window=lambda h, a, b: [], ensure_shift=lambda tok: None, post=lambda tok, body: posted.append(body) or {"id": "ret"}, wait=lambda tok, rid: {"id": rid})
    kw.update(over)
    with orders_db.get_connection() as c:
        return srr.run_once(c, **kw)


def checked(oid):
    with orders_db.get_connection() as c:
        return bool(orders_db.get_order(c, f"site_{oid}")["refund_checked_at"])


def uncheck(*oids):
    with orders_db.get_connection() as c:
        for o in oids:
            c.execute("UPDATE orders SET refund_checked_at=NULL WHERE internal_order_id=?", (f"site_{o}",))
        c.commit()


# лишаємо в кандидатах тільки групу P (інші тест-замовлення вимикаємо)
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET payment_confirmed=0 WHERE site_group_id IN (?,?) OR internal_order_id IN (?,?)", (P2, P3, f"site_{kt}", f"site_{kr}"))
    c.commit()
STATUS[P] = {"status": "success", "amount": 100.0, "order_id": P}
res = run()
chk("група: LiqPay питається за БАТЬКІВСЬКИМ id РІВНО один раз (не за кожну частину), обидві частини позначено перевіреними, алертів нема",
    asked == [P] and checked(K_T) and checked(K_R) and alerts == [] and posted == [])
uncheck(K_T, K_R)
asked.clear()
STATUS[P] = {"status": "reversed", "amount": 100.0, "refund_amount": 100.0, "order_id": P, "currency": "UAH"}
res = run()
chk("група reversed: чеків повернення НЕ створено (post не викликався), 1 запит до LiqPay за батьківським id, human=1", posted == [] and asked == [P] and res["human"] == 1 and res["created"] == 0)
chk("група reversed: ОДИН алерт refund_group:<батьківський> з обома частинами, номерами й діями людини", len(alerts) == 1 and alerts[0][0] == f"refund_group:{P}"
    and K_T in alerts[0][1] and K_R in alerts[0][1] and "PT-111111" in alerts[0][1] and "PT-222222" in alerts[0][1] and "ОДИН" in alerts[0][1] and "Checkbox" in alerts[0][1])
chk("група reversed: обидві частини позначено перевіреними (не опитуються щогодини щоразу)", checked(K_T) and checked(K_R))
# часткове повернення групи — теж алерт людині, не чек
uncheck(K_T, K_R)
alerts.clear()
asked.clear()
STATUS[P] = {"status": "reversed", "amount": 100.0, "refund_amount": 60.0, "order_id": P, "currency": "UAH"}
res = run()
chk("група: часткове повернення (60 із 100) → той самий алерт людині, чека нема", posted == [] and len(alerts) == 1 and "60" in alerts[0][1])
# відповідь LiqPay з чужим order_id → mismatch-алерт, не груповий
uncheck(K_T, K_R)
alerts.clear()
STATUS[P] = {"status": "reversed", "amount": 100.0, "refund_amount": 100.0, "order_id": "PT-20261023-999999-ffffff", "currency": "UAH"}
res = run()
chk("відповідь LiqPay з ІНШИМ order_id → алерт про розбіжність (refund_mismatch), не груповий, чека нема", posted == [] and alerts and alerts[0][0].startswith("refund_mismatch"))
# збій LiqPay-статусу не валить прохід і не позначає перевіреним
uncheck(K_T, K_R)
alerts.clear()
STATUS[P] = RuntimeError("мережа")
res = run(status_fn=lambda oid: (_ for _ in ()).throw(RuntimeError("мережа")))
chk("збій статусу LiqPay для групи: прохід не падає, refund_checked_at не ставиться (повтор у наступному циклі)", not checked(K_T) and not checked(K_R) and posted == [])

# звичайне замовлення — як раніше, за власним order_id
STATUS[P] = {"status": "success", "amount": 100.0, "order_id": P}
make("PT-20261023-091000-eeeeee", T, group=None, total=60)
STATUS["PT-20261023-091000-eeeeee"] = {"status": "success", "amount": 60.0}
asked.clear()
res = run()
chk("звичайне замовлення: LiqPay питається за ВЛАСНИМ order_id (поведінка не змінилась)", "PT-20261023-091000-eeeeee" in asked)

# ── Telegram «Статус»: батьківський id → перша частина групи
bot.ORDERS_DB = os.environ["ORDERS_DB_PATH"]
row_p = bot.fetch_order_row(P)
chk("fetch_order_row(батьківський id) повертає ПЕРШУ частину групи (Toysi, № PT-111111)", row_p is not None and row_p["public_number"] == "PT-111111")
chk("fetch_order_row(власний id дочірнього) — як раніше", bot.fetch_order_row(K_R)["public_number"] == "PT-222222")
chk("fetch_order_row(невідомий) → None", bot.fetch_order_row("PT-20261023-123456-abcdef") is None)

# ── алерт LiqPay-статусу називає обидві частини
os.environ.pop("AUDIT_NO_TELEGRAM", None)
hint = api._mixed_group_hint(P)
chk("_mixed_group_hint(батьківський) називає обидві частини, їх типи й номери", K_T in hint and K_R in hint and "Toysi" in hint and "RT" in hint and "PT-111111" in hint)
chk("_mixed_group_hint(звичайний/невідомий/None) → порожній рядок", api._mixed_group_hint("PT-20261023-091000-eeeeee") == "" and api._mixed_group_hint(None) == "" and api._mixed_group_hint("x") == "")

# ── mixed_order_lookup: за будь-яким номером групи
import mixed_order_lookup as mol  # noqa: E402
with orders_db.get_connection() as c:
    by_parent = mol.resolve(c, P)
    by_site_prefix = mol.resolve(c, "site_" + K_R)
    by_pub = mol.resolve(c, "PT-222222")
    nothing = [mol.resolve(c, x) for x in ("", "PT-20261023-123456-abcdef", "PT-999999", "garbage", "PT-20261023-091000-eeeeee")]
chk("lookup за батьківським номером → 2 частини", [k["order_id"] for k in by_parent] == [K_T, K_R])
chk("lookup за internal id частини (з префіксом site_) і за публічним номером покупця → та сама група", [k["order_id"] for k in by_site_prefix] == [K_T, K_R] and [k["order_id"] for k in by_pub] == [K_T, K_R])
chk("lookup: нема/сміття/звичайне (не групове) замовлення → []", all(x == [] for x in nothing))
txt = mol.render(by_parent)
chk("render: показує батьківський номер, обидві частини, типи й публічні номери, БЕЗ ПІБ/телефону", P in txt and K_T in txt and K_R in txt and "RT" in txt and "Toysi" in txt and "PT-111111" in txt and "380501112233" not in txt and "Т Т" not in txt)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
