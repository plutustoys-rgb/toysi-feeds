#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_kv_order_runner.py — етап Б2 модуля замовлень KIDDISVIT (21.11.2026): kv_order_runner.py.
ІНВАРІАНТИ: (1) без воріт (KV_RUNNER_APPLY/NP_TTN_APPLY/KV_ORDERS_LIVE/SINCE) — DRY-RUN: нічого не створюється, стан НЕ міняється; (2) повний шлях apply: NULL → kv_created → ttn_created → docs_ready → docs_sent,
номер КЗ-… іде в ТТН (AdditionalInformation/InfoRegClientBarcodes); (3) orders/create викликається РІВНО раз; невизначений збій → kv_unknown і ніколи не повторюється; визначена відмова → claim повертається;
(4) status_OK=0 → manual без ТТН; (5) живий залишок недостатній / замовлення повернене між pending і create / чек не видано → create НЕ викликається; (6) збій створення ТТН → ttn_creating (STUCK), повтору немає;
(7) стани STUCK не дають жодного виклику create/ТТН; (8) ttn-data неоднозначний → docs_ready лишається; (9) старіші за SINCE без стану не беруться. Без мережі. `python test_kv_order_runner.py` → exit 0/1.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
TMP = tempfile.mkdtemp(prefix="kvrun_")
os.environ["ORDERS_DB_PATH"] = os.path.join(TMP, "orders.db")
for k in ("KV_RUNNER_APPLY", "NP_TTN_APPLY", "KV_ORDERS_LIVE", "KV_RUNNER_SINCE"):
    os.environ.pop(k, None)
sys.path.insert(0, str(HERE))
import orders_db  # noqa: E402
import kv_order_runner as kr  # noqa: E402
import kiddisvit_api as api  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


ALERTS = []
kr._alert = lambda oid, key, text, cooldown=0: ALERTS.append((oid, key))     # без мережі/Telegram (інакше тест повільний)
orders_db.init_db()
SUP = {"kv-100": {"cost": 100.0, "warehouse": "Dnepr", "stock": 50, "name": "Фігурка", "condition": "new"}}
ITEMS = [{"toysi_code": "kv-100", "name": "Т", "qty": 2, "price": 250}]
_n = [0]


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


def row(iid):
    with orders_db.get_connection() as c:
        return dict(c.execute("SELECT * FROM orders WHERE internal_order_id=?", (iid,)).fetchone())


class Deps:
    def __init__(self, rest=10, create=None, ttn_live_exc=None, push=None, search=None, rest_map=None, ttn_res=None):
        self.calls = []
        self.rest, self.create, self.ttn_live_exc, self.push, self.search = rest, create, ttn_live_exc, push, search
        self.extra_seen = []
        self.rest_map, self.ttn_res = rest_map, ttn_res

    def ids(self):
        return {"OrganizationID": "org", "ClientID": "cl", "ContractID": "co", "shopID": "sh"}

    def lookup(self, city, num):
        return "wh-ref"

    def rests(self):
        self.calls.append("rests")
        return self.rest_map if self.rest_map is not None else {"100": {"Dnepr": self.rest}}

    def create_order(self, payload):
        self.calls.append("create")
        if isinstance(self.create, Exception):
            raise self.create
        return self.create or {"number": "КЗ-1", "id": "uuid-1", "status_OK": 1, "Comment": ""}

    def search_orders(self, ids, a, b):
        self.calls.append("search")
        return self.search or []

    def ttn_dry_run(self, order, extra):
        self.calls.append("ttn_dry")
        return {"dry_run": True}

    def create_ttn(self, order, extra):
        self.calls.append("ttn_live")
        self.extra_seen.append(extra)
        if self.ttn_live_exc:
            raise self.ttn_live_exc
        if self.ttn_res is not None:
            return self.ttn_res
        return {"number": "20450000000001", "ref": "11111111-2222-3333-4444-555555555555"}

    def save_docs(self, ref, oid):
        self.calls.append("docs")
        p1, p2 = Path(TMP) / f"{oid}_m.pdf", Path(TMP) / f"{oid}_d.pdf"
        p1.write_bytes(b"%PDF-m")
        p2.write_bytes(b"%PDF-d")
        return {"marking": str(p1), "document": str(p2)}

    def push_docs(self, kv_id, paths):
        self.calls.append("push")
        if isinstance(self.push, Exception):
            raise self.push
        return self.push if self.push is not None else [{"id": kv_id, "status": "success", "errorMessage": ""}]


def step(iid, apply, d, n=1):
    rep = None
    for _ in range(n):
        with orders_db.get_connection() as c:
            o = orders_db.get_order(c, iid)
            rep = kr.process_order(c, o, SUP, apply, d)
    return rep


