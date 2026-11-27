#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rozetka_refund_receipts.py — автоматичний чек ПОВЕРНЕННЯ для Rozetka (rozetka_refund_receipts, запит бухгалтера 27.11.2026).
Інваріанти: (1) paid -> нічого; (2) returned і RETURN ще нема -> РІВНО один RETURN, claim лишається/позначено done; (3) returned і RETURN уже є (ручний) -> лише позначка, чек не створюється
(реальні 912431454, 905484851); (4) реальний 912764384 (paid) -> нічого; (5) багатопозиційний чек -> відмова validate, алерт, чека нема; (6) збій ДО відправки -> claim знято;
(7) збій ПІСЛЯ відправки -> claim ЛИШЕНО, алерт; (8) збій Rozetka API не валить прохід (3 збої підряд — зупинка); (9) статус не частіше разу на годину;
(10) невідомий статус (напр. partially_returned) -> нічого; (11) site/prom-замовлення не чіпаються. `python test_rozetka_refund_receipts.py` -> exit 0/1."""
import os
import sys
import tempfile

os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")   # ізоляція ДО імпорту orders_db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import orders_db
import rozetka_refund_receipts as rrr

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


alerts = []
rrr._alert = lambda key, text: alerts.append((key, text))
orders_db.init_db()


def make_order(oid, total=258, platform="rozetka", payment_method="prepaid", receipt="r-", delivery="returned"):
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, dict(order_id=oid, platform=platform, payment_method=payment_method, payment_confirmed=True, customer_name="Т Т", phone="+380501112233",
                                       np_branch="Київ, Відділення №1", items=[{"toysi_code": "1", "name": "x", "qty": 1, "price": total}], status="forwarded_to_supplier"))
        c.execute("UPDATE orders SET checkbox_receipt_id = ?, delivery_status = ? WHERE internal_order_id = ?", ((receipt + oid) if receipt else None, delivery, f"{platform}_{oid}"))
        c.commit()


def sell_receipt(rid, total=258, goods=1):
    k = total * 100
    return {"id": rid, "type": "SELL", "status": "DONE", "serial": 7, "fiscal_code": "FC1", "total_sum": k * goods, "is_test": False,
            "goods": [{"good": {"code": str(i), "name": f"Товар {i}", "price": k}, "quantity": 1000} for i in range(goods)],
            "payments": [{"type": "CASHLESS", "value": k * goods}], "delivery": {"phone": "+380501112233"}}


posted, shifts = [], []
STATUS, SELLS, FRESH = {}, {}, []
calls = []


def status_fn(oid):
    calls.append(oid)
    v = STATUS[oid]
    if isinstance(v, Exception):
        raise v
    return v


ORDERS = {}


def order_fn(oid):
    return ORDERS.get(oid, {"amount": "258.00", "total_quantity": 1})


def run(**over):
    kw = dict(status_fn=status_fn, order_fn=order_fn, auth=lambda: "tok", get_receipt=lambda tok, rid: SELLS[rid], fetch_window=lambda h, a, b: list(FRESH),
              ensure_shift=lambda tok: shifts.append(1), post=lambda tok, body: (posted.append(body) or {"id": "ret-" + str(len(posted))}),
              wait=lambda tok, rid: {"id": rid, "serial": 99, "fiscal_code": "FC9", "total_sum": 25800})
    kw.update(over)
    with orders_db.get_connection() as c:
        return rrr.run_once(c, **kw)


def row(oid, platform="rozetka"):
    with orders_db.get_connection() as c:
        return orders_db.get_order(c, f"{platform}_{oid}")


def reset_check(*oids):
    with orders_db.get_connection() as c:
        for o in oids:
            c.execute("UPDATE orders SET refund_checked_at = NULL WHERE internal_order_id = ?", (f"rozetka_{o}",))
        c.commit()


# ── (1)+(4) paid -> нічого (реальний 912764384)
make_order("912764384", total=137, delivery="shipped")
STATUS["912764384"] = {"name": "paid", "title": "Оплачено", "value": 1}
SELLS["r-912764384"] = sell_receipt("r-912764384", 137)
r = run()
chk("paid (912764384): нічого не створено, чек не відправлено", r["created"] == 0 and not posted and r["checked"] == 1)
chk("paid: refund_checked_at виставлено, refund_receipt_at порожній", row("912764384").get("refund_checked_at") and not row("912764384").get("refund_receipt_at"))

# ── (2) returned, RETURN ще нема -> рівно один RETURN
make_order("2001")
STATUS["2001"] = {"name": "returned", "title": "Повернено", "value": 8}
SELLS["r-2001"] = sell_receipt("r-2001")
r = run()
chk("returned, RETURN нема: створено рівно 1 чек повернення", r["created"] == 1 and len(posted) == 1 and len(shifts) >= 1)
chk("тіло RETURN: related_receipt_id = чек продажу, is_return, оплата CASHLESS на всю суму", posted and posted[0]["related_receipt_id"] == "r-2001" and posted[0]["goods"][0]["is_return"] is True and posted[0]["payments"][0]["value"] == 25800)
chk("замовлення позначено виконаним (refund_receipt_at)", bool(row("2001").get("refund_receipt_at")))
n = len(posted)
reset_check("2001")
run()
chk("повторний прохід НЕ створює другий чек (ідемпотентність)", len(posted) == n)

# ── (3) returned, RETURN уже є (ручний) -> лише позначка (реальні 912431454, 905484851)
for oid in ("912431454", "905484851"):
    make_order(oid)
    STATUS[oid] = {"name": "returned", "title": "Повернено", "value": 8}
    SELLS["r-" + oid] = sell_receipt("r-" + oid)
FRESH[:] = [{"id": "man-1", "type": "RETURN", "status": "DONE", "serial": 258, "related_receipt_id": "r-912431454"},
            {"id": "man-2", "type": "RETURN", "status": "DONE", "serial": 130, "related_receipt_id": "r-905484851"}]
n = len(posted)
r = run()
chk("912431454 і 905484851: RETURN уже є -> already=2, нових чеків 0", r["already"] == 2 and len(posted) == n)
chk("обидва позначено виконаними без створення", bool(row("912431454").get("refund_receipt_at")) and bool(row("905484851").get("refund_receipt_at")))
FRESH[:] = []

# ── (5) багатопозиційний чек -> відмова, алерт, чека нема
make_order("3001")
STATUS["3001"] = {"name": "returned"}
SELLS["r-3001"] = sell_receipt("r-3001", 100, goods=2)
n, na = len(posted), len(alerts)
r = run()
chk("багатопозиційний чек: чек не створено, human=1, алерт є", r["human"] == 1 and len(posted) == n and len(alerts) > na)
chk("багатопозиційний: refund_receipt_at порожній", not row("3001").get("refund_receipt_at"))

# ── (6) збій ДО відправки -> claim знято
make_order("4001")
STATUS["4001"] = {"name": "returned"}
SELLS["r-4001"] = sell_receipt("r-4001")
def shift_boom(tok):
    raise RuntimeError("зміну не відкрито")
n = len(posted)
r = run(ensure_shift=shift_boom)
chk("збій до відправки: чека нема, claim знято, повтор можливий", r["failed"] >= 1 and len(posted) == n and not row("4001").get("refund_receipt_claim_at"))
reset_check("4001")
r = run()
chk("наступний цикл після збою до відправки створює чек", r["created"] == 1 and bool(row("4001").get("refund_receipt_at")))

# ── (7) збій ПІСЛЯ відправки -> claim лишається, алерт
make_order("5001")
STATUS["5001"] = {"name": "returned"}
SELLS["r-5001"] = sell_receipt("r-5001")
def wait_boom(tok, rid):
    raise TimeoutError("результат невідомий")
na = len(alerts)
r = run(wait=wait_boom)
chk("збій після відправки: claim ЛИШЕНО, refund_receipt_at порожній, алерт", bool(row("5001").get("refund_receipt_claim_at")) and not row("5001").get("refund_receipt_at") and len(alerts) > na)
n = len(posted)
reset_check("5001")
run()
chk("claim лишився -> повторного POST немає", len(posted) == n)

# ── (8) збій Rozetka API не валить прохід
for o in ("6001", "6002", "6003", "6004"):
    make_order(o)
    STATUS[o] = ConnectionError("Rozetka недоступна")
r = run()
chk("3 збої API підряд -> прохід зупинено, без винятку", r["checked"] == 3 and r["created"] == 0)
chk("при збої API refund_checked_at НЕ виставлено (повтор у наступному циклі)", not row("6001").get("refund_checked_at"))

# ── (9) не частіше разу на годину
for o in ("6001", "6002", "6003", "6004"):
    STATUS[o] = {"name": "paid"}      # API відновився: ці замовлення більше не збійні
run()
calls.clear()
make_order("7001")
STATUS["7001"] = {"name": "paid"}
run()
n1 = calls.count("7001")
run()
chk("статус одного замовлення — не частіше разу на годину", n1 == 1 and calls.count("7001") == 1)

# ── (10) невідомий статус -> нічого
make_order("8001")
STATUS["8001"] = {"name": "partially_returned"}
SELLS["r-8001"] = sell_receipt("r-8001")
n = len(posted)
run()
chk("невідомий/частковий статус: чек не створюється", len(posted) == n and not row("8001").get("refund_receipt_at"))

# ── (11) site/prom не чіпаються
make_order("9001", platform="site")
make_order("9002", platform="prom")
STATUS["9001"] = {"name": "returned"}; STATUS["9002"] = {"name": "returned"}
calls.clear()
run()
chk("site/prom замовлення цим модулем не опитуються", "9001" not in calls and "9002" not in calls)
with orders_db.get_connection() as _c:
    _cands = orders_db.get_rozetka_refund_candidates(_c, "2000-01-01T00:00:00", "2999-01-01T00:00:00")
chk("get_rozetka_refund_candidates не віддає не-rozetka", all(c["platform"] == "rozetka" for c in _cands))

# ── (12) запобіжник: returned, але замовлення НЕ повернуто цілим (delivered/shipped) -> чека нема, алерт
make_order("1201", delivery="delivered")
STATUS["1201"] = {"name": "returned"}
SELLS["r-1201"] = sell_receipt("r-1201")
n, na = len(posted), len(alerts)
r = run()
chk("returned при delivery_status=delivered: чек НЕ створено, human, алерт «ймовірно часткове»", len(posted) == n and r["human"] >= 1 and any("rz_refund_notfull:rozetka_1201" == a[0] for a in alerts[na:]))
chk("запобіжник: refund_receipt_at порожній", not row("1201").get("refund_receipt_at"))
al = [a[1] for a in alerts if a[0] == "rz_refund_notfull:rozetka_1201"]
chk("алерт містить номер замовлення Rozetka, id і серіал чека продажу", al and "1201" in al[0] and "r-1201" in al[0] and "серіал 7" in al[0])
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET delivery_status = 'returned', refund_checked_at = NULL WHERE internal_order_id = 'rozetka_1201'"); c.commit()
r = run()
chk("після переходу замовлення в returned наступний цикл створює чек", r["created"] == 1 and bool(row("1201").get("refund_receipt_at")))
for ds in ("cancelled", "expired"):
    oid = "12" + ds[:2]
    make_order(oid, delivery=ds); STATUS[oid] = {"name": "returned"}; SELLS["r-" + oid] = sell_receipt("r-" + oid)
n = len(posted)
r = run()
chk("cancelled/expired теж вважаються цілим поверненням -> чеки створено (2)", len(posted) == n + 2)

# ── (13) аудит #2093 Z1: сума й кількість із замовлення Rozetka
make_order("1301")
STATUS["1301"] = {"name": "returned"}; SELLS["r-1301"] = sell_receipt("r-1301", 258)
ORDERS["1301"] = {"amount": "300.00", "total_quantity": 1}
n, na = len(posted), len(alerts)
r = run()
chk("сума замовлення Rozetka ≠ сумі чека -> відмова validate, чека нема, алерт", len(posted) == n and r["human"] >= 1 and len(alerts) > na)
make_order("1302")
STATUS["1302"] = {"name": "returned"}; SELLS["r-1302"] = sell_receipt("r-1302", 258)
ORDERS["1302"] = {"amount": "258.00", "total_quantity": 2}
n = len(posted)
r = run()
chk("total_quantity=2 -> автоматично не видаємо", len(posted) == n and not row("1302").get("refund_receipt_at"))
make_order("1303")
STATUS["1303"] = {"name": "returned"}; SELLS["r-1303"] = sell_receipt("r-1303", 258)
ORDERS["1303"] = {"total_quantity": 1}
n = len(posted)
r = run()
chk("суми замовлення нема -> відмова, чека нема", len(posted) == n and not row("1303").get("refund_receipt_at"))
print("alert done sent:", any(a[0].startswith("rz_refund_done") for a in alerts))
chk("успішний чек НЕ шле Telegram-алерт rz_refund_done (правило власника)", not any(a[0].startswith("rz_refund_done") for a in alerts))

print("ALL OK" if not F else f"FAILED: {F}")
sys.exit(1 if F else 0)
