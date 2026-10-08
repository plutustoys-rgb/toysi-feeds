# -*- coding: utf-8 -*-
"""Накладений платіж НП = цілі гривні, чек Checkbox на ту саму суму (запит головного бухгалтера 06–07.10.2026).
Приклади з бойових даних: Prom №416114712 (39,23 → НП взяла 39,00), Prom №416236076 (175,68 → НП: 175), ціла ціна 121 — без змін.
`python test_cod_amount.py` → exit 0/1, мережа не потрібна."""
import os
import sys
import sqlite3
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")

import cod_amount as ca
import order_router as orr
import order_status_tracker as ost

orr.settlement_raion = lambda *a, **k: ""
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


# ── 1. collected_cod_amount: підлога до цілих гривень ──
chk("39,23 → 39; 175,68 → 175 (саме підлога, не «до найближчого»); 121 → 121; 330,34 → 330; 206,62 → 206",
    [ca.collected_cod_amount(x) for x in (39.23, 175.68, 121, 330.34, 206.62)] == [39.0, 175.0, 121.0, 330.0, 206.0])
chk("float-шум не з'їдає гривню: 41.0000000001 → 41; 40.999999999 (=41.00 після округлення до копійок) → 41", ca.collected_cod_amount(41.0000000001) == 41.0 and ca.collected_cod_amount(40.999999999) == 41.0)
chk("порожнє/None → 0", ca.collected_cod_amount(None) == 0.0 and ca.collected_cod_amount(0) == 0.0)
chk("order_total: 2 знаки", ca.order_total([{"price": 19.99, "qty": 3}, {"price": 0.1, "qty": 3}]) == 60.27)

# ── 2. fit_goods_to_total: сума рядків = сума оплати до копійки ──
def kop(goods):
    return sum(round(g["price"] * 100) * g.get("qty", 1) for g in goods)

g1 = [{"code": "1", "name": "A", "price": 39.23, "qty": 1}]
r1 = ca.fit_goods_to_total(g1, 39.0)
chk("один рядок qty=1: ціна 39,23 → 39,00, сума 3900 коп.", kop(r1) == 3900 and r1[0]["price"] == 39.0 and len(r1) == 1)
chk("вхід не мутовано", g1[0]["price"] == 39.23)
g2 = [{"code": "1", "name": "A", "price": 87.89, "qty": 2}]
r2 = ca.fit_goods_to_total(g2, 175.0)             # 175,78 → 175,00: різниця 78 коп.
chk("qty=2: розщеплено на 1×87,89 + 1×87,11, сума 17500 коп., назва/код збережені",
    kop(r2) == 17500 and [(g["qty"], g["price"]) for g in r2] == [(1, 87.89), (1, 87.11)] and all(g["name"] == "A" and g["code"] == "1" for g in r2))
g3 = [{"code": "a", "name": "A", "price": 50.10, "qty": 1}, {"code": "b", "name": "B", "price": 120.55, "qty": 3}]
r3 = ca.fit_goods_to_total(g3, ca.collected_cod_amount(ca.order_total([{"price": 50.10, "qty": 1}, {"price": 120.55, "qty": 3}])))  # 411,75 → 411
chk("кілька рядків: знижується найдорожчий (B розщеплено 2×120,55 + 1×119,80), A не чіпається, сума = 411,00",
    kop(r3) == 41100 and [g["price"] for g in r3 if g["code"] == "a"] == [50.10] and sorted(g["price"] for g in r3 if g["code"] == "b") == [119.80, 120.55])
chk("ціла сума — без змін (копія)", ca.fit_goods_to_total([{"code": "x", "name": "x", "price": 121.0, "qty": 1}], 121.0) == [{"code": "x", "name": "x", "price": 121.0, "qty": 1}])
for bad, why in ((250.0, "ціль більша за суму"), (36.0, "різниця ≥ 1 грн")):
    try:
        ca.fit_goods_to_total(g1, bad); ok = False
    except ValueError:
        ok = True
    chk(f"ValueError, а не мовчазна підгонка: {why}", ok)

