#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_np_cityref_override_and_address_dedup.py — регрес-тест 2 багів наскрізного аудиту
order→Toysi (2026-09-26, вимога власника «не перекладай на тойсі, ще раз пропусти свій код
через незалежний аудит які ми данні отримали з маркетплейсів та куди ми їх вписуєм у тойсі»,
живий інцидент EVA 8-081850129 — Toysi показав "Адресна доставка" замість "Нова Пошта" +
подвоєне місто/область в адресі).

ФІКС 1 — city_ref ЗАВЖДИ звіряється з живим NP-довідником, а не лише коли площадка нічого
не дала. Живий доказ: EVA дала np_city_ref="e718a680-..." для Відділення №303, а
nova_poshta.warehouse_by_ref("bdad9843-...") (той самий np_ref_id, той самий заклад) повернув
city_ref="8d5a980d-..." — ІНШИЙ GUID для того самого відділення. Toysi не розпізнав хибний
CityRef площадки й показав загальну "Адресна доставка" замість "Нова Пошта". Раніше
warehouse_by_ref викликався лише коли np_city_ref був ПОРОЖНІМ (throttle-ретрай, інцидент
906260104) — тепер він викликається ЗАВЖДИ, коли є np_ref_id, і його результат ЗАВЖДИ
переважає значення площадки.

ФІКС 2 — shipping_address для carrier=nova_poshta більше не дублює місто+область, які вже
несе shipping_city_name (стек фіксів #593+#594 раніше складав їх без дедуплікації — живий
приклад: "г. Київ, Київська обл.. Склад #303. Київ (Київська обл.), Відділення №303...").
Тепер лишається лише залишок np_branch ПІСЛЯ міста (відділення+вулиця). Для НЕ-НП перевізників
(shipping_city_name БЕЗ області) лишається ПОВНИЙ np_branch — єдине місце, де область
узагалі передається.

Мережа не потрібна (warehouse_by_ref замокано). `python test_np_cityref_override_and_address_dedup.py`
→ exit 0/1.
"""
import sys

import order_router as orr

orr.settlement_raion = lambda *a, **k: ""

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


def _eva_order(**kw):
    base = dict(
        internal_order_id="eva_8-081850129", order_id="8-081850129", platform="eva",
        carrier="nova_poshta", customer_name="Голубнича Олеся Євгенівна",
        phone="+380688671070", payment_method="cod",
        items=[{"toysi_code": "1", "name": "x", "qty": 1, "price": 100}],
        np_branch="Київ (Київська обл.), Відділення №303 (до 30 кг) : вул. Срібнокільська, 14а",
        np_warehouse_number="303",
        np_city_ref="e718a680-4b33-11e4-ab6d-005056801329",  # хибний CityRef від EVA
        np_ref_id="bdad9843-7196-11e9-898c-005056b24375",
    )
    base.update(kw)
    return base


_REAL_WAREHOUSE = {
    "city_ref": "8d5a980d-391c-11dd-90d9-001a92567626",  # ІНШИЙ GUID, ніж EVA дала
    "number": "303",
    "description": "Відділення №303 (до 30 кг): вул. Срібнокільська, 14а",
}


# ── ФІКС 1: живий NP-довідник переважає над (хибним) city_ref площадки ──
_calls = []


def _mock_warehouse_by_ref(ref):
    _calls.append(ref)
    return _REAL_WAREHOUSE


orr.warehouse_by_ref = _mock_warehouse_by_ref
_calls.clear()
to = orr.build_toysi_order(_eva_order())

_chk("warehouse_by_ref викликано з np_ref_id (ЗАВЖДИ, не лише коли city_ref порожній)",
     _calls == ["bdad9843-7196-11e9-898c-005056b24375"])
_chk("city_id = ЖИВИЙ рез (не хибний EVA city_ref) — саме це вирішує 'Адресна доставка' vs 'Нова Пошта'",
     to.get("shipping_city_id") == "8d5a980d-391c-11dd-90d9-001a92567626")
_chk("city_id НЕ дорівнює хибному EVA city_ref",
     to.get("shipping_city_id") != "e718a680-4b33-11e4-ab6d-005056801329")
_chk("warehouse_id звірений тим самим резолвом", to.get("shipping_warehouse_id") == "303")

# Симуляція ПРЕ-ФІКС поведінки (умова "лише коли city_ref порожній") — довела б, що цей
# тест реально стереже регрес, а не проходить випадково.
_calls.clear()
_city_ref_platform = (_eva_order().get("np_city_ref") or "").strip()
_pre_fix_would_call = not _city_ref_platform and _eva_order().get("np_ref_id")
_chk("ПРЕ-ФІКС інваріант: city_ref НЕПОРОЖНІЙ від EVA → стара умова НЕ викликала б warehouse_by_ref",
     not _pre_fix_would_call)


# ── ФІКС 2: shipping_address не дублює місто+область (уже в shipping_city_name) ──
_chk("shipping_city_name несе повне місто+область",
     to.get("shipping_city_name") == "Київ, Київська обл.")
_chk("shipping_address = ЛИШЕ залишок ПІСЛЯ міста (без повторного 'Київ (Київська обл.)')",
     to.get("shipping_address") == "Відділення №303 (до 30 кг) : вул. Срібнокільська, 14а")
_chk("shipping_address НЕ містить дублю 'Київ' (регрес живого інциденту)",
     to.get("shipping_address", "").count("Київ") == 0)

# Пре-фікс: без дедуплікації shipping_address = ПОВНИЙ np_branch → містив би "Київ" ще раз.
_pre_fix_address = _eva_order()["np_branch"]
_chk("ПРЕ-ФІКС інваріант: без дедуплікації адреса-текст містила б 'Київ' (доводить, що фікс справді щось міняє)",
     "Київ" in _pre_fix_address)


# ── Контроль: НЕ-НП перевізник — shipping_address лишається ПОВНИМ (область більш ніде нема) ──
_calls.clear()
to_np_only_city = orr.build_toysi_order(_eva_order(
    carrier="ukrposhta", np_warehouse_number=None, np_ref_id=None, np_city_ref=None,
))
_chk("не-НП: warehouse_by_ref НЕ викликається (нема np_ref_id)", _calls == [])
_chk("не-НП: shipping_city_name БЕЗ області", to_np_only_city.get("shipping_city_name") == "Київ")
_chk("не-НП: shipping_address = ПОВНИЙ np_branch (єдине місце з областю)",
     to_np_only_city.get("shipping_address")
     == "Київ (Київська обл.), Відділення №303 (до 30 кг) : вул. Срібнокільська, 14а")


# ── Контроль: warehouse_by_ref падає (мережа/throttle) — best-effort, форвард не валиться ──
def _mock_warehouse_by_ref_raises(ref):
    raise RuntimeError("мережа впала")


orr.warehouse_by_ref = _mock_warehouse_by_ref_raises
to_err = orr.build_toysi_order(_eva_order())
_chk("warehouse_by_ref впав: build_toysi_order все одно повертає dict (best-effort)",
     to_err is not None)
_chk("warehouse_by_ref впав: city_id = (хибний, але наявний) фолбек площадки, не падіння",
     to_err.get("shipping_city_id") == "e718a680-4b33-11e4-ab6d-005056801329")


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ city_ref ЗАВЖДИ звіряється з живим NP-довідником + shipping_address не дублює "
      "місто/область (інцидент EVA 8-081850129, 2026-09-26)")
sys.exit(0)
