#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_promo.py — знижки сайту: промокод PLUTUS5 (−5%) і автоматична −10% за дві РІЗНІ позиції (site_promo + site_order_api, рішення власника/Консультанта 13.10.2026).
Інваріанти: (1) константи збігаються з закритою формулою (еквайринг 2,8%, 17,81); (2) ціна одиниці після знижки = ціла гривня ВГОРУ; (3) знижка лише на рядки, де лишається покриття обробки
(нова_ціна×(1−2,8%) − собівартість − 17,81 для коду; − 8,905 і нова ціна ≥20 для −10%); (4) знижки НЕ складаються, береться більша (рівність — код); (5) −10% потребує ≥2 РІЗНИХ id (qty=2 одного — ні);
(6) невідома собівартість (нема/застарів файл) → знижок немає, код → «unavailable»; (7) замовлення: items[].price = після знижки, list_price збережено, site_charged_total = Σ price×qty (чек і LiqPay збігаються
за побудовою), promo_code і promo_discount_total у БД; (8) PLUTUS5 — 1 використання на телефон (повторно → 400 promo_used, замовлення не створюється); (9) невідомий код → 400, замовлення нема;
(10) quote дає ті самі числа, що й замовлення, і нічого не пише в БД. Мережа не потрібна. `python test_site_promo.py` → exit 0/1."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")

import competitor_pricing as cp
import orders_db
import site_order_api as api
import site_promo as sp

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


# ── (1) константи
chk("еквайринг і витрати збігаються з закритою формулою competitor_pricing", sp.ACQUIRING == cp.PAYMENT_COMMISSION["site"] and sp.FULFILL_FULL == cp.SITE_FULFILL_PER_ORDER and abs(sp.FULFILL_HALF - 8.905) < 1e-9)

# ── (2) округлення
chk("ціна після знижки: ціла гривня ВГОРУ (100→95, 101→96, 19@10%→18, 20@10%→18, 1000@5%→950)", (sp.discounted_unit(100, 5), sp.discounted_unit(101, 5), sp.discounted_unit(19, 10), sp.discounted_unit(20, 10), sp.discounted_unit(1000, 5)) == (95, 96, 18, 18, 950))

# ── (3-6) compute
OK_COST = {"A": 50.0, "B": 60.0, "C": 98.0, "D": 12.0}                 # A,B дешеві по собівартості → знижка проходить; C майже без запасу; D дешевий товар
items = [{"toysi_code": "A", "qty": 1, "price": 100, "name": "A"}, {"toysi_code": "B", "qty": 2, "price": 120, "name": "B"}]
r = sp.compute(items, OK_COST, "PLUTUS5")
chk("PLUTUS5 на 2 товарах: −5% кращий чи рівний? перевірка вибору більшої знижки: авто −10% більша → TWO10, код не використано", r["applied"] == "TWO10" and r["code_status"] == "not_applicable" and r["lines"] == {"A": 90, "B": 108})
r = sp.compute(items[:1], OK_COST, "plutus5")
chk("PLUTUS5 (будь-який регістр) на одному товарі → застосовано −5%: 100→95", r["applied"] == "PLUTUS5" and r["lines"] == {"A": 95} and r["discount_total"] == 5 and r["code_status"] == "ok")
r = sp.compute(items, OK_COST, None)
chk("без коду, два різні товари → автоматична −10%", r["applied"] == "TWO10" and r["discount_total"] == (100 - 90) * 1 + (120 - 108) * 2)
r = sp.compute([{"toysi_code": "A", "qty": 2, "price": 100, "name": "A"}], OK_COST, None)
chk("один товар qty=2 → −10% НЕ діє (потрібні дві РІЗНІ позиції)", r["applied"] is None and r["discount_total"] == 0)
r = sp.compute([{"toysi_code": "C", "qty": 1, "price": 100, "name": "C"}], OK_COST, "PLUTUS5")
chk("собівартість 98: після −5% покриття від'ємне → рядок без знижки, код not_applicable", r["applied"] is None and r["code_status"] == "not_applicable")
r = sp.compute([{"toysi_code": "A", "qty": 1, "price": 100, "name": "A"}, {"toysi_code": "C", "qty": 1, "price": 100, "name": "C"}], OK_COST, None)
chk("−10% лише на придатні рядки (A так, C ні)", r["applied"] == "TWO10" and r["lines"] == {"A": 90})
r = sp.compute([{"toysi_code": "D", "qty": 1, "price": 21, "name": "D"}, {"toysi_code": "A", "qty": 1, "price": 100, "name": "A"}], OK_COST, None)
chk("−10% не дає ціну нижче 20 ₴ (21→19 не береться), на A береться", r["lines"] == {"A": 90})
r = sp.compute(items[:1], OK_COST, "ABC")
chk("невідомий код → unknown, знижки нема", r["code_status"] == "unknown" and r["applied"] is None)
r = sp.compute(items, OK_COST, "PLUTUS5", code_allowed=False)
chk("код уже використано з цього телефону → used, але авто −10% лишається", r["code_status"] == "used" and r["applied"] == "TWO10")
r = sp.compute(items, None, "PLUTUS5")
chk("собівартість невідома (None) → знижок нема, код → unavailable", r["applied"] is None and r["code_status"] == "unavailable" and r["costs_available"] is False)
new_items, new_total = sp.apply_to_items(items[:1], sp.compute(items[:1], OK_COST, "PLUTUS5"))
chk("apply_to_items: price після знижки, list_price каталожна, вхід не мутується, сума", new_items[0]["price"] == 95 and new_items[0]["list_price"] == 100 and items[0]["price"] == 100 and new_total == 95)

