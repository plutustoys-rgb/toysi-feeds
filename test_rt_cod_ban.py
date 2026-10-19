#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rt_cod_ban.py — 19.10.2026 (рішення власника: «продумайте вимкнення накладеного платежа» для товарів RoyalToys). Інваріанти:
(1) кошик із rt-позицією + cod / невідомий / відсутній спосіб → OrderError (fail-closed), prepaid проходить; кошик без rt — поведінка не змінилась (cod за замовчуванням);
(2) склад rt- fail-closed: нема знімка / застарілий / ключа «rt-…» у ньому немає / нечисловий / менше замовленого → рядок у списку «немає»; Toysi-рядки при тому ж стані — як раніше (fail-open);
(3) order_router: замовлення з rt-позицією НЕ передається в Toysi (route_order повертається до будь-якого звернення до Toysi), звичайне — проходить далі."""
import sys
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "site"))
import site_order_api as so  # noqa: E402
import order_router as orr  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def raises(fn):
    try:
        fn()
    except so.OrderError:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


so.price_map = lambda: {"100": {"price": 250, "name": "Тест"}, "rt-101": {"price": 400, "name": "Іграшка RT"}}
so.stock_snapshot = lambda: ({"100": 5, "rt-101": 3}, None)
base = {"items": [{"id": "rt-101", "qty": 1}], "name": "Іван Петренко", "phone": "0501234567", "city_name": "Київ", "warehouse_name": "відділення 5"}
chk("rt + cod → OrderError", raises(lambda: so.build_order(dict(base, payment_method="cod"))))
chk("rt + спосіб не вказано → OrderError (не мовчки cod)", raises(lambda: so.build_order(dict(base))))
chk("rt + сміття у способі → OrderError", raises(lambda: so.build_order(dict(base, payment_method="COD "))))
o, t = so.build_order(dict(base, payment_method="prepaid"))
chk("rt + prepaid → замовлення створено, prepaid", o["payment_method"] == "prepaid" and t == 400)
mixed = dict(base, items=[{"id": "100", "qty": 1}, {"id": "rt-101", "qty": 1}])
chk("змішаний кошик + cod → OrderError; + prepaid → ок", raises(lambda: so.build_order(dict(mixed, payment_method="cod"))) and so.build_order(dict(mixed, payment_method="prepaid"))[1] == 650)
plain = dict(base, items=[{"id": "100", "qty": 1}])
chk("без rt: cod за замовчуванням, як раніше", so.build_order(plain)[0]["payment_method"] == "cod" and so.build_order(dict(plain, payment_method="prepaid"))[0]["payment_method"] == "prepaid")

# склад
def items(pid, q=1):
    return [{"toysi_code": pid, "name": "x", "qty": q}]


chk("rt: у знімку 3, просять 4 → немає", so.check_stock(items("rt-101", 4))[0]["available"] == 3)
chk("rt: у знімку 3, просять 3 → є", so.check_stock(items("rt-101", 3)) == [])
so.stock_snapshot = lambda: ({"100": 5}, None)
chk("rt: ключа нема у знімку → немає (fail-closed)", so.check_stock(items("rt-101"))[0]["available"] == 0)
chk("Toysi: ключа нема у знімку → не чіпаємо (fail-open, як раніше)", so.check_stock(items("999")) == [])
so.stock_snapshot = lambda: ({"rt-101": "abc"}, None)
chk("rt: нечисловий залишок → немає", so.check_stock(items("rt-101"))[0]["available"] == 0)
so.stock_snapshot = lambda: (None, None)
chk("знімка нема/застарів: rt → немає, Toysi → не чіпаємо", so.check_stock(items("rt-101"))[0]["available"] == 0 and so.check_stock(items("100")) == [])
chk("змішаний кошик без знімка: лише rt-рядок у списку", [b["id"] for b in so.check_stock(items("rt-101") + items("100"))] == ["rt-101"])
try:
    so.build_order(dict(base, payment_method="prepaid"))
    ok = False
except so.StockChanged:
    ok = True
chk("rt без знімка → StockChanged (409), замовлення не створюється", ok)

# роутер: rt не йде в Toysi
orr._ensure_site_prepaid_receipt = lambda conn, order, tm: True
for fn in ("_check_prom_not_cancelled", "_check_eva_not_cancelled", "_check_rozetka_not_cancelled"):
    setattr(orr, fn, lambda conn, order: True)
reached = []
orr.fetch_toysi_catalog = lambda: reached.append("fetch") or {}
orr.site_np_hold_reason = lambda o, t: reached.append("hold") or "stop"
orr.send_throttled_alert = lambda *a, **k: None
orr.route_order(None, {"internal_order_id": "PT-1", "platform": "site", "items": [{"toysi_code": "rt-101", "qty": 1}]})
chk("route_order: rt-замовлення зупиняється ДО звернення до Toysi", reached == [])
orr.route_order(None, {"internal_order_id": "PT-2", "platform": "site", "items": [{"toysi_code": "100", "qty": 1}]})
chk("route_order: звичайне замовлення йде далі (fetch каталогу Toysi)", reached[:1] == ["fetch"])
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
