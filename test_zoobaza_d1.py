#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_zoobaza_d1.py — регрес D1 ZooBaza (intake підключено до order_pipeline; схема zoobaza_intake.py, аудит #634 M-1/M-3).

ІНВАРІАНТИ:
  - get_orders_ready_to_forward НІКОЛИ не віддає Toysi-роутеру замовлення з позиціями zb- (чисті І змішані) — навіть БЕЗ claim
    (подвійний захист за складом позицій) і з статусами zoobaza_hold / zoobaza_mixed_hold;
  - звичайні Toysi-замовлення (cod та оплачений prepaid) проходять як і раніше; RT/кенсел-виключення не зламані;
  - claim_new ставить статуси; daily_report показує утримані як «ZooBaza: утримано», НЕ як «застрягло»; змішане — з потребою уваги;
  - order_pipeline.run_once: poll -> bank_check -> claim_new -> route (claim ДО роутера); збій claim НЕ зупиняє роутинг.
Тимчасова БД, без мережі й Telegram.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import daily_report
import order_pipeline
import orders_db as odb
import zoobaza_intake as zi

F = []


def chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        F.append(name)


tmp = Path(tempfile.mkdtemp())
db = str(tmp / "t.db")
odb.init_db(db)


def item(code, qty=1):
    return {"toysi_code": code, "name": "x", "qty": qty, "price": 100}


def add(conn, oid, items, method="prepaid", paid=True, platform="site"):
    odb.insert_order(conn, {"order_id": oid, "platform": platform, "payment_method": method, "payment_confirmed": paid, "items": items})
    return f"{platform}_{oid}"


with odb.get_connection(db) as c:
    ids = {
        "toy_prepaid": add(c, "1", [item("111")]),
        "toy_cod": add(c, "2", [item("222")], method="cod", paid=False),
        "zb": add(c, "3", [item("zb-501")]),
        "mixed": add(c, "4", [item("zb-502"), item("333")]),
        "toy_unpaid": add(c, "5", [item("444")], paid=False),
    }

def ready(conn):
    return {o["internal_order_id"] for o in odb.get_orders_ready_to_forward(conn)}

with odb.get_connection(db) as c:
    r0 = ready(c)
chk("БЕЗ claim: чисте zb- і змішане НЕ потрапляють у роутер (подвійний захист за складом позицій)",
    ids["zb"] not in r0 and ids["mixed"] not in r0)
chk("звичайні Toysi-замовлення (оплачений prepaid і cod) проходять", ids["toy_prepaid"] in r0 and ids["toy_cod"] in r0)
chk("неоплачений prepaid не проходить (як і раніше)", ids["toy_unpaid"] not in r0)

with odb.get_connection(db) as c:
    res = zi.claim_new(c, state_path=tmp / "zb_state.json", notify=False)
chk("claim_new: zb- -> zoobaza_hold, змішане -> zoobaza_mixed_hold", ids["zb"] in res["claimed"] and ids["mixed"] in res["mixed"])
with odb.get_connection(db) as c:
    st = {r["internal_order_id"]: r["status"] for r in c.execute("SELECT internal_order_id, status FROM orders").fetchall()}
chk("статуси записані в orders.db", st[ids["zb"]] == "zoobaza_hold" and st[ids["mixed"]] == "zoobaza_mixed_hold")
chk("Toysi-замовлення claim НЕ чіпає", st[ids["toy_prepaid"]] != "zoobaza_hold" and st[ids["toy_cod"]] != "zoobaza_hold")
with odb.get_connection(db) as c:
    chk("після claim роутер їх теж не бачить (статуси + склад позицій)", not ({ids["zb"], ids["mixed"]} & ready(c)))
    res2 = zi.claim_new(c, state_path=tmp / "zb_state.json", notify=False)
chk("claim_new ідемпотентний (повторно нічого не забирає)", not res2["claimed"] and not res2["mixed"])

# --- статус зі складом: навіть якщо хтось скинув статус (напр. bank_check -> awaiting_manual_confirmation), роутер їх не бачить
with odb.get_connection(db) as c:
    c.execute("UPDATE orders SET status = 'awaiting_manual_confirmation' WHERE internal_order_id = ?", (ids["zb"],))
    chk("статус скинуто на сторонній — роутер все одно не бачить zb- (склад позицій)", ids["zb"] not in ready(c))
    zi.claim_new(c, state_path=tmp / "zb_state.json", notify=False)
    chk("наступний claim повертає утримання", c.execute("SELECT status FROM orders WHERE internal_order_id=?", (ids["zb"],)).fetchone()[0] == "zoobaza_hold")

# --- daily_report
with odb.get_connection(db) as c:
    section = daily_report._open_orders_detail_section(c)
hold_line = next((ln for ln in section.split(chr(10)) if "ZooBaza: утримано" in ln), "")
chk("звіт: утримане zb- показано як «ZooBaza: утримано», а не «застрягло»", bool(hold_line) and "застрягло" not in hold_line)
chk("звіт: змішаний кошик помічений і рахується як «потребує уваги»", "ЗМІШАНИЙ" in section and "потребують уваги" in section)

# --- order_pipeline: порядок і стійкість
calls = []
saved = (order_pipeline.orders_watcher.poll_once, order_pipeline.bank_check.check_pending_prepayments,
         order_pipeline.order_router.route_pending_orders, order_pipeline.site_refund_receipts.run_once,
         order_pipeline.zoobaza_intake.claim_new, order_pipeline.get_connection)


class _Conn:
    def __enter__(self):
        return object()

    def __exit__(self, *a):
        return False


try:
    order_pipeline.orders_watcher.poll_once = lambda: calls.append("poll")
    order_pipeline.bank_check.check_pending_prepayments = lambda: calls.append("bank")
    order_pipeline.order_router.route_pending_orders = lambda: calls.append("route")
    order_pipeline.site_refund_receipts.run_once = lambda: calls.append("refund")
    order_pipeline.zoobaza_intake.claim_new = lambda conn, **k: calls.append("claim")
    order_pipeline.get_connection = lambda: _Conn()
    order_pipeline.run_once()
    chk("run_once: poll -> bank -> claim -> route (claim ДО роутера)", calls[:4] == ["poll", "bank", "claim", "route"])

    calls.clear()

    def _boom(conn, **k):
        calls.append("claim")
        raise RuntimeError("збій claim")
    order_pipeline.zoobaza_intake.claim_new = _boom
    order_pipeline.run_once()
    chk("збій claim НЕ зупиняє роутинг і наступні кроки", calls == ["poll", "bank", "claim", "route", "refund"])
finally:
    (order_pipeline.orders_watcher.poll_once, order_pipeline.bank_check.check_pending_prepayments,
     order_pipeline.order_router.route_pending_orders, order_pipeline.site_refund_receipts.run_once,
     order_pipeline.zoobaza_intake.claim_new, order_pipeline.get_connection) = saved

sys.exit(1 if F else 0)