# ── 1. gates
chk("apply_enabled: без воріт закрито", kr.apply_enabled()[0] is False)
os.environ.update({"KV_RUNNER_APPLY": "1", "NP_TTN_APPLY": "1", "KV_ORDERS_LIVE": "1"})
chk("apply_enabled: ворота без SINCE закрито", kr.apply_enabled()[0] is False)
os.environ["KV_RUNNER_SINCE"] = "не-дата"
chk("apply_enabled: SINCE не ISO закрито", kr.apply_enabled()[0] is False)
os.environ["KV_RUNNER_SINCE"] = "2026-11-21T00:00:00"
chk("apply_enabled: усе задано → відкрито", kr.apply_enabled()[0] is True)
os.environ["NP_TTN_APPLY"] = "0"
chk("apply_enabled: NP_TTN_APPLY≠1 → закрито", kr.apply_enabled()[0] is False)
for k in ("KV_RUNNER_APPLY", "NP_TTN_APPLY", "KV_ORDERS_LIVE", "KV_RUNNER_SINCE"):
    os.environ.pop(k, None)

# ── 2. dry-run
a = new()
d = Deps()
rep = step(a, False, d)
chk("dry-run: create/ТТН НЕ викликані, стан NULL", "create" not in d.calls and "ttn_live" not in d.calls and row(a)["kv_state"] is None and not rep["problems"])

# ── 3. повний шлях
d = Deps()
rep = step(a, True, d)
r = row(a)
chk("apply: create викликано рівно раз, стан дійшов до docs_sent", d.calls.count("create") == 1 and r["kv_state"] == "docs_sent" and r["kv_order_number"] == "КЗ-1" and r["kv_order_id"] == "uuid-1")
chk("apply: ТТН створено з номером КЗ-… у двох полях", d.extra_seen == [{"AdditionalInformation": "КЗ-1", "InfoRegClientBarcodes": "КЗ-1"}] and r["toysi_ttn"] == "20450000000001" and r["kv_ttn_ref"])
chk("apply: документи збережені й передані", "docs" in d.calls and "push" in d.calls and json.loads(r["kv_docs_path"]).keys() >= {"marking", "document"})
rep = step(a, True, d)
chk("docs_sent: повторний запуск нічого не робить", d.calls.count("create") == 1 and d.calls.count("ttn_live") == 1 and not rep["problems"])

# ── 4. невизначений збій create
b = new()
d = Deps(create=api.KiddisvitUncertain("таймаут"))
rep = step(b, True, d)
chk("create Uncertain → kv_unknown, create був 1 раз", row(b)["kv_state"] == "kv_unknown" and d.calls.count("create") == 1 and rep["problems"])
d2 = Deps()
step(b, True, d2, n=3)
chk("kv_unknown: повторні запуски НЕ викликають create/ТТН", "create" not in d2.calls and "ttn_live" not in d2.calls and row(b)["kv_state"] == "kv_unknown")
d2s = Deps(search=[{"number": "КЗ-77", "id": "u77", "Comment": f"Сайт, замовлення {b}"}])
rep = step(b, True, d2s)
chk("kv_unknown: reconcile лише підказка, стан не змінено", row(b)["kv_state"] == "kv_unknown" and "create" not in d2s.calls and "КЗ-77" in rep["problems"][0])

# ── 5. визначена відмова
c_ = new()
d = Deps(create=api.KiddisvitError("orders/create: HTTP 422"))
step(c_, True, d)
chk("визначена відмова → claim повернено (kv_state NULL), kv_error записано", row(c_)["kv_state"] is None and row(c_)["kv_error"])

# ── 6. не зарезервовано
e = new()
d = Deps(create={"number": "КЗ-2", "id": "u2", "status_OK": 0, "Comment": "нема товару"})
step(e, True, d)
chk("status_OK=0 → manual, ТТН НЕ створено", row(e)["kv_state"] == "manual" and "ttn_live" not in d.calls and row(e)["kv_order_number"] == "КЗ-2")

# ── 7. залишок, повернення, чек
f = new()
d = Deps(rest=1)
rep = step(f, True, d)
chk("залишок < кількості → create НЕ викликано", "create" not in d.calls and row(f)["kv_state"] is None and rep["problems"])
g = new(checkbox_ettn_registered_at=None)
d = Deps()
step(g, True, d)
chk("чек не видано → create НЕ викликано", "create" not in d.calls)
h = new()
with orders_db.get_connection() as c:
    stale = orders_db.get_order(c, h)
    c.execute("UPDATE orders SET refund_receipt_claim_at='2026-11-21T11:00:00' WHERE internal_order_id=?", (h,))
    c.commit()