# вижилі мутанти аудиту #640: (а) ціна одиниці після заокруглення < 1 ₴ → ValueError; (б) diff==0 при qty>1 → без розщеплення
try:
    ca.fit_goods_to_total([{"code": "c", "name": "c", "price": 1.40, "qty": 1}, {"code": "d", "name": "d", "price": 1.40, "qty": 1}], 2.0); ok2 = False   # 2,80 → 2,00: найдорожча 1,40−0,80 = 0,60 < 1 ₴
except ValueError:
    ok2 = True
chk("ValueError, коли ціна одиниці після заокруглення впала б нижче 1 ₴ (2×1,40 → 2,00)", ok2)
rq = ca.fit_goods_to_total([{"code": "q", "name": "q", "price": 40.0, "qty": 3}], 120.0)
chk("diff==0 і qty>1: рядок НЕ розщеплюється (одна позиція qty=3)", len(rq) == 1 and rq[0]["qty"] == 3 and rq[0]["price"] == 40.0)

# ── 3. build_toysi_order: moneyback ціле лише для НП+COD ──
def _order(**kw):
    base = dict(internal_order_id="t_1", order_id="1", platform="prom", carrier="nova_poshta", customer_name="Іваненко Іван Іванович",
                phone="+380508581429", payment_method="cod", items=[{"toysi_code": "1", "name": "x", "qty": 1, "price": 39.23}],
                np_branch="Харків (Харківська обл.), Відділення №65", np_warehouse_number="65", np_city_ref="db5c88e0-391c-11dd-90d9-001a92567626")
    base.update(kw)
    return base

chk("НП + COD: moneyback 39,23 → 39.0 (те, що НП реально збере)", orr.build_toysi_order(_order())["moneyback"] == 39.0)
chk("НП + COD, кілька позицій: 2×87,89 + 10,50 = 186,28 → 186", orr.build_toysi_order(_order(items=[{"toysi_code": "1", "name": "x", "qty": 2, "price": 87.89}, {"toysi_code": "2", "name": "y", "qty": 1, "price": 10.50}]))["moneyback"] == 186.0)
chk("НП + COD, ціла сума: 121 → 121", orr.build_toysi_order(_order(items=[{"toysi_code": "1", "name": "x", "qty": 1, "price": 121}]))["moneyback"] == 121.0)
chk("передоплата (prepaid): moneyback 0", orr.build_toysi_order(_order(payment_method="prepaid"))["moneyback"] == 0.0)
chk("Rozetka Delivery + COD: БЕЗ змін (копійки лишаються)", orr.build_toysi_order(_order(carrier="rozetka_delivery"))["moneyback"] == 39.23)

# ── 4. _maybe_issue_receipt: чек = сума, яку НП фактично зібрала ──
calls = []
ost.create_receipt = lambda **kw: (calls.append(kw) or {"id": "rcpt-1"})
ost.mark_checkbox_ettn_registered = lambda conn, oid, rid: calls.append(("mark", oid, rid))
ost.nova_poshta.get_tracking_status = lambda ttn: {"delivered": True}

def issue(order, ttn="2045"):
    calls.clear()
    ost._maybe_issue_receipt(None, order, ttn)
    return [c for c in calls if isinstance(c, dict)]

o = _order(internal_order_id="prom_416114712")
r = issue(o)
chk("COD+НП: чек на 39,00 (а не 39,23), рядки чека в сумі = total_amount", len(r) == 1 and r[0]["total_amount"] == 39.0 and round(sum(g["price"] * g["qty"] for g in r[0]["goods"]), 2) == 39.0 and r[0]["payment_type"] == "CASH")
o2 = _order(internal_order_id="prom_416236076", items=[{"toysi_code": "9", "name": "z", "qty": 1, "price": 175.68}])
r = issue(o2)
chk("COD+НП: 175,68 → чек 175,00", r[0]["total_amount"] == 175.0 and round(sum(g["price"] * g["qty"] for g in r[0]["goods"]), 2) == 175.0)
r = issue(_order(internal_order_id="eva_1", platform="eva", items=[{"toysi_code": "3", "name": "w", "qty": 3, "price": 69.34}]))      # 208,02 → 208
chk("COD+НП, qty=3: 208,02 → 208,00, рядок розщеплено, сума збігається", r[0]["total_amount"] == 208.0 and round(sum(g["price"] * g["qty"] for g in r[0]["goods"]), 2) == 208.0 and len(r[0]["goods"]) == 2)
r = issue(_order(internal_order_id="prom_2", items=[{"toysi_code": "1", "name": "x", "qty": 1, "price": 121}]))
chk("COD+НП, ціла ціна: чек 121 без змін, один рядок", r[0]["total_amount"] == 121 and len(r[0]["goods"]) == 1)
r = issue(_order(internal_order_id="prom_3", payment_method="prepaid", payment_confirmed=True))
chk("prepaid (CASHLESS): сума з копійками БЕЗ змін — гроші отримано в повному обсязі", r and r[0]["payment_type"] == "CASHLESS" and r[0]["total_amount"] == 39.23)
ost.rozetka_client.is_order_done = lambda oid: True
r = issue(_order(internal_order_id="roz_1", platform="rozetka", carrier="rozetka_delivery"))
chk("Rozetka Delivery COD: БЕЗ змін (39,23) — механіка збору інша, не НП", r and r[0]["total_amount"] == 39.23)

