#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_manual_warehouse.py — інцидент 10.10.2026, замовлення PT-20261010-142322-afbf46: покупець ВПИСАВ відділення руками («369»),
`np_branch` став «м. Київ, 369», order_router.parse_np_branch не витяг номер → Toysi прийняв замовлення як АДРЕСНУ доставку.
Інваріанти build_order:
  • однозначні ручні форми («369», «№369», «відділення 369», «Відділення №369», «відд. 369», «НП 369», «369 відділення», «Нова Пошта №369»,
    «Поштомат №55902») → np_warehouse_number = цифри, а parse_np_branch(np_branch) і сам знаходить цей номер (подвійний захист);
  • вибір з автокомпліту (np_warehouse_number від клієнта) НЕ перезаписується;
  • неоднозначне («вул. Хрещатик 22», «12 і 13», «0», порожньо-службове) НЕ вгадується: np_warehouse_number лишається None, текст не змінений;
  • поле відділення, як і раніше, не вимагає номера (сервер приймає будь-який непорожній текст) — «НЕ ламати» №1 передачі сайту.
Мережа не потрібна."""
import os
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["ORDERS_DB_PATH"] = tempfile.mktemp(suffix=".db")

import order_router  # noqa: E402
import site_order_api as api  # noqa: E402

api.price_map = lambda: {"1": {"price": 100, "name": "Тестова іграшка"}}   # без читання site/index.json
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def order_for(city, warehouse, **extra):
    payload = {"items": [{"id": "1", "qty": 1}], "name": "Іван Петренко", "phone": "0501234567",
               "city_name": city, "warehouse_name": warehouse, "payment_method": "cod"}
    payload.update(extra)
    return api.build_order(payload)[0]


# ── однозначні ручні форми
for txt in ["369", " 369 ", "№369", "№ 369", "#369", "відділення 369", "Відділення №369", "відд. 369", "відд 369", "НП 369", "Н.П. 369",
            "369 відділення", "Нова Пошта №369", "нова пошта 369", "Відділення № 369."]:
    o = order_for("м. Київ", txt)
    _, wh, _ = order_router.parse_np_branch(o["np_branch"])
    chk(f"«{txt}» → np_warehouse_number='369'", o["np_warehouse_number"] == "369")
    chk(f"«{txt}» → parse_np_branch знаходить 369 (текст нормалізовано: {o['np_branch']!r})", wh == "369")

for txt, num in [("Поштомат №55902", "55902"), ("поштомат 55902", "55902"), ("Поштомат Нова Пошта №1234", "1234")]:
    o = order_for("Київ", txt)
    chk(f"«{txt}» → np_warehouse_number={num} і підпис «Поштомат»", o["np_warehouse_number"] == num and "Поштомат №" + num in o["np_branch"])

o = order_for("Київ", "0369")
chk("провідний нуль не ламає: «0369» → 369", o["np_warehouse_number"] == "369")

# ── вибір з автокомпліту не перезаписується
o = order_for("Київ", "Відділення №15 (до 30 кг): вул. Хрещатик, 1", np_warehouse_number="15", np_city_ref="48520e71-2122-11ec-a344-b8830365bd14")
chk("автокомпліт: np_warehouse_number='15' збережено", o["np_warehouse_number"] == "15")
chk("автокомпліт: текст відділення не переписано", "вул. Хрещатик, 1" in o["np_branch"])
o = order_for("Київ", "369", np_warehouse_number="15")
chk("автокомпліт має пріоритет над розпізнаним у тексті (15, не 369)", o["np_warehouse_number"] == "15")

# ── неоднозначне — не вгадуємо
for txt in ["вул. Хрещатик 22", "Хрещатик, 22", "12 і 13", "369 та 370", "кв. 5", "0", "00", "відділення", "№", "123456", "біля метро"]:
    o = order_for("Київ", txt)
    chk(f"«{txt}» НЕ розпізнається (np_warehouse_number=None, текст як є)", o["np_warehouse_number"] is None and o["np_branch"].endswith(txt))

# ── ручний ввід без номера все ще приймається (поле не блокується)
try:
    o = order_for("Київ", "біля метро Лук'янівська")
    chk("ручний текст без номера проходить валідацію", True)
except api.OrderError:
    chk("ручний текст без номера проходить валідацію", False)
for bad in ("", "   "):
    try:
        order_for("Київ", bad)
        chk(f"порожнє відділення {bad!r} відхилено", False)
    except api.OrderError:
        chk(f"порожнє відділення {bad!r} відхилено", True)

# ── область міста з автокомпліту не втрачається
o = order_for("Київ", "369", np_city_area="Київська")
chk("з областю: np_branch = «Київ (Київська обл.), Відділення №369»", o["np_branch"] == "Київ (Київська обл.), Відділення №369")

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
