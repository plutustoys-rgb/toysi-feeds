#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_warehouse_routing.py — регрес-тест маршрутизації відділення НП у Toysi.

Захищає від РЕСТАВРАЦІЇ бага «замовляли №3 — прийшло на №2» (інцидент EVA 2026-09-13):
раніше build_toysi_order перешукував відділення в НП (find_warehouse), і голий номер
хибно збігався з цифрою в описі раніше розташованого відділення («№3» ↔ «до 30 кг» опису №2).

ІНВАРІАНТ, що тут закріплюється: build_toysi_order віддає Toysi shipping_warehouse_id
= ТОЧНИЙ номер, що обрав клієнт (структурний з площадки або розпарсений з тексту),
НІКОЛИ не перешукуючи відділення в НП. У НП резолвиться лише місто (назва→CityRef),
і лише коли площадка не дала CityRef напряму.

Самодостатній: `python test_warehouse_routing.py` → exit 0 (усі ок) / 1 (є провал).
find_city замоканий (мережа НП не потрібна); бойові дані не чіпаються.
"""
import sys

import order_router as orr

_FAILS = []


def _check(name, got, exp):
    ok = got == exp
    print(f"[{'OK ' if ok else 'FAIL'}] {name}: got={got!r} exp={exp!r}")
    if not ok:
        _FAILS.append(name)


def _order(**kw):
    base = dict(
        internal_order_id="test_1", order_id="1", platform="prom", carrier="nova_poshta",
        customer_name="Іван Петренко", phone="+380671112233", payment_method="cod",
        items=[{"toysi_code": "1", "name": "x", "qty": 1, "price": 100}],
    )
    base.update(kw)
    return base


def main():
    orr.settlement_raion = lambda *a, **k: ""

    # 1) EVA: CityRef + номер з площадки → обидва напряму.
    r = orr.build_toysi_order(_order(platform="eva", np_branch="Мукачево, Відділення №3",
                                     np_city_ref="EVA-CITYREF", np_warehouse_number="3"))
    _check("EVA warehouse=клієнтів №3", r.get("shipping_warehouse_id"), "3")
    _check("EVA city_id=реф напряму", r.get("shipping_city_id"), "EVA-CITYREF")

    # 2) Rozetka БЕЗ np_city_ref (НП-реф не резолвнувся) → номер клієнта ВСЕ ОДНО йде;
    #    міста за назвою НЕ гадаємо (find_city прибрано) → shipping_city_id відсутній,
    #    назва міста — як дав клієнт. Money-safe: чужого однойменного села не буде.
    r = orr.build_toysi_order(_order(platform="rozetka", np_branch="Мукачево, Відділення №3",
                                     np_warehouse_number="3"))
    _check("Rozetka warehouse=клієнтів №3", r.get("shipping_warehouse_id"), "3")
    _check("Rozetka без гадання: city_id відсутній", "shipping_city_id" in r, False)
    _check("Rozetka місто=Мукачево (назва клієнта)", r.get("shipping_city_name"), "Мукачево")

    # 3) Prom (вільний текст): номер розпарсений; city_id відсутній (find_city прибрано).
    r = orr.build_toysi_order(_order(platform="prom", np_branch="Київ (Київська обл.), Відділення №5"))
    _check("Prom warehouse=розпарсений №5", r.get("shipping_warehouse_id"), "5")
    _check("Prom без гадання: city_id відсутній", "shipping_city_id" in r, False)
    _check("Prom місто=Київ", r.get("shipping_city_name"), "Київ")

    # 4) КЛЮЧОВИЙ РЕГРЕС: однозначний №3 → саме 3 (раніше find_warehouse давав 2).
    r = orr.build_toysi_order(_order(platform="prom", np_branch="Мукачево, Відділення №3"))
    _check("Prom однозначний №3 → 3 (НЕ 2!)", r.get("shipping_warehouse_id"), "3")

    # 5) Укрпошта: НП-поля не застосовуються, адреса лишається текстом.
    r = orr.build_toysi_order(_order(platform="rozetka", carrier="ukrposhta",
                                     np_branch="Львів, Відділення №1", np_warehouse_number="1"))
    _check("Укрпошта без НП-рефа", "shipping_warehouse_id" in r, False)
    _check("Укрпошта адреса текстом", r.get("shipping_address"), "Львів, Відділення №1")

    # 6) Немає номера → без структурного відділення, текст-фолбек.
    r = orr.build_toysi_order(_order(platform="prom", np_branch="Просто вулиця без номера"))
    _check("Без номера → без рефа", "shipping_warehouse_id" in r, False)

    # 7) Реф не резолвнувся / НП недоступна: номер+місто клієнта ВСЕ ОДНО йдуть СТРУКТУРНО
    #    (не текст-фолбек, не гадання) — shipping_city_id відсутній, адреса-текст порожня.
    #    Раніше було навпаки (текст-фолбек + ручна обробка) — тепер клієнтські дані йдуть напряму.
    r = orr.build_toysi_order(_order(platform="prom", np_branch="Київ, Відділення №5"))
    _check("Реф-fail: warehouse=5 структурно", r.get("shipping_warehouse_id"), "5")
    _check("Реф-fail: city_id відсутній (без гадання)", "shipping_city_id" in r, False)
    _check("Реф-fail: адреса-текст порожня (є номер)", r.get("shipping_address"), "")

    print()
    if _FAILS:
        print(f"❌ ПРОВАЛЕНО {len(_FAILS)}: {_FAILS}")
        return 1
    print("✅ Усі перевірки маршрутизації відділення пройдено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