# ── 5. РЕАЛЬНИЙ create_receipt (мережа замінена): тіло запиту Checkbox узгоджене — payments.value = сума рядків = ціла гривня ──
import checkbox_client as cbc
bodies = []
cbc._require_receipt_credentials = lambda: None
cbc._authenticate_cashier = lambda: "tok"
cbc._ensure_shift_open = lambda tok: None
class _R:
    def raise_for_status(self): pass
    def json(self): return {"id": "r"}
cbc.requests.post = lambda url, **kw: (bodies.append(kw["json"]) or _R())
ost.create_receipt = cbc.create_receipt          # справжня функція; marks/трекінг замінені вище
calls.clear()
ost._maybe_issue_receipt(None, _order(internal_order_id="prom_416114712"), "2045")
b = bodies[-1]
chk("справжній create_receipt: payments.value = 3900 коп., сума рядків у тілі = 3900 коп. (Checkbox не відхилить за розбіжністю)",
    b["payments"][0]["value"] == 3900 and sum(g["good"]["price"] * g["quantity"] // 1000 for g in b["goods"]) == 3900)
bodies.clear(); calls.clear()
ost._maybe_issue_receipt(None, _order(internal_order_id="eva_9", platform="eva", items=[{"toysi_code": "3", "name": "w", "qty": 3, "price": 69.34}]), "2045")
b = bodies[-1]
chk("справжній create_receipt, qty=3: 20800 коп., два рядки (qty 2 і 1), кількості в тисячних частках",
    b["payments"][0]["value"] == 20800 and sorted(g["quantity"] for g in b["goods"]) == [1000, 2000]
    and sum(g["good"]["price"] * g["quantity"] // 1000 for g in b["goods"]) == 20800)

# ── 6. збій видачі чека більше не мовчазний (аудит #640): throttled-алерт із причиною ──
alerts = []
ost.send_throttled_alert = lambda key, text, cooldown_sec=0: alerts.append((key, text, cooldown_sec)) or True
def _boom(**kw): raise cbc.CheckboxAPIError("422 тест: невірне значення")
ost.create_receipt = _boom
calls.clear()
ost._maybe_issue_receipt(None, _order(internal_order_id="prom_fail_1"), "2045")
chk("CheckboxAPIError → throttled-алерт: ключ по замовленню, причина в тексті, ліміт 24 год, чек не позначено виданим",
    len(alerts) == 1 and alerts[0][0] == "receipt_fail_prom_fail_1" and "422 тест" in alerts[0][1] and alerts[0][2] == 24 * 3600
    and not any(c[0] == "mark" for c in calls if isinstance(c, tuple)))
def _boom2(**kw): raise RuntimeError("несподіване")
ost.create_receipt = _boom2
alerts.clear()
ost._maybe_issue_receipt(None, _order(internal_order_id="prom_fail_2"), "2045")
chk("неочікуваний виняток → теж алерт", len(alerts) == 1 and "несподіване" in alerts[0][1])
ost.send_throttled_alert = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("telegram down"))
ost._maybe_issue_receipt(None, _order(internal_order_id="prom_fail_3"), "2045")
chk("збій самого алерту не валить трекер", True)

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ COD цілі гривні — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
