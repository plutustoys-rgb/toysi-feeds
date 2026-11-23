#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_kv_tracking.py — етап Б3 модуля замовлень KIDDISVIT (23.11.2026): трекінг посилки НП у kv_order_runner (classify_np, track_shipments, _run_pipeline).
ІНВАРІАНТИ: (1) без воріт (apply=False) — НП не питаємо, нічого не пишемо; (2) docs_sent → shipped → delivered / returned за статусом НП, delivery_status дзеркалить, delivered_at ставиться;
(3) returned → ОДИН алерт; shipped/delivered — без алерту; delivered нічого грошового не чіпає (чек/оплата/повернення коштів); (4) fail-closed: НП недоступна / ТТН не знайдена / код 1,2,3 / порожній чи невідомий запис →
стан НЕ міняється; (5) без ТТН або ТТН не 14 цифр → проблема, НП не питається; (6) стан змінився між вибіркою й записом → пропущено; delivery_status чужий → не перезаписуємо;
(7) shipped лишається в get_kv_orders_pending, delivered/returned — ні; (8) не-kv замовлення не чіпаємо; (9) збій трекінгу не ламає решту конвеєра. Без мережі. `python test_kv_tracking.py` → exit 0/1.
"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
TMP = tempfile.mkdtemp(prefix="kvtrack_")
os.environ["ORDERS_DB_PATH"] = os.path.join(TMP, "orders.db")
for k in ("KV_RUNNER_APPLY", "NP_TTN_APPLY", "KV_ORDERS_LIVE", "KV_RUNNER_SINCE"):
    os.environ.pop(k, None)
sys.path.insert(0, str(HERE))
import orders_db  # noqa: E402
import kv_order_runner as kr  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


ALERTS = []
kr._alert = lambda oid, key, text, cooldown=0: ALERTS.append((oid, key, text))      # без мережі/Telegram
orders_db.init_db()
ITEMS = [{"toysi_code": "kv-100", "name": "Т", "qty": 2, "price": 250}]
_n = [0]
TTN = "20450000000001"


def new(**upd):
    _n[0] += 1
    oid = f"t{_n[0]}"
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": "site", "payment_method": "prepaid", "payment_confirmed": 0, "customer_name": "Іваненко Марія", "phone": "380971234567",
                                   "email": "b@example.com", "np_branch": "Дніпро, №28", "np_warehouse_number": "28", "np_city_ref": "city-ref", "carrier": "nova_poshta", "items": ITEMS})
        iid = f"site_{oid}"
        c.execute("UPDATE orders SET payment_confirmed=1, created_at=?, checkbox_ettn_registered_at=? WHERE internal_order_id=?", ("2026-11-21T10:00:00", "2026-11-21T10:01:00", iid))
        for k, v in upd.items():
            c.execute(f"UPDATE orders SET {k}=? WHERE internal_order_id=?", (v, iid))
        c.commit()
    return iid


def kvo(state="docs_sent", ttn=None, **upd):   # ttn=None → згенерувати; ttn="" → справді порожня
    """kv-замовлення в стані трекінгу з унікальною ТТН (14 цифр)."""
    ttn = f"2045{_n[0] + 1:010d}" if ttn is None else ttn
    return new(kv_state=state, toysi_ttn=ttn, kv_order_number="КЗ-1", kv_order_id="uuid-1", kv_ttn_ref="11111111-2222-3333-4444-555555555555", **upd), ttn


def row(iid):
    with orders_db.get_connection() as c:
        return dict(c.execute("SELECT * FROM orders WHERE internal_order_id=?", (iid,)).fetchone())


def pending_ids():
    with orders_db.get_connection() as c:
        return {o["internal_order_id"] for o in orders_db.get_kv_orders_pending(c)}