# ── завантаження файлу собівартості
tmp = Path(tempfile.mkdtemp())
sp.COST_FILE = tmp / "promo_cost.json"
chk("файлу нема → load_cost_map None", sp.load_cost_map() is None)
sp.COST_FILE.write_text(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "c": OK_COST}), encoding="utf-8")
chk("свіжий файл → мапа", sp.load_cost_map() == OK_COST)
sp.COST_FILE.write_text(json.dumps({"ts": (datetime.now() - timedelta(hours=7)).isoformat(timespec="seconds"), "c": OK_COST}), encoding="utf-8")
sp._cost_cache["key"] = None
chk("файл старший за 6 год → None (знижок нема)", sp.load_cost_map() is None)
sp.COST_FILE.write_text("не json", encoding="utf-8")
sp._cost_cache["key"] = None
chk("пошкоджений файл → None", sp.load_cost_map() is None)


def fresh_costs(c=OK_COST):
    sp.COST_FILE.write_text(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "c": c}), encoding="utf-8")
    sp._cost_cache["key"] = None


# ── інтеграція з замовленням
orders_db.init_db()
api.price_map = lambda: {"A": {"name": "Товар A", "price": 100}, "B": {"name": "Товар B", "price": 120}, "C": {"name": "Товар C", "price": 100}}
api.STOCK_FILE = str(tmp / "no_stock.json")          # перевірка залишку fail-open


def post(path, body):
    out = []
    h = api.Handler.__new__(api.Handler)
    h.command, h.path = "POST", path
    h._read_json = lambda: body
    h._json = lambda code, obj: out.append((code, obj))
    (h._handle_order if path == "/api/order" else h._handle_promo_quote)()
    return out[0]


def order_body(items, phone, code=None, pm="cod"):
    b = {"items": items, "name": "Іван Петренко", "phone": phone, "city_name": "Київ", "warehouse_name": "Відділення №1", "np_warehouse_number": "1", "payment_method": pm}
    if code is not None:
        b["promo_code"] = code
    return b


def db_rows():
    with orders_db.get_connection() as c:
        return c.execute("SELECT * FROM orders WHERE platform = 'site' ORDER BY created_at").fetchall()


fresh_costs()
n0 = len(db_rows())
code, resp = post("/api/order", order_body([{"id": "A", "qty": 1}], "+380671110001", "PLUTUS5"))
rows = db_rows()
row = dict(rows[-1])
its = json.loads(row["items"])
chk("замовлення з PLUTUS5: 200, сума = 95 (а не 100)", code == 200 and resp["total"] == 95)
chk("БД: promo_code, promo_discount_total, site_charged_total = Σ price×qty (чек і LiqPay збігаються за побудовою), items[].list_price", row["promo_code"] == "PLUTUS5" and row["promo_discount_total"] == 5 and row["site_charged_total"] == 95 == sum(i["price"] * i["qty"] for i in its) and its[0]["list_price"] == 100)
n1 = len(db_rows())
code, resp = post("/api/order", order_body([{"id": "A", "qty": 1}], "+380671110001", "PLUTUS5"))
chk("PLUTUS5 повторно з того ж телефону → 400 promo_used, замовлення НЕ створено", code == 400 and resp["code"] == "promo_used" and len(db_rows()) == n1)
code, resp = post("/api/order", order_body([{"id": "A", "qty": 1}], "+380671110002", "PLUTUS5"))
chk("інший телефон → код діє", code == 200 and resp["total"] == 95)
code, resp = post("/api/order", order_body([{"id": "A", "qty": 1}, {"id": "B", "qty": 1}], "+380671110003"))
row = dict(db_rows()[-1])
chk("два різні товари без коду → авто −10%: 100→90, 120→108 = 198; promo_code TWO10", code == 200 and resp["total"] == 198 and row["promo_code"] == "TWO10" and row["promo_discount_total"] == 22)
n1 = len(db_rows())
code, resp = post("/api/order", order_body([{"id": "A", "qty": 1}], "+380671110004", "ЛІВИЙ"))
chk("невідомий код → 400 promo_unknown, замовлення НЕ створено", code == 400 and resp["code"] == "promo_unknown" and len(db_rows()) == n1)
code, resp = post("/api/order", order_body([{"id": "A", "qty": 1}], "+380671110005"))
row = dict(db_rows()[-1])
chk("без коду і без знижки: ціна каталожна, promo_code NULL", code == 200 and resp["total"] == 100 and row["promo_code"] is None)
code, resp = post("/api/order", order_body([{"id": "C", "qty": 1}], "+380671110006", "PLUTUS5"))
chk("код дійсний, але C без покриття → 400 promo_not_applicable, замовлення нема", code == 400 and resp["code"] == "promo_not_applicable")

