#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_address_pass_direct.py — регрес-тест «передаємо адресу клієнта НАПРЯМУ, без гадання».

Закриває рецидивний клас (інциденти 906224962 Бородянський, money-loss): раніше build_toysi_order
при відсутньому np_city_ref РЕЗОЛВИВ місто за назвою (find_city) → чуже однойменне село; і губив
НОМЕР відділення, якщо city_ref не резолвився (shipping_fields заповнювались лише разом з city_ref).

ІНВАРІАНТИ (order_router.build_toysi_order):
  1. is_np + місто + номер → shipping_warehouse_id = НОМЕР КЛІЄНТА ЗАВЖДИ; shipping_city_name = місто.
  2. np_city_ref є (площадка дала напряму: EVA / Rozetka-ref) → додається shipping_city_id.
  3. np_city_ref ПОРОЖНІЙ (НП недоступна) → номер+місто ВСЕ ОДНО йдуть; shipping_city_id ВІДСУТНІЙ;
     міста за назвою НЕ гадаємо (find_city прибрано — його імпорту в модулі більше нема).
  4. shipping_address порожній, коли є структурний номер (не текст-фолбек).

Мережа не потрібна (find_city усунено; settlement_raion замокано нижче).
`python test_address_pass_direct.py` → exit 0/1.
"""
import sys
import order_router as orr

# settlement_raion робить живі виклики НП (район у comment) — мокаємо, щоб тест був офлайн
# і не з'їдав rate-limit НП (аудит #553, nit).
orr.settlement_raion = lambda *a, **k: ""

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


def _order(**kw):
    base = dict(
        internal_order_id="t_1", order_id="1", platform="rozetka", carrier="nova_poshta",
        customer_name="Царенко Лідія Костянтинівна", phone="+380508581429", payment_method="cod",
        items=[{"toysi_code": "1", "name": "x", "qty": 1, "price": 168}],
        np_branch="Харків (Харківська обл.), Відділення №65",
    )
    base.update(kw)
    return base


# 1+2: np_city_ref є (Rozetka-ref/EVA) → номер+місто+CityRef
to = orr.build_toysi_order(_order(np_warehouse_number="65",
                                  np_city_ref="db5c88e0-391c-11dd-90d9-001a92567626"))
_chk("є ref: warehouse=65", to.get("shipping_warehouse_id") == "65")
_chk("є ref: місто=Харків", to.get("shipping_city_name") == "Харків")
_chk("є ref: city_id проставлено", to.get("shipping_city_id") == "db5c88e0-391c-11dd-90d9-001a92567626")
_chk("є ref: адреса-текст порожня", to.get("shipping_address") == "")

# 3: np_city_ref ПОРОЖНІЙ (НП недоступна) → номер+місто йдуть, city_id ВІДСУТНІЙ, БЕЗ гадання,
#    але ПОВНИЙ текст адреси клієнта (з областю) йде в shipping_address + область у comment (аудит #553)
to = orr.build_toysi_order(_order(np_warehouse_number="65", np_city_ref=""))
_chk("нема ref: warehouse=65 ВСЕ ОДНО", to.get("shipping_warehouse_id") == "65")
_chk("нема ref: місто=Харків ВСЕ ОДНО", to.get("shipping_city_name") == "Харків")
_chk("нема ref: city_id ВІДСУТНІЙ (не гадаємо)", "shipping_city_id" not in to)
_chk("нема ref: ПОВНИЙ текст адреси (з областю) у shipping_address",
     to.get("shipping_address") == "Харків (Харківська обл.), Відділення №65")
_chk("нема ref: область у comment", "Харківська обл." in to.get("comment", ""))

# 3в: коли CityRef Є — адреса-текст порожня (структурно однозначно)
to = orr.build_toysi_order(_order(np_warehouse_number="65", np_city_ref="db5c88e0"))
_chk("є ref: shipping_address порожня", to.get("shipping_address") == "")

# 3b: find_city прибрано з модуля — гадання за назвою фізично неможливе
_chk("find_city не імпортовано в order_router", not hasattr(orr, "find_city"))

# 4: Prom вільний текст → номер із parse_np_branch, місто, без city_id
to = orr.build_toysi_order(_order(platform="prom", np_branch="Львів, відділення №3",
                                  np_warehouse_number=None, np_city_ref=None))
_chk("Prom: warehouse=3 з тексту", to.get("shipping_warehouse_id") == "3")
_chk("Prom: місто=Львів", to.get("shipping_city_name") == "Львів")
_chk("Prom: city_id відсутній (без гадання)", "shipping_city_id" not in to)

# 5: не-НП (rozetka_delivery) → БЕЗ np shipping-полів
to = orr.build_toysi_order(_order(carrier="rozetka_delivery", np_warehouse_number="65",
                                  np_city_ref="x"))
_chk("не-НП: без shipping_warehouse_id", "shipping_warehouse_id" not in to)


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Адреса передається напряму (номер+місто клієнта), без гадання")
sys.exit(0)
