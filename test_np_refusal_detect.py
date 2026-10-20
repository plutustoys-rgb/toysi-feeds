#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_np_refusal_detect.py — 20.10.2026 (власник: «детектити відмови і повернення»): відправлене замовлення, яке НП вже позначила відмовою/поверненням, трекер переводить у returned (+алерт; КОДВ-сторно далі робить daily_report), хоча Toysi досі каже shipped.
Інваріанти: (1) відмова/повернення (текст статусу або документ CargoReturn) → returned + один алерт з ТТН; (2) звичайні («Відправлення отримано», «прямує») — НЕ чіпаємо; (3) збій НП → нічого не ламається, статус від Toysi лишається; (4) повторний цикл алерт не дублює (returned виходить з опитування); (5) is_refusal на ЖИВИХ статусах з бази (отримано ≠ відмова; код 104/111 без слів ≠ відмова)."""
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
import nova_poshta  # noqa: E402
import order_status_tracker as ost  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


chk("is_refusal: «Відмова від отримання» → так; документ CargoReturn → так; «Відправлення отримано» → ні; «Відправлення прямує…» → ні; код 111 без слів → ні",
    nova_poshta.is_refusal({"status": "Відмова від отримання", "status_code": "102"}) and nova_poshta.is_refusal({"status": "x", "return_ttn": "59001806649372"})
    and not nova_poshta.is_refusal({"status": "Відправлення отримано", "status_code": "9"}) and not nova_poshta.is_refusal({"status": "Відправлення прямує до міста Київ", "status_code": "5"})
    and not nova_poshta.is_refusal({"status": "Невдала спроба доставки", "status_code": "111"}))

chk("is_refusal (аудит #935 N1): «Одержано і створено ЄН повернення коштів» / «Відправлення отримано. …повернення коштів» → НЕ відмова; код 102 → відмова; «Відправлення повертається» → відмова; код 104/111 без слів → ні",
    not nova_poshta.is_refusal({"status": "Одержано і створено ЄН повернення коштів", "status_code": "106"}) and not nova_poshta.is_refusal({"status": "Відправлення отримано. Повернення коштів на карту", "status_code": "9"})
    and nova_poshta.is_refusal({"status": "щось", "status_code": "102"}) and nova_poshta.is_refusal({"status": "Відправлення повертається відправнику", "status_code": "103"})
    and not nova_poshta.is_refusal({"status": "Зміна адреси одержувача", "status_code": "104"}))
orders_db.init_db()


def mk(oid, platform="prom", ds="shipped", ttn="20451500000001", tid="1"):
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": platform, "payment_method": "cod", "payment_confirmed": 0, "customer_name": "Іван Петренко", "phone": "380671112233", "email": "b@e.com",
                                   "np_branch": "Київ, №5", "np_city_ref": "C", "np_warehouse_number": "5", "carrier": "nova_poshta", "items": [{"toysi_code": "100", "name": "x", "qty": 1, "price": 100}]})
        iid = f"{platform}_{oid}"
        c.execute("UPDATE orders SET forwarded_to_toysi_at='2026-10-01T10:00:00', toysi_order_id=?, toysi_ttn=?, delivery_status=? WHERE internal_order_id=?", (tid, ttn, ds, iid))
        c.commit()
    return iid


def row(iid):
    with orders_db.get_connection() as c:
        return orders_db._row_to_dict(c.execute("SELECT * FROM orders WHERE internal_order_id=?", (iid,)).fetchone())


a = mk("A1", ttn="20451500000011", tid="11")      # відмова
b = mk("B1", platform="eva", ttn="20451500000012", tid="12")      # отримано
c_ = mk("C1", platform="site", ttn="20451500000013", tid="13")    # прямує
d = mk("D1", ttn="20451500000014", tid="14")      # CargoReturn створено
NP = {"20451500000011": {"status": "Відмова від отримання", "status_code": "102", "return_ttn": "59001806649372", "based_ttn": None, "based_type": "CargoReturn"},
      "20451500000012": {"status": "Відправлення отримано", "status_code": "9", "return_ttn": None, "based_ttn": None, "based_type": None},
      "20451500000013": {"status": "Відправлення прямує до міста Київ", "status_code": "5", "return_ttn": None, "based_ttn": None, "based_type": None},
      "20451500000014": {"status": "Прибув у відділення", "status_code": "7", "return_ttn": "59001806649999", "based_ttn": None, "based_type": "CargoReturn"}}
sent = []
state = {"np_fail": False}
ost.send_telegram_message = lambda t: sent.append(t)
ost.fetch_order_statuses = lambda ids: {str(i): {"status": 60, "TTN": row_ttn} for i, row_ttn in (("11", "20451500000011"), ("12", "20451500000012"), ("13", "20451500000013"), ("14", "20451500000014"))}


def _np(ttns):
    if state["np_fail"]:
        raise nova_poshta.NovaPoshtaAPIError("boom")
    return {t: NP[t] for t in ttns if t in NP}


nova_poshta.get_tracking_batch = _np
for fn in ("_maybe_issue_receipt", "_maybe_advance_rozetka_processing", "_maybe_push_ttn_to_rozetka", "_maybe_push_ttn_to_prom", "_maybe_push_ttn_to_eva", "_maybe_push_delivered_to_prom",
           "_maybe_ticket_rozetka_cancelled", "_maybe_create_np_return", "_maybe_sync_eva_status", "_maybe_return_prom_eva_cancelled", "_maybe_record_observed_discount"):
    setattr(ost, fn, lambda *a, **k: None)

state["np_fail"] = True
ost.track_orders()
chk("збій НП → трекер не ламається, статуси лишаються від Toysi (shipped), алертів нема", all(row(x)["delivery_status"] == "shipped" for x in (a, b, c_, d)) and sent == [])
state["np_fail"] = False
ost.track_orders()
chk("відмова (102) → returned + алерт із ТТН і ТТН повернення", row(a)["delivery_status"] == "returned" and any("20451500000011" in m and "59001806649372" in m for m in sent))
chk("створено документ повернення (CargoReturn), статус ще «Прибув» → теж returned", row(d)["delivery_status"] == "returned")
chk("звичайні («отримано», «прямує») НЕ чіпаємо: shipped", row(b)["delivery_status"] == "shipped" and row(c_)["delivery_status"] == "shipped")
n = len(sent)
ost.track_orders()
chk("повторний цикл: returned виходить з опитування, алерт не дублюється", len(sent) == n and row(a)["delivery_status"] == "returned")
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