# ── собівартість недоступна
sp.COST_FILE.unlink()
sp._cost_cache["key"] = None
n1 = len(db_rows())
code, resp = post("/api/order", order_body([{"id": "A", "qty": 1}], "+380671110007", "PLUTUS5"))
chk("нема файлу собівартості + код → 400 promo_unavailable, замовлення нема", code == 400 and resp["code"] == "promo_unavailable" and len(db_rows()) == n1)
code, resp = post("/api/order", order_body([{"id": "A", "qty": 1}, {"id": "B", "qty": 1}], "+380671110008"))
chk("нема файлу собівартості, без коду → замовлення приймається БЕЗ знижки (220)", code == 200 and resp["total"] == 220)

# ── quote
fresh_costs()
n1 = len(db_rows())
code, q = post("/api/promo/quote", {"items": [{"id": "A", "qty": 1}, {"id": "B", "qty": 1}], "promo_code": "PLUTUS5", "phone": "+380671119999"})
chk("quote: applied TWO10, total 198, list_total 220, рядки з list/unit ціною, код not_applicable з поясненням", code == 200 and q["applied"] == "TWO10" and q["total"] == 198 and q["list_total"] == 220 and q["code_status"] == "not_applicable" and q["message"] and q["lines"][0]["unit_price"] == 90)
code, q = post("/api/promo/quote", {"items": [{"id": "A", "qty": 1}], "promo_code": "PLUTUS5", "phone": "+380671110001"})
chk("quote: телефон уже використав код → used", q["code_status"] == "used" and q["total"] == 100)
code, q = post("/api/promo/quote", {"items": [{"id": "ZZZ", "qty": 1}]})
chk("quote: невідомий товар → 400", code == 400)
chk("quote нічого не пише в БД", len(db_rows()) == n1)

# ═══ ДОПОВНЕННЯ після аудиту PR #738 ═══
# рівні знижки → код (X: 21×5 дає код 5, −10% не береться (<20); Z: 100×1 дає код 5, −10% 10 → da=10, db=10)
eq_items = [{"toysi_code": "X", "qty": 5, "price": 21, "name": "X"}, {"toysi_code": "Z", "qty": 1, "price": 100, "name": "Z"}]
r = sp.compute(eq_items, {"X": 1.0, "Z": 50.0}, "PLUTUS5")
chk("рівні суми знижок (10 = 10) → перемагає промокод", r["applied"] == "PLUTUS5" and r["discount_total"] == 10 and r["code_status"] == "ok")

# нерядковий промокод не валить запит
fresh_costs()
for k, bad in enumerate((123, {"a": 1}, ["PLUTUS5"], True)):
    code, resp = post("/api/order", order_body([{"id": "A", "qty": 1}], "+3806711200" + str(10 + k), bad))
    chk(f"нерядковий promo_code {bad!r} → замовлення без коду (не 500)", code == 200 and resp["total"] == 100)

# ліміт: неоплачені передоплатні (свіжі) теж рахуються; покинуті >24 год — ні
import orders_db as odb
for i in range(3):
    odb_row = dict(order_id=f"PT-LIM{i}", platform="site", payment_method="prepaid", payment_confirmed=False, customer_name="Т", phone="380671131001", np_branch="Київ, №1",
                   items=[{"toysi_code": "A", "name": "A", "qty": 1, "price": 95, "list_price": 100}], promo_code="PLUTUS5", promo_discount_total=5)
    with odb.get_connection() as c:
        odb.insert_order(c, odb_row)
        c.commit()