class NP:
    """Підміна НП: {ТТН: запис get_tracking_batch}; exc — виняток; hook — що виконати під час запиту (імітація гонки)."""

    def __init__(self, info=None, exc=None, hook=None):
        self.info, self.exc, self.hook, self.calls = info or {}, exc, hook, []

    def tracking_batch(self, ttns):
        self.calls.append(list(ttns))
        if self.hook:
            self.hook()
        if self.exc:
            raise self.exc
        return self.info


def rec(code, text, **kw):
    return {"status": text, "status_code": str(code), "return_ttn": kw.get("return_ttn"), "based_ttn": None, "based_type": None}


def go(iid, d, apply=True):
    with orders_db.get_connection() as c:
        o = orders_db.get_order(c, iid)
        return kr.track_shipments(c, [o], apply, d)


# ── 1. classify_np
chk("classify: 9 «Відправлення отримано» → delivered", kr.classify_np(rec(9, "Відправлення отримано")) == "delivered")
chk("classify: 11 «…отримано. Грошовий переказ видано» → delivered", kr.classify_np(rec(11, "Відправлення отримано. Грошовий переказ видано одержувачу.")) == "delivered")
chk("classify: 7 «Прибув у відділення» → shipped (не delivered)", kr.classify_np(rec(7, "Прибув у відділення")) == "shipped")
chk("classify: 5 «Прямує до міста» → shipped", kr.classify_np(rec(5, "Відправлення прямує до міста одержувача")) == "shipped")
chk("classify: слово «отримано» при коді не-вручення (7) → shipped, не delivered", kr.classify_np(rec(7, "Відправлення отримано відправником")) == "shipped")
chk("classify: 102 «Відмова одержувача» → returned", kr.classify_np(rec(102, "Відмова одержувача")) == "returned")
chk("classify: документ повернення (return_ttn) → returned, навіть якщо текст «отримано»", kr.classify_np(rec(9, "Відправлення отримано", return_ttn="20450000009999")) == "returned")
chk("classify: «Відправлення повертається» → returned", kr.classify_np(rec(103, "Відправлення повертається")) == "returned")
chk("classify: коди 1/2/3 → None (ще не в дорозі)", all(kr.classify_np(rec(c, "Номер не знайдено")) is None for c in (1, 2, 3)))
chk("classify: порожній код / порожній текст / None / не словник → None", kr.classify_np(rec("", "Прибув")) is None and kr.classify_np(rec(7, "")) is None
    and kr.classify_np(None) is None and kr.classify_np("x") is None and kr.classify_np({}) is None)

# ── 2. ворота: apply=False — НП не питаємо, стан не міняємо
a, ttn_a = kvo("docs_sent")
d = NP({ttn_a: rec(9, "Відправлення отримано")})
rep = go(a, d, apply=False)
chk("apply=False: НП не викликана, звіту нема, стан docs_sent без змін", d.calls == [] and rep == [] and row(a)["kv_state"] == "docs_sent" and not row(a)["delivery_status"])

# ── 3. docs_sent → shipped → delivered (без алерту, без грошових полів)
before = row(a)
d = NP({ttn_a: rec(7, "Прибув у відділення")})
rep = go(a, d)
r = row(a)
chk("docs_sent → shipped, delivery_status=shipped, один запит НП з нашою ТТН", r["kv_state"] == "shipped" and r["delivery_status"] == "shipped" and d.calls == [[ttn_a]] and not rep[0]["problems"])
chk("shipped: delivered_at порожній, алерту немає", not r["delivered_at"] and not ALERTS)
chk("shipped лишається в роботі (get_kv_orders_pending)", a in pending_ids())
d = NP({ttn_a: rec(7, "Прибув у відділення")})
rep = go(a, d)
chk("повтор того самого статусу → без запису (kv_updated_at не змінився)", row(a)["kv_updated_at"] == r["kv_updated_at"] and rep[0]["actions"] == [])
d = NP({ttn_a: rec(9, "Відправлення отримано")})
go(a, d)
r = row(a)
chk("shipped → delivered: kv_state/delivery_status=delivered, delivered_at проставлено", r["kv_state"] == "delivered" and r["delivery_status"] == "delivered" and r["delivered_at"])
chk("delivered виходить із get_kv_orders_pending", a not in pending_ids())
chk("delivered без алерту", not ALERTS)
money = ("payment_method", "payment_confirmed", "checkbox_ettn_registered_at", "refund_receipt_at", "refund_receipt_claim_at", "cancelled_at", "status", "total", "toysi_ttn", "kv_order_number", "kv_order_id", "kv_ttn_ref")
chk("delivered: чек/оплата/повернення/скасування/ТТН/номери замовлення — без змін", all(before.get(k) == r.get(k) for k in money if k in r))

