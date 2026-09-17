#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_eva_warehouse_ref.py — regres-test: EVA shipping.address.warehouse_id (NP UUID-реф
відділення, задокументований у eva.md: "city_id/region_id/warehouse_id/settlement_type:
UUID-рефи НП") тепер протягується як np_ref_id — той самий механізм ретраю/відкладання
резолву CityRef при форварді, що вже є для Rozetka (order_router.build_toysi_order,
2026-09-17, інцидент 906260104). Раніше warehouse_id був задокументований, але код його
не читав — якщо EVA колись дасть warehouse_id без city_id, build_toysi_order мав би нічим
ретраїти й одразу падав би на текст без структури (той самий клас бага, що й у Rozetka).

Мережа НЕ потрібна: nova_poshta.warehouse_by_ref замокано.
`python test_eva_warehouse_ref.py` → exit 0/1.
"""
import sys
import orders_watcher as ow
import order_router as orr

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


def _eva_order(**shipping_addr_overrides):
    addr = {
        "city": "Харків",
        "region": "Харківська",
        "street": {"name": "Відділення №65 (до 30 кг) : просп. Байрона, 169/24"},
        "city_id": "db5c88e0-391c-11dd-90d9-001a92567626",
        "warehouse_id": "b7fab5aa-a62c-11e4-a77a-005056887b8d",
        "warehouse_number": 65,
        "settlement_type": "місто",
    }
    addr.update(shipping_addr_overrides)
    return {
        "id": "8-000000001",
        "payment": {"method": "CASH_ON_DELIVERY", "status": "pending"},
        "items": [{"id": "1", "name": "x", "quantity": 1, "price": "100.00"}],
        "recipient": {"last_name": "Тест", "first_name": "Тест", "phone": "380500000000"},
        "shipping": {"method": "novaposhta_warehouse", "address": addr},
    }


# 1: city_id є напряму (штатний випадок) → np_ref_id ТЕЖ протягується (для звірки/майбутнього
#    ретраю), але build_toysi_order його не потребує, бо city_ref уже є.
conv = ow._convert_eva_order(_eva_order())
_chk("city_id є: np_city_ref = city_id", conv["np_city_ref"] == "db5c88e0-391c-11dd-90d9-001a92567626")
_chk("city_id є: np_ref_id = warehouse_id (протягнуто)", conv["np_ref_id"] == "b7fab5aa-a62c-11e4-a77a-005056887b8d")
_chk("city_id є: np_warehouse_number = 65", conv["np_warehouse_number"] == "65")
_chk("carrier = nova_poshta", conv["carrier"] == "nova_poshta")

# 2: city_id ВІДСУТНІЙ, але warehouse_id Є (гіпотетичний майбутній випадок EVA) → np_ref_id
#    дає build_toysi_order ЧИМ ретраїти, замість одразу падати на текст без структури.
conv2 = ow._convert_eva_order(_eva_order(city_id=None))
_chk("без city_id: np_city_ref порожній", conv2["np_city_ref"] is None)
_chk("без city_id: np_ref_id = warehouse_id (є чим ретраїти)", conv2["np_ref_id"] == "b7fab5aa-a62c-11e4-a77a-005056887b8d")

# 3: інтеграційно — normalize_order() коректно прокидає np_ref_id далі (той самий шлях у orders.db)
norm = ow.normalize_order(conv2)
_chk("normalize_order прокидає np_ref_id", norm["np_ref_id"] == "b7fab5aa-a62c-11e4-a77a-005056887b8d")

# 4: end-to-end — build_toysi_order() використовує np_ref_id EVA-замовлення ТОЧНО так само,
#    як Rozetka-замовлення (спільний механізм, не дублювання логіки)
_calls = []
orr.warehouse_by_ref = lambda ref: (_calls.append(ref) or
                                     {"city_ref": "db5c88e0-391c-11dd-90d9-001a92567626", "number": "65"})
norm["internal_order_id"] = "eva_8-000000001"
to = orr.build_toysi_order(norm)
_chk("build_toysi_order: warehouse_by_ref викликано з EVA warehouse_id",
     _calls == ["b7fab5aa-a62c-11e4-a77a-005056887b8d"])
_chk("build_toysi_order: city_id резолвнуто через ретрай", to.get("shipping_city_id") == "db5c88e0-391c-11dd-90d9-001a92567626")
_chk("build_toysi_order: warehouse_id=65", to.get("shipping_warehouse_id") == "65")

# 5: ні city_id, ні warehouse_id (гіпотетичний крайній випадок) → як і раніше, без гадання
conv3 = ow._convert_eva_order(_eva_order(city_id=None, warehouse_id=None))
_chk("без обох рефів: np_city_ref порожній", conv3["np_city_ref"] is None)
_chk("без обох рефів: np_ref_id порожній (нема чим ретраїти)", conv3["np_ref_id"] is None)


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ EVA warehouse_id (UUID-реф НП) протягується як np_ref_id — той самий механізм ретраю, що й Rozetka")
sys.exit(0)
