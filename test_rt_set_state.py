#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rt_set_state.py — ручне виставлення стану застряглого rt-замовлення: дозволені переходи, обов'язковість --ref/--ttn, без --yes нічого не пишеться."""
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
sys.path.insert(0, str(Path(__file__).parent))
import orders_db  # noqa: E402
import rt_set_state as rs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


row = {"rt_state": "rt_ordering", "toysi_ttn": "20451565972503"}
chk("rt_ordering -> rt_ordered з --ref і збіжним --ttn: дозволено", rs.plan(row, "rt_ordered", "RT000773594", "20451565972503") is None)
chk("rt_ordered без --ref / з поганим --ref / без --ttn / з чужим --ttn: відмова",
    all(rs.plan(row, "rt_ordered", r, t) for r, t in ((None, "20451565972503"), ("773594", "20451565972503"), ("RT000773594", None), ("RT000773594", "20451500000000"))))
chk("rt_ordering -> marking_ready / manual: дозволено; -> ttn_created: відмова", rs.plan(row, "marking_ready", None, None) is None and rs.plan(row, "manual", None, None) is None and rs.plan(row, "ttn_created", None, None))
chk("ttn_creating -> ttn_created лише з ТТН у базі; -> rt_ordered: відмова", rs.plan({"rt_state": "ttn_creating", "toysi_ttn": "2045"}, "ttn_created", None, None) is None and rs.plan({"rt_state": "ttn_creating", "toysi_ttn": ""}, "ttn_created", None, None) and rs.plan({"rt_state": "ttn_creating", "toysi_ttn": "2045"}, "rt_ordered", "RT000773594", "2045"))
chk("не застряглий стан (marking_ready / rt_ordered / NULL / delivered): відмова", all(rs.plan({"rt_state": s, "toysi_ttn": "1"}, "manual", None, None) for s in ("marking_ready", "rt_ordered", None, "delivered")))

orders_db.init_db()
with orders_db.get_connection() as c:
    orders_db.insert_order(c, {"order_id": "PT-20261020-111111-aaaaaa", "platform": "site", "payment_method": "prepaid", "payment_confirmed": 0, "customer_name": "Іван Петренко", "phone": "380671112233", "email": "b@e.com",
                               "np_branch": "Київ, Відділення №5", "np_city_ref": "C", "np_warehouse_number": "5", "carrier": "nova_poshta", "items": [{"toysi_code": "rt-1", "name": "x", "qty": 1, "price": 30}]})
    c.execute("UPDATE orders SET rt_state='rt_ordering', toysi_ttn='20451565972503' WHERE internal_order_id='site_PT-20261020-111111-aaaaaa'")
    c.commit()
rc = rs.main(["--order", "PT-20261020-111111-aaaaaa", "--to", "rt_ordered", "--ref", "RT000773594", "--ttn", "20451565972503"])
with orders_db.get_connection() as c:
    st1 = c.execute("SELECT rt_state FROM orders WHERE internal_order_id='site_PT-20261020-111111-aaaaaa'").fetchone()[0]
chk("без --yes нічого не записано (стан лишився rt_ordering)", rc == 0 and st1 == "rt_ordering")
rc = rs.main(["--order", "PT-20261020-111111-aaaaaa", "--to", "rt_ordered", "--ref", "RT000773594", "--ttn", "20451565972503", "--yes"])
with orders_db.get_connection() as c:
    r2 = c.execute("SELECT rt_state, rt_order_ref FROM orders WHERE internal_order_id='site_PT-20261020-111111-aaaaaa'").fetchone()
chk("з --yes: rt_ordered + RT000773594", rc == 0 and tuple(r2) == ("rt_ordered", "RT000773594"))
chk("повторно (вже не застряглий) -> відмова, код 2; невідоме замовлення -> код 2", rs.main(["--order", "PT-20261020-111111-aaaaaa", "--to", "manual", "--yes"]) == 2 and rs.main(["--order", "PT-1", "--to", "manual", "--yes"]) == 2)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