d = Deps()
with orders_db.get_connection() as c:
    kr.process_order(c, stale, SUP, True, d)
chk("повернення між вибіркою і create (свіжа перевірка) → create НЕ викликано, стан NULL", "create" not in d.calls and row(h)["kv_state"] is None)

# ── 8. збій ТТН
i = new()
d = Deps(ttn_live_exc=RuntimeError("мережа"))
step(i, True, d)
chk("збій live-ТТН → ttn_creating (STUCK)", row(i)["kv_state"] == "ttn_creating" and d.calls.count("create") == 1)
d2 = Deps()
step(i, True, d2, n=2)
chk("ttn_creating: ТТН і create НЕ повторюються", "ttn_live" not in d2.calls and "create" not in d2.calls and row(i)["kv_state"] == "ttn_creating")

# ── 9. ttn-data неоднозначний
j = new()
d = Deps(push=[{"id": "x", "status": "error", "errorMessage": "bad pdf"}])
step(j, True, d)
chk("ttn-data з помилкою → docs_ready лишається", row(j)["kv_state"] == "docs_ready")
d = Deps(push=[])
step(j, True, d)
chk("ttn-data порожня відповідь → docs_ready лишається", row(j)["kv_state"] == "docs_ready")
d = Deps()
step(j, True, d)
chk("ttn-data успіх при повторі → docs_sent, create/ТТН не повторені", row(j)["kv_state"] == "docs_sent" and "create" not in d.calls and "ttn_live" not in d.calls)
chk("docs_push_ok: помилка/None/порожнє → False", not kr.docs_push_ok([{"status": "ok", "errorMessage": "x"}]) and not kr.docs_push_ok([{"status": None}]) and not kr.docs_push_ok([]) and kr.docs_push_ok([{"status": "success", "errorMessage": ""}]))

# ── 10. SINCE у конвеєрі
k = new()
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET created_at='2026-11-20T10:00:00' WHERE internal_order_id=?", (k,))
    c.commit()
os.environ["KV_RUNNER_SINCE"] = "2026-11-21T00:00:00"
import kiddisvit_orders as ko  # noqa: E402
ko.load_supplier = lambda path=None: SUP
d = Deps()
reps = kr._run_pipeline(False, d)
chk("SINCE: старіше без стану не береться", all(r.get("order") != k for r in reps))
os.environ.pop("KV_RUNNER_SINCE", None)


# ── 11. (аудит #1946) ліміт спроб, вже є ТТН, формат залишку, відповідь ТТН, ttn-data
r1 = new()
d = Deps(create=api.KiddisvitError("orders/create: HTTP 422"))
step(r1, True, d)
step(r1, True, d)
chk("відмова create x2: стан NULL, kv_error має лічильник [n=2], create викликано 2 рази", row(r1)["kv_state"] is None and str(row(r1)["kv_error"]).startswith("[n=2]") and d.calls.count("create") == 2)
step(r1, True, d)
chk("відмова create x3 -> manual, create викликано РІВНО 3 рази", row(r1)["kv_state"] == "manual" and d.calls.count("create") == 3)
step(r1, True, d)
chk("після manual замовлення не береться знову (create лишається 3)", d.calls.count("create") == 3)
r2 = new(toysi_ttn="20450000009999")
d = Deps()
rep = step(r2, True, d)
chk("вже є toysi_ttn (оформлено вручну) -> create НЕ викликано, стан NULL", "create" not in d.calls and row(r2)["kv_state"] is None and rep["problems"])
r3 = new()
d = Deps(rest_map={"100": {"dnepr": "10.0"}})
step(r3, True, d)
chk("залишок '10.0' і склад «dnepr» (регістр) приймається -> create викликано", d.calls.count("create") == 1)
r4 = new()
d = Deps(rest_map={"100": {"Dnepr": "n/a"}})
rep = step(r4, True, d)
chk("залишок не число -> створення НЕ відбулось", "create" not in d.calls and rep["problems"])
r5 = new()
d = Deps(ttn_res={"dry_run": True, "payload": {}})
rep = step(r5, True, d)
chk("create_ttn повернув dry-run/без Ref -> ttn_creating (STUCK), не ttn_created", row(r5)["kv_state"] == "ttn_creating" and rep["problems"])
r6 = new()
d = Deps(ttn_live_exc=RuntimeError("мережа"))
rep = step(r6, True, d)
chk("збій live-ТТН: текст проблеми каже НЕВІДОМИЙ", "НЕВІДОМИЙ" in rep["problems"][0])
r7 = new()
d = Deps(push=api.KiddisvitUncertain("таймаут"))
step(r7, True, d)
step(r7, True, d)
chk("ttn-data Uncertain x2: docs_ready, лічильник [n=2]", row(r7)["kv_state"] == "docs_ready" and str(row(r7)["kv_error"]).startswith("[n=2]"))
step(r7, True, d)
chk("ttn-data без підтвердження x3 -> manual, create/ТТН лишились по 1", row(r7)["kv_state"] == "manual" and d.calls.count("create") == 1 and d.calls.count("ttn_live") == 1 and d.calls.count("push") == 3)


