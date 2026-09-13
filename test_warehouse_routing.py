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
    calls = {"find_city": 0}
    orr.find_city = lambda name, area_hint="": (calls.__setitem__("find_city", calls["find_city"] + 1)
                                                or {"ref": f"CITYREF::{name}"})
    orr.settlement_raion = lambda *a, **k: ""

    # 1) EVA: CityRef + номер з площадки → напряму, find_city НЕ викликається.
    calls["find_city"] = 0
    r = orr.build_toysi_order(_order(platform="eva", np_branch="Мукачево, Відділення №3",
                                     np_city_ref="EVA-CITYREF", np_warehouse_number="3"))
    _check("EVA warehouse=клієнтів №3", r.get("shipping_warehouse_id"), "3")
    _check("EVA city=реф напряму", r.get("shipping_city_id"), "EVA-CITYREF")
    _check("EVA find_city не викликано", calls["find_city"], 0)

    # 2) Rozetka: номер (place_number) з площадки, CityRef немає → номер напряму, місто резолвимо.
    calls["find_city"] = 0
    r = orr.build_toysi_order(_order(platform="rozetka", np_branch="Мукачево, Відділення №3",
                                     np_warehouse_number="3"))
    _check("Rozetka warehouse=клієнтів №3", r.get("shipping_warehouse_id"), "3")
    _check("Rozetka city резолвнуто за назвою", r.get("shipping_city_id"), "CITYREF::Мукачево")
    _check("Rozetka find_city викликано (місто)", calls["find_city"], 1)

    # 3) Prom: лише вільний текст → номер розпарсений з тексту, місто з area_hint.
    r = orr.build_toysi_order(_order(platform="prom", np_branch="Київ (Київська обл.), Відділення №5"))
    _check("Prom warehouse=розпарсений №5", r.get("shipping_warehouse_id"), "5")
    _check("Prom city=Київ", r.get("shipping_city_id"), "CITYREF::Київ")

    # 4) КЛЮЧОВИЙ РЕГРЕС: однозначний №3 → саме 3 (раніше find_warehouse давав 2).
    r = orr.build_toysi_order(_order(platform="prom", np_branch="Мукачево, Відділення №3"))
    _check("Prom однозначний №3 → 3 (НЕ 2!)", r.get("shipping_warehouse_id"), "3")

    # 5) Укрпошта: НП-поля не застосовуються, адреса лишається текстом.
    r = orr.build_toysi_order(_order(platform="rozetka", carrier="ukrposhta",
                                     np_branch="Львів, Відділення №1", np_warehouse_number="1"))
    _check("Укрпошта без НП-рефа", "shipping_warehouse_id" in r, False)
    _check("Укрпошта адреса текстом", r.get("shipping_address"), "Львів, Відділення №1")

    # 6) Немає номера → без рефа, текст-фолбек.
    r = orr.build_toysi_order(_order(platform="prom", np_branch="Просто вулиця без номера"))
    _check("Без номера → без рефа", "shipping_warehouse_id" in r, False)

    # 7) НП недоступна (find_city кидає) → без рефа, текст-фолбек (Toysi-менеджер обробить вручну).
    import nova_poshta
    orr.find_city = lambda *a, **k: (_ for _ in ()).throw(nova_poshta.NovaPoshtaAPIError("НП down"))
    r = orr.build_toysi_order(_order(platform="prom", np_branch="Київ, Відділення №5"))
    _check("НП down → без рефа, текст", r.get("shipping_address"), "Київ, Відділення №5")

    print()
    if _FAILS:
        print(f"❌ ПРОВАЛЕНО {len(_FAILS)}: {_FAILS}")
        return 1
    print("✅ Усі перевірки маршрутизації відділення пройдено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
