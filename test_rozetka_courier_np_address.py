#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_rozetka_courier_np_address.py — регрес: кур'єрська доставка Нової Пошти з Rozetka (інцидент 908234237).

Що сталось (Toysi №100453636, 08–09.10.2026; те саме було 905803580 від 11.09): клієнт обрав доставку НП
НА АДРЕСУ (delivery_method_id=2, ref_id=null, place_number=""), у delivery лежать place_street/place_house.
`_rozetka_delivery_address` віддавав ГОЛЕ місто «Харків (Харківська обл.)» → у Toysi пішов shipping_address=""
→ менеджер питав «який тип доставки та адресу».

Інваріанти:
  1. НП без відділення + є вулиця → np_branch несе вулицю/будинок/квартиру.
  2. «№» у вулиці не стає «номером відділення» (parse_np_branch не бачить складу).
  3. build_toysi_order: shipping_warehouse_id НЕ задано (=адресна), shipping_address = «вул. …, буд. …».
  4. Відділення/поштомат — без змін (є номер → «Відділення №N»).
  5. Вулиці нема й відділення нема → голе місто, як раніше.

Самодостатній: `python test_rozetka_courier_np_address.py` → exit 0/1. Мережа не потрібна.
"""
import sys

import orders_watcher as ow
import order_router as orr

_FAILS = []


def _check(name, got, exp):
    ok = got == exp
    print(f"[{'OK ' if ok else 'FAIL'}] {name}: got={got!r} exp={exp!r}")
    if not ok:
        _FAILS.append(name)


# Дані — дослівно з живого GET замовлення 908234237 (08.10.2026), скорочено до потрібного.
_DELIVERY = {
    "delivery_service_name": "Нова Пошта", "name_logo": "nova-pochta", "ref_id": None,
    "place_street": "вул. Сумська", "place_number": "", "place_house": "39", "place_flat": None,
    "city": {"name_ua": "Харків", "region_title": "Харківська"},
}

_check("1. кур'єр: вулиця+будинок у np_branch",
       ow._rozetka_delivery_address({"delivery": dict(_DELIVERY)}),
       "Харків (Харківська обл.), вул. Сумська, буд. 39")
_check("1b. кур'єр: з квартирою",
       ow._rozetka_delivery_address({"delivery": dict(_DELIVERY, place_flat="12")}),
       "Харків (Харківська обл.), вул. Сумська, буд. 39, кв. 12")
_check("2. «№» у вулиці не дає номера відділення",
       orr.parse_np_branch(ow._rozetka_delivery_address(
           {"delivery": dict(_DELIVERY, place_street="вул. №5 Лісова", place_house="№7")}))[1], "")

_br = ow._rozetka_delivery_address({"delivery": dict(_DELIVERY)})
_o = orr.build_toysi_order({
    "internal_order_id": "rozetka_908234237", "order_id": "908234237", "platform": "rozetka",
    "payment_method": "prepaid", "customer_name": "Яковенко Ольга Викторовна", "phone": "380973922400",
    "np_branch": _br, "items": [{"toysi_code": "121516", "name": "x", "qty": 1, "price": 143.0}],
    "carrier": "nova_poshta", "np_warehouse_number": None, "np_city_ref": None, "np_ref_id": None,
})
_check("3a. Toysi: адреса доставки не порожня", _o["shipping_address"], "вул. Сумська, буд. 39")
_check("3b. Toysi: shipping_warehouse_id не задано (адресна)", "shipping_warehouse_id" in _o, False)

_check("4. відділення — без змін",
       ow._rozetka_delivery_address({"delivery": dict(_DELIVERY, place_number="253", place_street=None)}),
       "Харків (Харківська обл.), Відділення №253")
_check("5. нічого нема → голе місто",
       ow._rozetka_delivery_address({"delivery": dict(_DELIVERY, place_street=None, place_house=None)}),
       "Харків (Харківська обл.)")

print()
if _FAILS:
    print("FAILED:", _FAILS)
    sys.exit(1)
print("ALL OK")