# ── 4. docs_sent → delivered напряму (пропущений shipped)
b, ttn_b = kvo("docs_sent")
go(b, NP({ttn_b: rec(10, "Відправлення отримано")}))
chk("docs_sent → delivered напряму", row(b)["kv_state"] == "delivered")

# ── 5. returned: стан, kv_error, один алерт, вихід із черги
ALERTS.clear()
c_, ttn_c = kvo("shipped", delivery_status="shipped")
go(c_, NP({ttn_c: rec(102, "Відмова одержувача", return_ttn="20450000008888")}))
r = row(c_)
chk("shipped → returned: kv_state/delivery_status=returned, у kv_error статус і ТТН повернення", r["kv_state"] == "returned" and r["delivery_status"] == "returned" and "20450000008888" in str(r["kv_error"]))
chk("returned: рівно один алерт «returned» із ТТН; текст не обіцяє автодій", len(ALERTS) == 1 and ALERTS[0][1] == "returned" and ttn_c in ALERTS[0][2] and "автоматично нічого не робиться" in ALERTS[0][2])
chk("returned виходить із get_kv_orders_pending", c_ not in pending_ids())
go(c_, NP({ttn_c: rec(102, "Відмова одержувача")}))
chk("повторний прогін returned — алерту вдруге немає", len(ALERTS) == 1)
e, ttn_e = kvo("docs_sent")
go(e, NP({ttn_e: rec(102, "Відмова одержувача")}))
chk("docs_sent → returned напряму", row(e)["kv_state"] == "returned" and len(ALERTS) == 2)

# ── 6. fail-closed
ALERTS.clear()
f1, t1 = kvo("docs_sent")
rep = go(f1, NP(exc=RuntimeError("НП недоступна")))
chk("НП кидає виняток → стан без змін, проблем/алертів немає (повтор наступного циклу)", row(f1)["kv_state"] == "docs_sent" and not rep[0]["problems"] and not ALERTS)
rep = go(f1, NP({}))
chk("ТТН немає у відповіді НП → без змін", row(f1)["kv_state"] == "docs_sent" and not rep[0]["problems"])
rep = go(f1, NP({t1: rec(3, "Номер не знайдено")}))
chk("НП: код 3 → без змін", row(f1)["kv_state"] == "docs_sent")
rep = go(f1, NP({t1: rec(1, "Відправник самостійно створив цю накладну")}))
chk("НП: код 1 → без змін", row(f1)["kv_state"] == "docs_sent")
rep = go(f1, NP({t1: {"status": "", "status_code": ""}}))
chk("НП: порожній запис → без змін", row(f1)["kv_state"] == "docs_sent")
rep = go(f1, NP(["не", "словник"]))
chk("НП: відповідь не словник → без змін, без винятку", row(f1)["kv_state"] == "docs_sent")
chk("fail-closed: жодного алерту", not ALERTS)

# ── 7. ТТН відсутня / некоректна
g, _ = kvo("docs_sent", ttn="")
g2, _ = kvo("shipped", ttn="12345")
d = NP()
with orders_db.get_connection() as c:
    reps = kr.track_shipments(c, [orders_db.get_order(c, g), orders_db.get_order(c, g2)], True, d)