# ── 12. (аудит #1948) застарілий лічильник, збереження лічильника, ліміт для винятку не з KIDDISVIT, номер ТТН, залишок
t1 = new()
d = Deps(create=api.KiddisvitError("orders/create: HTTP 422"))
step(t1, True, d)
step(t1, True, d)
d.create = None
d.push = api.KiddisvitUncertain("таймаут")
step(t1, True, d)
chk("create відхилено x2, на 3-му запуску create пройшов, перший збій ttn-data = спроба 1 (не manual): лічильник не переноситься між кроками", row(t1)["kv_state"] == "docs_ready" and str(row(t1)["kv_error"]).startswith("[n=1]"))
t2 = new()
d = Deps(create=api.KiddisvitError("orders/create: HTTP 422"))
step(t2, True, d)
step(t2, True, d)
d.rest = 1
step(t2, True, d)
chk("проміжна проблема (залишок) не скидає накопичений лічильник [n=2]", str(row(t2)["kv_error"]).startswith("[n=2]") and row(t2)["kv_state"] is None)
t3 = new()
d = Deps(push=OSError("disk"))
step(t3, True, d)
step(t3, True, d)
chk("ttn-data: виняток не з KIDDISVIT x2 — ще docs_ready", row(t3)["kv_state"] == "docs_ready")
step(t3, True, d)
chk("ttn-data: виняток не з KIDDISVIT x3 → manual (ліміт діє й для загального винятку)", row(t3)["kv_state"] == "manual" and d.calls.count("push") == 3)
t4 = new()
d = Deps(push=api.KiddisvitUncertain("таймаут"))
step(t4, True, d)
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET kv_docs_path=? WHERE internal_order_id=?", (json.dumps({"marking": "/нема/a.pdf", "document": "/нема/b.pdf"}), t4))
    c.commit()
step(t4, True, d)
chk("PDF зникли: спроба 2 — ще docs_ready, push не викликано", row(t4)["kv_state"] == "docs_ready" and d.calls.count("push") == 1)
step(t4, True, d)
chk("PDF зникли: спроба 3 → manual (не безкінечний повтор), push так і не викликано повторно", row(t4)["kv_state"] == "manual" and d.calls.count("push") == 1)
t5 = new()
d = Deps(ttn_res={"number": "20450000007777", "ref": "не-guid"})
rep = step(t5, True, d)
chk("відповідь ТТН з номером, але не-GUID Ref: номер у тексті проблеми (для ручної звірки), стан ttn_creating", "20450000007777" in rep["problems"][0] and row(t5)["kv_state"] == "ttn_creating")
chk("_rest_qty: inf / від'ємне / не число → None; '7.0' → 7.0", kr._rest_qty({"100": {"Dnepr": "inf"}}, 100, "Dnepr") is None and kr._rest_qty({"100": {"Dnepr": -5}}, 100, "Dnepr") is None
    and kr._rest_qty({"100": {"Dnepr": "n/a"}}, 100, "Dnepr") is None and kr._rest_qty({"100": {"Dnepr": "7.0"}}, 100, "Dnepr") == 7.0)


class FakeClient:
    def rests(self):
        return [{"ProductID": 100, "rest": [{"WarehouseID": "Dnepr", "Rest": 3}, {"WarehouseID": "Dnepr", "Rest": 4}, {"WarehouseID": "NTV20", "Rest": "inf"}, {"WarehouseID": "NTV20", "Rest": -2}]},
                {"ProductID": 100, "rest": [{"WarehouseID": "Dnepr", "Rest": 5}]},
                {"ProductID": 200, "rest": [{"WarehouseID": "Dnepr", "Rest": 1}]}]


dd = kr._Deps.__new__(kr._Deps)
dd.client = FakeClient()
rr = dd.rests()
chk("_Deps.rests: рядки одного складу в одному товарі додаються (3+4=7), дубль ProductID — більше значення (max 7, а не 12), inf і від'ємне відкинуті", rr["100"].get("Dnepr") == 7.0 and "NTV20" not in rr["100"] and rr["200"]["Dnepr"] == 1.0)

print("\nПідсумок:", "УСЕ ЗЕЛЕНЕ" if not F else f"ПРОВАЛИ: {F}")
sys.exit(1 if F else 0)
