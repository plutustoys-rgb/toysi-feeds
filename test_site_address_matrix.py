#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_address_matrix.py — 12.10.2026 (запит власника: «змоделювати всі варіанти заповнення адреси клієнтом і переконатися, що жоден минулий адресний баг з інших площадок не повториться на сайті»).
Таблична модель ВСІХ варіантів вводу форми сайту через СПРАВЖНІ site_order_api.build_order → order_router.site_np_hold_reason → order_router.build_toysi_order (мережу НП застабовано).
Кожен рядок = «ввід покупця → що піде в Toysi»: PASS (shipping_warehouse_id / CityRef / місто+область / ПІБ / телефон), HOLD (замовлення НЕ йде в Toysi, алерт власнику) або REFUSE (сайт відмовляє з поясненням).
Покриті минулі баги: Бородянський 906224962 і «Південне»→«Южное» EVA 8-081747967 (тезки міст), EVA 13.09 №3→№2 (перешук відділення), EVA 8-081850129 (дубль області/міста в адресі), сайт 10.10 «369» → «адресна доставка»,
«кв. №162»/«під'їзд №1»/«відділення166» як номер відділення, Prom 414634349 («м. Київ (Київська обл.)»), телефон «+380…» (Toysi code 16), ПІБ «Прізвище Ім'я По-батькові» (Toysi 100449125), COD копійки."""
import contextlib
import io
import sys
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import order_router as orr  # noqa: E402
import site_order_api as so  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


so.price_map = lambda: {"100": {"price": 250, "name": "Тест"}, "101": {"price": 39.23, "name": "Копійки"}}
orr.settlement_raion = lambda city, settlement_ref="", area_hint="": ""     # район (мережа НП) тут не моделюємо
orr.warehouse_by_ref = lambda ref: None
CITY_COUNT = {"default": 1}
orr.count_cities_named = lambda city: CITY_COUNT.get(city.strip().lower(), CITY_COUNT["default"])     # скільки населених пунктів НП має таку назву (мережа застабована)

REF = "e718a680-1111-2222-3333-444455556666"


def base(**kw):
    p = {"items": [{"id": "100", "qty": 1}], "name": "Іван Петренко", "phone": "+380501234567", "city_name": "Київ",
         "warehouse_name": "Відділення №15 (до 30 кг на одне місце): вул. Хрещатик, 1", "np_city_ref": REF, "np_warehouse_number": "15", "np_city_area": "Київська", "payment_method": "cod"}
    p.update(kw)
    return p


def manual(**kw):
    """ручний ввід (довідник мертвий / покупець не торкнувся підказки): без CityRef, області й № відділення"""
    kw.setdefault("np_city_ref", "")
    kw.setdefault("np_city_area", "")
    kw.setdefault("np_warehouse_number", "")
    return base(**kw)


def run(payload):
    try:
        order, _total = so.build_order(payload)
    except so.OrderError as e:
        return ("REFUSE", str(e), None)
    order = dict(order)
    order["internal_order_id"] = "site_T"
    hold = orr.site_np_hold_reason(order, None)
    if hold:
        return ("HOLD", hold, order)
    t = orr.build_toysi_order(order)
    return ("PASS", t, order)


def passes(label, payload, wh, **expect):
    kind, t, _o = run(payload)
    ok = kind == "PASS" and t.get("shipping_warehouse_id") == wh
    for k, v in expect.items():
        if k == "city_id":
            ok = ok and (bool(t.get("shipping_city_id")) == v)
        elif k == "city":
            ok = ok and t.get("shipping_city_name") == v
        elif k == "names":
            ok = ok and (t.get("first_name"), t.get("last_name"), t.get("middle_name")) == v
        elif k == "phone":
            ok = ok and t.get("phone") == v
        elif k == "addr_has":
            ok = ok and v in t.get("shipping_address", "")
        elif k == "moneyback":
            ok = ok and t.get("moneyback") == v
    chk(f"PASS  {label}", ok)


def holds(label, payload, why):
    kind, t, _o = run(payload)
    chk(f"HOLD  {label}", kind == "HOLD" and why in t)


def refuses(label, payload, why):
    kind, t, _o = run(payload)
    chk(f"REFUSE {label}", kind == "REFUSE" and why in t)


# ── вибір з автокомпліту ────────────────────────────────────────────────────────────────────
passes("A1 автокомпліт: Київ, відділення 15 → wh=15, CityRef є, місто «Київ, Київська обл.»", base(), "15", city_id=True, city="Київ, Київська обл.", addr_has="Відділення №15")
passes("A2 автокомпліт: поштомат НП №1234 → wh=1234", base(warehouse_name="Поштомат НП №1234: вул. Шевченка, 5", np_warehouse_number="1234"), "1234", city_id=True)
passes("A3 автокомпліт: село-тезка Іванівка Харківська → область в shipping_city (інцидент «Южное»)", base(city_name="Іванівка", np_city_area="Харківська область", np_warehouse_number="1"), "1", city_id=True, city="Іванівка, Харківська обл.")
passes("A4 автокомпліт: сміттєвий np_city_ref відкидається (CityRef немає), номер лишається", base(np_city_ref="'; DROP--"), "15", city_id=False)
holds("A5 автокомпліт: np_warehouse_number не цифри («15abc») → утримано", base(np_warehouse_number="15abc"), "нема валідного номера")
passes("A6 дубль міста/області в адресі прибрано (EVA 8-081850129): shipping_address без «Київ (Київська обл.),»", base(), "15", addr_has="Відділення №15")
_k, _t, _o = run(base())
chk("A6b …і shipping_address НЕ містить назви міста вдруге", "Київ (Київська" not in _t["shipping_address"])

# ── ручний ввід відділення (місто унікальне) ──────────────────────────────────────────────────
for label, wh_text, num in (("M1 «369»", "369", "369"), ("M2 «№369»", "№369", "369"), ("M3 «відділення 12 біля метро»", "відділення 12 біля метро", "12"), ("M4 «НП 369»", "НП 369", "369"),
                            ("M5 «369-е відділення»", "369-е відділення", "369"), ("M6 «поштомат 4321»", "поштомат 4321", "4321"), ("M7 «(№5)»", "(№5)", "5"), ("M8 з переносом «відділення\n12»", "відділення\n12", "12"),
                            ("M9 «відділення166» (цифри впритул — не №6)", "відділення166", "166")):
    passes(f"{label} → wh={num}", manual(warehouse_name=wh_text), num, addr_has="введено вручну")
holds("M10 вулиця+будинок «вул. Шевченка 12» (не відділення)", manual(warehouse_name="вул. Шевченка 12"), "нема валідного номера")
holds("M11 вулиця+будинок+кв. «вул. Шевченка 12, кв. 5»", manual(warehouse_name="вул. Шевченка 12, кв. 5"), "нема валідного номера")
holds("M12 «відділення 5, кв. 12» (два числа — не вгадуємо)", manual(warehouse_name="відділення 5, кв. 12"), "нема валідного номера")
holds("M13 «відд 5 і 7» (два числа)", manual(warehouse_name="відд 5 і 7"), "нема валідного номера")
holds("M14 «відділення 1 2»", manual(warehouse_name="відділення 1 2"), "нема валідного номера")
holds("M15 Укрпошта «відділення Укрпошти 5»", manual(warehouse_name="відділення Укрпошти 5"), "іншого перевізника")
holds("M16 «Justin 5» (інший перевізник)", manual(warehouse_name="Justin відділення 5"), "іншого перевізника")
holds("M17 індекс «01001»", manual(warehouse_name="01001"), "нема валідного номера")
holds("M18 «Нова пошта» без номера", manual(warehouse_name="Нова пошта"), "нема валідного номера")
holds("M19 кур'єр «кур'єром на адресу: вул. Лесі 3, кв 40»", manual(warehouse_name="кур'єром на адресу: вул. Лесі Українки 3, кв 40"), "нема валідного номера")
holds("M20 «кв. №162» (номер квартири — не відділення)", manual(warehouse_name="кв. №162"), "нема валідного номера")
holds("M21 «під'їзд №1»", manual(warehouse_name="під'їзд №1"), "нема валідного номера")
holds("M22 «0»", manual(warehouse_name="0"), "нема валідного номера")

# ── ручне місто ─────────────────────────────────────────────────────────────────────────────
passes("C1 ручне «київ» малими (унікальне в НП) → йде з номером", manual(city_name="київ", warehouse_name="відділення 15"), "15", city="київ")
passes("C2 ручне «м. Львів» → префікс знято", manual(city_name="м. Львів", warehouse_name="відділення 3"), "3", city="Львів")
passes("C3 ручне «Київ (Київська обл.)» → область виділено", manual(city_name="Київ (Київська обл.)", warehouse_name="відділення 3"), "3", city="Київ, Київська обл.")
CITY_COUNT["іванівка"] = 37
holds("C4 ручне село-тезка «Іванівка» без області (НП: 37 збігів) → УТРИМАНО (Бородянський / «Южное»)", manual(city_name="Іванівка", warehouse_name="відділення 1"), "збігів за назвою: 37")
CITY_COUNT["ксямлвпр"] = 0
holds("C5 ручне місто, якого нема в довіднику НП → утримано", manual(city_name="Ксямлвпр", warehouse_name="відділення 1"), "збігів за назвою: 0")
orr.count_cities_named = lambda c: (_ for _ in ()).throw(RuntimeError("НП недоступна"))
holds("C6 довідник НП недоступний → fail-closed (утримано)", manual(city_name="Київ", warehouse_name="відділення 15"), "довідник НП недоступний")
passes("C7 …але вибір з автокомпліту (CityRef є) НП не питає: тезка з областю ПРОХОДИТЬ", base(city_name="Іванівка", np_city_area="Харківська", np_warehouse_number="1"), "1", city_id=True, city="Іванівка, Харківська обл.")
orr.count_cities_named = lambda city: CITY_COUNT.get(city.strip().lower(), CITY_COUNT["default"])
passes("C8 «Київ, Оболонь» (кома в назві) → місто «Київ» (поведінка master збережено)", manual(city_name="Київ, Оболонь", warehouse_name="відділення 5"), "5", city="Київ")
passes("C9 кома ВСЕРЕДИНІ дужок «Троїцьке (Одеська обл., Біляївський р-н)» → область «Одеська» не губиться (CityRef є)", base(city_name="Троїцьке", np_city_area="Одеська", np_warehouse_number="3"), "3", city="Троїцьке, Одеська обл.")

# ── ПІБ ─────────────────────────────────────────────────────────────────────────────────────
passes("N1 «Іван Петренко» → Ім'я/Прізвище", base(name="Іван Петренко"), "15", names=("Іван", "Петренко", ""))
passes("N2 «Петренко Іван Олексійович» (Прізвище Ім'я По-батькові) → поля НЕ переставлено (Toysi 100449125)", base(name="Петренко Іван Олексійович"), "15", names=("Іван", "Петренко", "Олексійович"))
passes("N3 «Іван Олексійович Петренко»? 3 слова, по-батькові не третє → лишається «Ім'я Прізвище…» (неоднозначно, не гадаємо)", base(name="Іван Олексійович Петренко"), "15", names=("Іван", "Олексійович Петренко", ""))
passes("N4 апостроф «Мар’яна Дем’янчук»", base(name="Мар’яна Дем’янчук"), "15", names=("Мар’яна", "Дем’янчук", ""))
passes("N5 подвійне прізвище «Анна Коваль-Шевченко»", base(name="Анна Коваль-Шевченко"), "15", names=("Анна", "Коваль-Шевченко", ""))
refuses("N6 одне слово «Іван»", base(name="Іван"), "імʼя та прізвище")
refuses("N7 порожнє ім'я", base(name="  "), "імʼя та прізвище")

# ── телефон ─────────────────────────────────────────────────────────────────────────────────
for label, ph in (("P1 0501234567", "0501234567"), ("P2 «050 123 45 67»", "050 123 45 67"), ("P3 «+38 (050) 123-45-67»", "+38 (050) 123-45-67"), ("P4 «380501234567»", "380501234567")):
    passes(f"{label} → 380501234567 (12 цифр без «+», Toysi code 16)", base(phone=ph), "15", phone="380501234567")
for label, ph in (("P5 9 цифр", "501234567"), ("P6 «8050…»", "80501234567"), ("P7 два номери", "0501234567, 0671234567"), ("P8 літери", "abc"), ("P9 порожній", "")):
    refuses(f"{label} → відмова з поясненням", base(phone=ph), "номер телефону")

# ── гроші/кошик ──────────────────────────────────────────────────────────────────────────────
passes("G1 COD копійки (39,23 ₴) → накладений платіж цілими гривнями (НП відкидає копійки): 39", base(items=[{"id": "101", "qty": 1}]), "15", moneyback=39.0)
refuses("G2 порожній кошик", base(items=[]), "Кошик порожній")
refuses("G3 неіснуючий товар", base(items=[{"id": "999", "qty": 1}]), "недоступний")
refuses("G4 некоректна кількість 0", base(items=[{"id": "100", "qty": 0}]), "Некоректна кількість")
refuses("G5 порожні місто/відділення", base(city_name="", warehouse_name=""), "Оберіть місто та відділення")

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
