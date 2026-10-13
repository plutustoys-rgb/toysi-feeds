#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_refund_receipts.py — автоматичний чек ПОВЕРНЕННЯ для онлайн-оплат сайту (site_refund_receipts, рішення власника 13.10.2026).
Інваріанти: (1) платіж не reversed → нічого; (2) повне повернення, чек продажу валідний → РІВНО один RETURN (related_receipt_id, сума), БД позначена, повторний цикл не створює другого;
(3) часткове повернення → чека нема, сигнал людині; (4) багатопозиційний чек → відмова валідації, сигнал, чека нема; (5) RETURN вже існує (зробив бухгалтер) → позначено виконаним БЕЗ створення;
(6) збій ДО відправки → claim знято, чека нема, повтор можливий; (7) збій ПІСЛЯ відправки → claim ЛИШЕНО, повторів нема, сигнал «перевір касу»; (8) тестові (sandbox_test) замовлення не чіпаються;
(9) збій LiqPay-статусу не валить прохід; (10) статус перевіряється не частіше разу на годину; (11) бойова БД не чіпається (ORDERS_DB_PATH). Мережа не потрібна. `python test_site_refund_receipts.py` → exit 0/1."""
import os
import sys
import tempfile

os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")   # ізоляція ДО імпорту orders_db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import liqpay_client as lp
import orders_db
import site_refund_receipts as srr

lp.LIQPAY_PUBLIC_KEY, lp.LIQPAY_PRIVATE_KEY, lp.LIQPAY_SANDBOX = "i1", "k1", False
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


alerts = []
srr._alert = lambda key, text: alerts.append((key, text))
orders_db.init_db()


def make_order(oid, total=23, payment_confirmed=1, status="forwarded_to_supplier", receipt="r-" ):
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, dict(order_id=oid, platform="site", payment_method="prepaid", payment_confirmed=bool(payment_confirmed), customer_name="Т Т", phone="+380501112233",
                                       np_branch="Київ, Відділення №1", items=[{"toysi_code": "1", "name": "x", "qty": 1, "price": total}], status=status))
        c.execute("UPDATE orders SET site_charged_total = ?, checkbox_receipt_id = ? WHERE internal_order_id = ?", (total, (receipt + oid) if receipt else None, f"site_{oid}"))
        c.commit()


def sell_receipt(rid, total=23, goods=1):
    k = total * 100
    return {"id": rid, "type": "SELL", "status": "DONE", "serial": 7, "fiscal_code": "FC1", "total_sum": k * goods, "is_test": False,
            "goods": [{"good": {"code": str(i), "name": f"Товар {i}", "price": k}, "quantity": 1000} for i in range(goods)],
            "payments": [{"type": "CASHLESS", "value": k * goods}], "delivery": {"phone": "+380501112233"}}


posted, shifts = [], []
STATUS = {}
SELLS = {}
FRESH = []


def run(**over):
    kw = dict(status_fn=lambda oid: STATUS[oid] if not isinstance(STATUS.get(oid), Exception) else (_ for _ in ()).throw(STATUS[oid]),
              auth=lambda: "tok", get_receipt=lambda tok, rid: SELLS[rid], fetch_window=lambda h, a, b: list(FRESH),
              ensure_shift=lambda tok: shifts.append(1), post=lambda tok, body: (posted.append(body) or {"id": "ret-" + str(len(posted))}),
              wait=lambda tok, rid: {"id": rid, "serial": 99, "fiscal_code": "FC9", "total_sum": 2300})
    kw.update(over)
    with orders_db.get_connection() as c:
        return srr.run_once(c, **kw)


def row(oid):
    with orders_db.get_connection() as c:
        return orders_db.get_order(c, f"site_{oid}")


def reset_check(*oids):
    with orders_db.get_connection() as c:
        for o in oids:
            c.execute("UPDATE orders SET refund_checked_at = NULL WHERE internal_order_id = ?", (f"site_{o}",))
        c.commit()


# ── (1) не reversed
make_order("A1")
STATUS["A1"] = {"status": "success", "amount": 23.0}
SELLS["r-A1"] = sell_receipt("r-A1")
res = run()
chk("статус success → нічого не створено, але перевірено", not posted and res["checked"] == 1 and res["created"] == 0 and row("A1")["refund_checked_at"])

# ── (10) не частіше разу на годину
res = run()
chk("повторний цикл одразу: замовлення не перевіряється знову (≤1/год)", res["checked"] == 0)

# ── (2) повне повернення → рівно один RETURN
STATUS["A1"] = {"status": "reversed", "amount": 23.0, "refund_amount": 23.0}
reset_check("A1")
res = run()
chk("reversed + повне повернення → створено один RETURN", res["created"] == 1 and len(posted) == 1)
b = posted[0]
chk("RETURN: related_receipt_id = чек продажу, is_return, сума й оплата CASHLESS", b["related_receipt_id"] == "r-A1" and b["goods"][0]["is_return"] is True and b["payments"] == [{"type": "CASHLESS", "value": 2300}])
chk("БД: refund_receipt_at і id заповнені, claim лишився (не відкочується після успіху)", row("A1")["refund_receipt_at"] and row("A1")["refund_receipt_id"])
reset_check("A1")
res = run()
chk("повторний цикл (навіть з скинутою перевіркою): другого RETURN нема", len(posted) == 1 and res["created"] == 0 and res["checked"] == 0)

# ── (3) часткове повернення
make_order("P1", total=50)
STATUS["P1"] = {"status": "reversed", "amount": 50.0, "refund_amount": 20.0}
SELLS["r-P1"] = sell_receipt("r-P1", 50)
alerts.clear(); n0 = len(posted)
res = run()
chk("часткове повернення → чека нема, сигнал людині", len(posted) == n0 and res["human"] == 1 and any("не повне" in t for _, t in alerts))

# ── (4) багатопозиційний чек
make_order("M1", total=40)
STATUS["M1"] = {"status": "reversed", "amount": 40.0, "refund_amount": 40.0}
SELLS["r-M1"] = sell_receipt("r-M1", 20, goods=2)
alerts.clear(); n0 = len(posted)
res = run()
chk("чек із двома позиціями → відмова валідації, чека нема, сигнал", len(posted) == n0 and res["human"] == 1 and any("НЕ створено" in t for _, t in alerts))

# ── (5) RETURN уже існує
make_order("D1")
STATUS["D1"] = {"status": "reversed", "amount": 23.0, "refund_amount": 23.0}
SELLS["r-D1"] = sell_receipt("r-D1")
FRESH[:] = [{"type": "RETURN", "related_receipt_id": "r-D1", "status": "DONE", "serial": 5, "id": "ret-manual"}]
n0 = len(posted)
res = run()
chk("RETURN уже є (бухгалтер) → позначено виконаним, нового чека нема", len(posted) == n0 and res["already"] == 1 and row("D1")["refund_receipt_id"] == "ret-manual")
FRESH.clear()

# ── (6) збій ДО відправки
make_order("E1")
STATUS["E1"] = {"status": "reversed", "amount": 23.0, "refund_amount": 23.0}
SELLS["r-E1"] = sell_receipt("r-E1")
n0 = len(posted)
res = run(ensure_shift=lambda tok: (_ for _ in ()).throw(RuntimeError("зміна не відкрилась")))
chk("збій до відправки → чека нема, claim знято, refund_receipt_at порожній", len(posted) == n0 and res["failed"] == 1 and row("E1")["refund_receipt_claim_at"] is None and row("E1")["refund_receipt_at"] is None)
reset_check("E1")
res = run()
chk("наступний цикл повторює й створює чек", res["created"] == 1 and len(posted) == n0 + 1)

# ── (7) збій ПІСЛЯ відправки
make_order("U1")
STATUS["U1"] = {"status": "reversed", "amount": 23.0, "refund_amount": 23.0}
SELLS["r-U1"] = sell_receipt("r-U1")
alerts.clear(); n0 = len(posted)
res = run(wait=lambda tok, rid: (_ for _ in ()).throw(RuntimeError("таймаут опитування")))
chk("збій після відправки → claim ЛИШЕНО, refund_receipt_at порожній, сигнал «перевір касу»", row("U1")["refund_receipt_claim_at"] and row("U1")["refund_receipt_at"] is None and any("ВІДПРАВЛЕНО" in t for _, t in alerts))
reset_check("U1")
res = run()
chk("автоповтору після невідомого результату НЕМАЄ (claim тримає)", len(posted) == n0 + 1 and res["checked"] == 0)

# ── (8) тестові замовлення
make_order("T1", status="sandbox_test")
STATUS["T1"] = {"status": "reversed", "amount": 23.0, "refund_amount": 23.0}
SELLS["r-T1"] = sell_receipt("r-T1")
n0 = len(posted)
res = run()
chk("sandbox_test не чіпається (ні перевірки, ні чека)", len(posted) == n0 and row("T1")["refund_checked_at"] is None)

# ── (9) збій LiqPay-статусу
make_order("L1")
STATUS["L1"] = RuntimeError("LiqPay 500")
res = run()
chk("збій статусу LiqPay не валить прохід, перевірку не відмічено (повтор у наступному циклі)", res["checked"] == 1 and row("L1")["refund_checked_at"] is None)

# ── додаткові: програш claim, невідповідність суми, sandbox-гейт, звірка відповіді LiqPay, завислий claim, зупинка після збоїв LiqPay
make_order("C1")
STATUS["C1"] = {"status": "reversed", "amount": 23.0, "refund_amount": 23.0}
SELLS["r-C1"] = sell_receipt("r-C1")
n0 = len(posted)
real_claim = orders_db.claim_refund_receipt
orders_db.claim_refund_receipt = lambda conn, oid: False
res = run()
orders_db.claim_refund_receipt = real_claim
chk("claim програно (інший процес) → чека не створюємо", len(posted) == n0 and res["created"] == 0)
reset_check("C1")
res = run()
chk("після програного claim наступний цикл створює (claim не було взято)", res["created"] == 1)

make_order("S1", total=25)
STATUS["S1"] = {"status": "reversed", "amount": 25.0, "refund_amount": 25.0}
SELLS["r-S1"] = sell_receipt("r-S1", 23)             # чек продажу на 23, оплата замовлення 25
alerts.clear()
n0 = len(posted)
res = run()
chk("сума чека продажу ≠ сумі замовлення → відмова, чека нема, сигнал", len(posted) == n0 and res["human"] == 1 and any("НЕ створено" in t_ for _, t_ in alerts))

make_order("X1")
STATUS["X1"] = {"status": "reversed", "amount": 23.0, "refund_amount": 23.0, "order_id": "ІНШЕ"}
SELLS["r-X1"] = sell_receipt("r-X1")
alerts.clear()
n0 = len(posted)
res = run()
chk("відповідь LiqPay з іншим order_id → чека нема, сигнал", len(posted) == n0 and res["human"] == 1 and any("не збігається" in t_ for _, t_ in alerts))

lp.LIQPAY_SANDBOX = True
make_order("B1")
STATUS["B1"] = {"status": "reversed", "amount": 23.0, "refund_amount": 23.0}
SELLS["r-B1"] = sell_receipt("r-B1")
n0 = len(posted)
res = srr.run_once(None)          # без status_fn у sandbox-режимі: нічого
lp.LIQPAY_SANDBOX = False
chk("sandbox-режим без підміни → вихід без викликів LiqPay і без чеків", res["checked"] == 0 and len(posted) == n0)

with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET refund_receipt_claim_at = '2020-01-01T00:00:00' WHERE internal_order_id = 'site_B1'")
    c.commit()
alerts.clear()
run()
chk("завислий claim (>1 год, без чека) → сигнал людині", any(k == "refund_stuck:site_B1" for k, _ in alerts))

for i in range(4):
    make_order(f"Q{i}")
    STATUS[f"Q{i}"] = RuntimeError("LiqPay лежить")
res = run()
chk("3 збої LiqPay підряд → прохід зупинено (не 4 звернення)", res["checked"] == 3)

# ── без чека продажу / без підтвердженої оплати
make_order("N1", receipt=None)
make_order("N2", payment_confirmed=0)
STATUS["N1"] = STATUS["N2"] = {"status": "reversed", "refund_amount": 23.0}
n0 = len(posted)
res = run()
chk("без checkbox_receipt_id чи без підтвердженої оплати — не кандидат", len(posted) == n0 and row("N1")["refund_checked_at"] is None and row("N2")["refund_checked_at"] is None)

# ── (11) ізоляція
chk("тест працює на тимчасовій БД, не на бойовій", orders_db.DB_PATH == os.environ["ORDERS_DB_PATH"] and "orders.db" in orders_db.DB_PATH and tempfile.gettempdir() in orders_db.DB_PATH)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