chk("порожня/некоректна ТТН: проблема в звіті, НП не питається, стани без змін", d.calls == [] and all(x["problems"] for x in reps) and row(g)["kv_state"] == "docs_sent" and row(g2)["kv_state"] == "shipped")

# ── 8. гонка і чужий delivery_status
h, th = kvo("docs_sent")


def _race():
    with orders_db.get_connection() as c:
        c.execute("UPDATE orders SET kv_state='manual' WHERE internal_order_id=?", (h,))
        c.commit()


rep = go(h, NP({th: rec(9, "Відправлення отримано")}, hook=_race))
chk("стан змінився на manual під час запиту → трекінг НЕ перезаписує", row(h)["kv_state"] == "manual" and not row(h)["delivery_status"])
i_, ti = kvo("docs_sent", delivery_status="packed")
rep = go(i_, NP({ti: rec(9, "Відправлення отримано")}))
chk("чужий delivery_status (packed) не перезаписується, проблема в звіті", row(i_)["kv_state"] == "docs_sent" and row(i_)["delivery_status"] == "packed" and rep[0]["problems"])

# ── 9. не-kv і інші стани не чіпаємо
other = new()                                                  # звичайне site-замовлення без kv_state
nonk = new(kv_state="docs_ready", toysi_ttn="20450000000777")  # стан не трекінговий
snap_o, snap_n = row(other), row(nonk)
d = NP({"20450000000777": rec(9, "Відправлення отримано")})
with orders_db.get_connection() as c:
    reps = kr.track_shipments(c, [orders_db.get_order(c, other), orders_db.get_order(c, nonk)], True, d)
chk("лише docs_sent/shipped: замовлення без kv_state і docs_ready не беруться, НП не питається", reps == [] and d.calls == [] and row(other) == snap_o and row(nonk) == snap_n)

# ── 10. _run_pipeline: ворота й ізоляція
j, tj = kvo("docs_sent")
d = NP({tj: rec(7, "Прибув у відділення")})
kr._run_pipeline(False, d)
chk("_run_pipeline(apply=False): НП не питається, стан без змін", d.calls == [] and row(j)["kv_state"] == "docs_sent")
rep = kr._run_pipeline(True, d)
chk("_run_pipeline(apply=True): docs_sent → shipped", row(j)["kv_state"] == "shipped" and any(x.get("tracking") for x in rep))
chk("_run_pipeline: НП питається один раз за цикл усіма ТТН пакетом", len(d.calls) == 1 and tj in d.calls[0])


class Boom(NP):
    def tracking_batch(self, ttns):
        raise MemoryError("збій")


k_, tk = kvo("docs_sent")
try:
    kr._run_pipeline(True, Boom())
    ok = True
except BaseException:  # noqa: BLE001
    ok = False
chk("збій трекінгу (MemoryError у НП-шарі) не валить цикл", ok and row(k_)["kv_state"] == "docs_sent")

# ── 11. ворота run(): без env — dry-run (apply_enabled закрито)
d = NP({tj: rec(9, "Відправлення отримано")})
kr.run(deps=d)
chk("run() без воріт: НП не питається, shipped лишається shipped", d.calls == [] and row(j)["kv_state"] == "shipped")
os.environ.update({"KV_RUNNER_APPLY": "1", "NP_TTN_APPLY": "1", "KV_ORDERS_LIVE": "1", "KV_RUNNER_SINCE": "2026-11-21T00:00:00"})
kr.run(deps=d)
chk("run() з усіма воротами: shipped → delivered", row(j)["kv_state"] == "delivered")
for k in ("KV_RUNNER_APPLY", "NP_TTN_APPLY", "KV_ORDERS_LIVE", "KV_RUNNER_SINCE"):
    os.environ.pop(k, None)

print("\nПідсумок:", "УСЕ ЗЕЛЕНЕ" if not F else f"ПРОВАЛИ: {F}")
sys.exit(1 if F else 0)