with odb.get_connection() as c:
    chk("свіжі НЕОПЛАЧЕНІ передоплатні з PLUTUS5 тепер рахуються (ліміт не обійти серією замовлень)", api._promo_code_allowed(c, "380671131001") is False)
    c.execute("UPDATE orders SET created_at = ? WHERE phone = '380671131001'", ((datetime.now() - timedelta(hours=30)).isoformat(timespec="seconds"),))
    c.commit()
    chk("покинуті неоплачені >24 год — не рахуються (чесний покупець може повторити)", api._promo_code_allowed(c, "380671131001") is True)

# fail-closed в /api/order при збої БД ліміту; quote толерантний
real_allowed = api._promo_code_allowed
api._promo_code_allowed = lambda conn, phone: (_ for _ in ()).throw(RuntimeError("database is locked"))
n1 = len(db_rows())
code, resp = post("/api/order", order_body([{"id": "A", "qty": 1}], "+380671130001", "PLUTUS5"))
chk("збій перевірки ліміту в /api/order → 400 promo_unavailable (fail-closed), замовлення нема", code == 400 and resp["code"] == "promo_unavailable" and len(db_rows()) == n1)
code, q = post("/api/promo/quote", {"items": [{"id": "A", "qty": 1}], "promo_code": "PLUTUS5", "phone": "+380671130002"})
chk("quote при збої БД ліміту не падає (показує знижку; замовлення все одно перевірить)", code == 200 and q["applied"] == "PLUTUS5")
api._promo_code_allowed = real_allowed

# гонка: повторна перевірка ліміту всередині транзакції вставки (перша перевірка пройшла, між нею і вставкою з'явилось замовлення)
calls = {"n": 0}


def racy_allowed(conn, phone):
    calls["n"] += 1
    return True if calls["n"] == 1 else real_allowed(conn, phone)      # 1-ша (quote) пройшла; 2-га (у транзакції вставки) бачить уже зайнятий код


with odb.get_connection() as c:                                         # «конкурент» уже вставив замовлення з кодом для цього телефону
    odb.insert_order(c, dict(order_id="PT-RACE0", platform="site", payment_method="cod", payment_confirmed=False, customer_name="Т", phone="380671140001", np_branch="Київ, №1",
                             items=[{"toysi_code": "A", "name": "A", "qty": 1, "price": 95, "list_price": 100}], promo_code="PLUTUS5", promo_discount_total=5))
    c.commit()
api._promo_code_allowed = racy_allowed
n1 = len(db_rows())
code, resp = post("/api/order", order_body([{"id": "A", "qty": 1}], "+380671140001", "PLUTUS5"))
api._promo_code_allowed = real_allowed
chk("гонка: друге замовлення з тим самим телефоном відхилено повторною перевіркою в транзакції (promo_used), нове НЕ створено", code == 400 and resp["code"] == "promo_used" and len(db_rows()) == n1)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
# ── ЗМІШАНИЙ кошик (Toysi + RT = два замовлення, дві витрати): автоматична −10% НЕ діє (Консультант 23.10)
MIX_COST = dict(OK_COST, **{"rt-1": 50.0})
mixed_items = [{"toysi_code": "A", "qty": 1, "price": 100, "name": "A"}, {"toysi_code": "rt-1", "qty": 1, "price": 120, "name": "R"}]
r = sp.compute(mixed_items, MIX_COST, None)
chk("ті самі два різні товари БЕЗ позначки mixed → автоматична −10% діє (поведінка не змінена)", r["applied"] == "TWO10" and r["discount_total"] > 0)
r = sp.compute(mixed_items, MIX_COST, None, mixed=True)
chk("mixed=True → TWO10 НЕ застосовується (знижки нема, discount_total=0)", r["applied"] is None and r["discount_total"] == 0 and r["lines"] == {})
r = sp.compute(mixed_items, MIX_COST, "PLUTUS5", mixed=True)
chk("mixed=True + PLUTUS5 → промокод діє як раніше (кожен рядок покриває повну витрату), код використано", r["applied"] == "PLUTUS5" and r["code_status"] == "ok" and r["discount_total"] > 0)
r = sp.compute(mixed_items, MIX_COST, "PLUTUS5", code_allowed=False, mixed=True)
chk("mixed=True + код уже використано → знижки нема зовсім (TWO10 теж не діє)", r["applied"] is None and r["code_status"] == "used")

sys.exit(1 if F else 0)
