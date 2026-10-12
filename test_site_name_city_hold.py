#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_name_city_hold.py — два закриття дір адресного шляху САЙТУ (12.10.2026, таблиця 42 сценаріїв Shopify-code):
 1) ПІБ: «Петренко Іван Олексійович» на сайті йшло як ім'я «Петренко» / прізвище «Іван Олексійович» (клас Toysi 100449125). Рівно 3 слова + останнє схоже на по-батькові →
    «Прізвище Ім'я По-батькові»; 2 слова, 4 слова, не-по-батькові третє слово — як було (ім'я, прізвище); Rozetka/EVA/Prom без змін.
 2) Ручне місто (довідник НП на фронті недоступний): без np_city_ref і без області Toysi дістав би лише назву → тезка-село. Утримуємо (site_np_hold_reason), якщо у довіднику НП
    за точною назвою != 1 збігів або НП недоступна (fail-closed); з np_city_ref або областю в np_branch — НП не викликається взагалі.
Мережа замокана. `python test_site_name_city_hold.py` → exit 0/1."""
import sys

import order_router as orr

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


sp = orr._split_recipient_name
chk("site: «Петренко Іван Олексійович» → прізв./ім'я/по-батькові", sp("Петренко Іван Олексійович", "site") == ("Іван", "Петренко", "Олексійович"))
chk("site: жіноче по-батькові «Коваль Оксана Іванівна»", sp("Коваль Оксана Іванівна", "site") == ("Оксана", "Коваль", "Іванівна"))
chk("site: «Лукіївна/Ілліч/Кузьмич» теж по-батькові", all(sp(f"Іваненко Марія {x}", "site")[2] == x for x in ("Лукіївна", "Ілліч", "Кузьмич", "Миколаївна", "Ярославович", "Євгенович", "Андрійович", "Ігорович")))
chk("site: 2 слова — як було (ім'я, прізвище)", sp("Іван Петренко", "site") == ("Іван", "Петренко", ""))
chk("site: 3 слова без по-батькові («Іван Петренко-Коваль Старший») — як було", sp("Іван Петренко Старший", "site") == ("Іван", "Петренко Старший", ""))
chk("site: 4 слова — як було", sp("Петренко Іван Олексійович Мл", "site") == ("Петренко", "Іван Олексійович Мл", ""))
chk("site: 1 слово", sp("Іван", "site") == ("Іван", "Невідомо", ""))
chk("rozetka без змін", sp("Петренко Іван Олексійович", "rozetka") == ("Іван", "Петренко", "Олексійович"))
chk("eva без змін", sp("Петренко Іван", "eva") == ("Іван", "Петренко", ""))
chk("prom без змін (3 слова з по-батькові — як було: ім'я, решта прізвище)", sp("Іван Петренко Олексійович", "prom") == ("Іван", "Петренко Олексійович", ""))


def order(**kw):
    base = dict(internal_order_id="site_T1", order_id="T1", platform="site", carrier="nova_poshta", customer_name="Іван Петренко", phone="380671112233",
                payment_method="cod", np_branch="Іванівка, Відділення №5", np_warehouse_number="5", np_city_ref=None,
                items=[{"toysi_code": "1", "name": "x", "qty": 1, "price": 100}])
    base.update(kw)
    return base


calls = []


def fake(n):
    def f(city, *a, **k):
        calls.append(city)
        if isinstance(n, Exception):
            raise n
        return n
    return f


orr.count_cities_named = fake(7)
r = orr.site_np_hold_reason(order(), None)
chk("ручне місто-тезка без області й CityRef → утримуємо", "збігів за назвою: 7" in r and "Іванівка" in r)
orr.count_cities_named = fake(0)
chk("міста нема в довіднику НП → утримуємо", "збігів за назвою: 0" in orr.site_np_hold_reason(order(), None))
orr.count_cities_named = fake(1)
chk("унікальне ручне місто → не утримуємо", orr.site_np_hold_reason(order(np_branch="Київ, Відділення №5"), None) == "")
orr.count_cities_named = fake(RuntimeError("НП лежить"))
chk("НП недоступна → утримуємо (fail-closed)", "недоступний" in orr.site_np_hold_reason(order(), None))
calls.clear()
orr.count_cities_named = fake(7)
chk("є np_city_ref (автокомпліт) → НП не викликається, не утримуємо", orr.site_np_hold_reason(order(np_city_ref="8d5a980d-391c-11dd-90d9-001a92567626"), None) == "" and not calls)
chk("є область в np_branch → НП не викликається, не утримуємо",
    orr.site_np_hold_reason(order(np_branch="Іванівка (Одеська обл.), Відділення №5"), None) == "" and not calls)
chk("не site (prom) → НП не викликається, не утримуємо", orr.site_np_hold_reason(order(platform="prom", np_warehouse_number=None), None) == "" and not calls)
chk("номер відділення відсутній → стара причина (раніше за перевірку міста)", "нема валідного номера" in orr.site_np_hold_reason(order(np_warehouse_number=None, np_branch="Іванівка"), None) and not calls)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
