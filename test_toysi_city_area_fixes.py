#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_toysi_city_area_fixes.py — регрес-тест 3 багів наскрізного аудиту order→Toysi
(2026-09-25, вимога власника «з усіх площадок повинно правильно передаватися Тойсі
з урахуванням їх вимог»):

  1. order_router.build_toysi_order: settlement_raion() тепер отримує settlement_ref
     (city_ref), коли він відомий — раніше район губився навіть при точному рефі
     (settlement_raion без ref повертає "" при кількох однойменних селах в ОДНІЙ
     області, хоча ref уже знімає неоднозначність).
  2. toysi_order_submit.build_order_create_payload: дублікат toysi_code у позиціях
     раніше мовчки губив qty попередніх входжень (dict-компрегенція перезаписувала),
     а moneyback рахував повну суму — тепер qty СУМУЄТЬСЯ за кодом.
  3. site_order_api.build_order: np_city_area (з автокомпліту фронта) тепер потрапляє
     в np_branch у форматі "(Xобл.)", який parse_np_branch розпізнає — раніше сайт
     ніколи не передавав область у shipping_city.

`python test_toysi_city_area_fixes.py` → exit 0/1. Мережа НП не потрібна (моки).
"""
import sys

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


# ── 1. settlement_raion отримує settlement_ref ──
import order_router as orr

_raion_calls = []


def _mock_settlement_raion(city_name, settlement_ref="", area_hint=""):
    _raion_calls.append({"city_name": city_name, "settlement_ref": settlement_ref, "area_hint": area_hint})
    return "Тестовий"


orr.settlement_raion = _mock_settlement_raion

to = orr.build_toysi_order(dict(
    internal_order_id="t_area", order_id="1", platform="eva", carrier="nova_poshta",
    customer_name="Іваненко Іван Іванович", phone="+380671112233", payment_method="cod",
    items=[{"toysi_code": "1", "name": "x", "qty": 1, "price": 100}],
    np_branch="Троїцьке (Одеська обл., Біляївський р-н), Відділення №1",
    np_warehouse_number="1", np_city_ref="SOME-EXACT-SETTLEMENT-REF",
))
_chk("settlement_raion викликано рівно раз", len(_raion_calls) == 1)
_chk("settlement_raion отримав ТОЙ САМИЙ city_ref (не порожній)",
     _raion_calls and _raion_calls[0]["settlement_ref"] == "SOME-EXACT-SETTLEMENT-REF")
_chk("район підхопився в shipping_city_name", "Тестовий р-н" in (to.get("shipping_city_name") or ""))

# 1б: той самий механізм на Rozetka (np_city_ref через warehouse_by_ref, не EVA structural) —
# спільний код build_toysi_order не розгалужується за платформою, але явна перевірка не зайва
# (власник, 2026-09-25: «розетка, пром теж перевірили?»).
_raion_calls.clear()
to_rz = orr.build_toysi_order(dict(
    internal_order_id="t_area_rz", order_id="2", platform="rozetka", carrier="nova_poshta",
    customer_name="Петренко Петро Петрович", phone="+380671112233", payment_method="cod",
    items=[{"toysi_code": "1", "name": "x", "qty": 1, "price": 100}],
    np_branch="Троїцьке (Одеська обл., Біляївський р-н), Відділення №1",
    np_warehouse_number="1", np_city_ref="RZ-EXACT-SETTLEMENT-REF",
))
_chk("Rozetka: settlement_raion отримав ref", _raion_calls and _raion_calls[0]["settlement_ref"] == "RZ-EXACT-SETTLEMENT-REF")
_chk("Rozetka: район підхопився в shipping_city_name", "Тестовий р-н" in (to_rz.get("shipping_city_name") or ""))


# ── 2. positions_quantity сумує дублікати toysi_code, не перезаписує ──
import toysi_order_submit as tos

order = dict(
    internal_order_id="t_dup", items=[
        {"toysi_code": "111", "qty": 2},
        {"toysi_code": "111", "qty": 3},
        {"toysi_code": "222", "qty": 1},
    ],
    first_name="Ім'я", last_name="Прізвище", phone="380671112233",
    shipping_city_name="Київ", shipping_address="",
)
payload = tos.build_order_create_payload(order)
_chk("дублікат toysi_code=111 просумований (2+3=5), не перезаписаний",
     payload.get("positions_quantity[111]") == 5)
_chk("унікальний toysi_code=222 не зачеплений", payload.get("positions_quantity[222]") == 1)
_chk("positions_count = 2 (кількість УНІКАЛЬНИХ кодів, не рядків items)",
     payload.get("positions_count") == 2)


# ── 3. site_order_api: np_city_area → np_branch у форматі "(Xобл.)" ──
import site_order_api as soa

soa.price_map = lambda: {"1": {"name": "Товар", "price": 100}}

order, total = soa.build_order({
    "items": [{"id": "1", "qty": 1}],
    "name": "Іван Петренко",
    "phone": "+380671112233",
    "city_name": "Південне",
    "warehouse_name": "Відділення №1",
    "np_city_ref": "",
    "np_city_area": "Харківська",
    "np_warehouse_number": "",
    "payment_method": "cod",
})
_chk("np_branch несе область у форматі (Xобл.)",
     order.get("np_branch") == "Південне (Харківська обл.), Відділення №1")

# 3б: без області (ручний ввід/старий фронт без поля) — фолбек як і раніше, без падіння
order2, _ = soa.build_order({
    "items": [{"id": "1", "qty": 1}],
    "name": "Іван Петренко",
    "phone": "+380671112233",
    "city_name": "Південне",
    "warehouse_name": "Відділення №1",
    "payment_method": "cod",
})
_chk("без np_city_area: старий фолбек без дужок", order2.get("np_branch") == "Південне, Відділення №1")

# 3в: захист від ін'єкції дужок/переносів у client-supplied area
order3, _ = soa.build_order({
    "items": [{"id": "1", "qty": 1}],
    "name": "Іван Петренко",
    "phone": "+380671112233",
    "city_name": "Київ",
    "warehouse_name": "Відділення №1",
    "np_city_area": "Зле)(значення\nобласть",
    "payment_method": "cod",
})
_chk("санітизація area: рівно 1 пара дужок у np_branch (клієнтський '(' ')'/перенос не проліз)",
     order3["np_branch"].count("(") == 1 and order3["np_branch"].count(")") == 1
     and "\n" not in order3["np_branch"])


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Усі 3 фікси наскрізного аудиту order→Toysi (2026-09-25) підтверджені")
sys.exit(0)
