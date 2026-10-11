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
            "369 відділення", "Нова Пошта №369", "нова пошта 369", "Відділення № 369.",
            # аудит #671: природні форми, які перша версія пропускала (→ знову адресна доставка)
            "Нової пошти 369", "369 відділення Нової пошти", "відділення нової пошти 369", "Нова почта 369", "Новая почта 369",
            "отделение 369", "369 отд", "відділеня 369", "369!", "(369)", "«369»", "369-е відділення", "369 відділення, Хрещатик",
            "біля метро відділення 369", "Київ відділення 369", "٣٦٩"]:
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
for txt in ["вул. Хрещатик 22", "Хрещатик, 22", "12 і 13", "369 та 370", "кв. 5", "0", "00", "відділення", "№", "123456", "біля метро",
            "01001", "79000", "3 №69", "N369", "Відділок 5", "Ніжин 5", "вул. 8 Березня", "мікрорайон 5, відділення", "відділення на Шевченка 5",
            "вул 3, відділення 5", "5 лінія", "км 12", "10.10.2026", "0501234567"]:
    o = order_for("Київ", txt)
    chk(f"«{txt}» НЕ розпізнається (np_warehouse_number=None, текст як є)", o["np_warehouse_number"] is None and o["np_branch"].endswith(txt))

# ── аудит #671 (2-й раунд): адреси з «#», «номер», «n», слова на «нов…пошт…», інші перевізники, гігантські цифри
for txt in ["домофон #1234", "код домофона #1234", "кв #12", "вул Київська #5", "будинок номер 5", "квартира номер 5", "кв. N 5",
            "вул. Київська n 5", "Хрещатик №22", "кв. №5", "під'їзд №2", "мономер 5", "автономер 7",
            "5 відділення Укрпошти", "5 відділення Justin", "5 відділення Meest", "Укрпошта відділення 5", "Justin відділення 5",
            "вул. Нова Поштова 5", "Новомосковськ Поштова 12", "Новоселівка, Поштова 7", "вул. Іванова Поштова 3", "Нове Почтове 9",
            "вул. Центральна 5, відділення", "вул. Київська 5 відділення 10", "НП5", "нп Ніжин 5", "1" * 4301, "5" * 400]:
    o = order_for("Київ", txt)
    chk(f"НЕ розпізнається як відділення: «{txt[:40]}»", o["np_warehouse_number"] is None and o["np_branch"].endswith(txt))
for txt in ["#369", "N 369", "номер 369"]:
    o = order_for("Київ", txt)
    chk(f"лише номер + службовий символ/слово (правило 1) → 369: «{txt}»", o["np_warehouse_number"] == "369")
chk("5 цифр + «поштомат» після числа → є", order_for("Київ", "12345 поштомат")["np_warehouse_number"] == "12345")

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
chk("з областю: np_branch починається «Київ (Київська обл.), Відділення №369»", o["np_branch"].startswith("Київ (Київська обл.), Відділення №369"))

# ── початковий текст покупця не губиться (аудит #671, ВАЖЛИВО-2)
o = order_for("м. Київ", "Нової пошти 369")
chk("оригінал збережено в np_branch: «Нової пошти 369»", "«Нової пошти 369»" in o["np_branch"] and "Відділення №369" in o["np_branch"])
o = order_for("Київ", 'відд. «369» (біля "метро")\n')
chk("небезпечні символи оригіналу (дужки, лапки, перенос) прибрано, ярлик цілий", "\n" not in o["np_branch"] and o["np_branch"].count("(") == 1 and o["np_branch"].count(")") == 1)
o = order_for("Київ", "відділення 369 " + "а" * 100)
chk("довгий оригінал обрізано ≤40 символів", len(o["np_branch"]) < 120)

# ── наскрізно: до того, що побачить Toysi (інцидент: shipping_warehouse_id не виставлявся)
import datetime  # noqa: E402
for txt in ["369", "№369", "Нової пошти 369", "369 відділення"]:
    o = order_for("м. Київ", txt, np_city_area="Київська")
    o["internal_order_id"] = o["order_id"]
    o["created_at"] = datetime.datetime.now().isoformat()
    t = order_router.build_toysi_order(o)
    chk(f"Toysi: «{txt}» → shipping_warehouse_id='369', адреса не порожня, місто з областю",
        t is not None and t.get("shipping_warehouse_id") == "369" and "369" in t.get("shipping_address", "") and "Київ" in t.get("shipping_city_name", ""))
o = order_for("м. Київ", "Хрещатик, 22")
o["internal_order_id"] = o["order_id"]
o["created_at"] = datetime.datetime.now().isoformat()
t = order_router.build_toysi_order(o)
chk("Toysi: адреса вулиці БЕЗ номера відділення НЕ отримує shipping_warehouse_id (поведінка не змінена)", t is not None and "shipping_warehouse_id" not in t)

# ── поштомат: 5 цифр лише зі словом «поштомат»
chk("5 цифр без «поштомат» → None, зі словом → є", order_for("Київ", "55902")["np_warehouse_number"] is None and order_for("Київ", "Поштомат 55902")["np_warehouse_number"] == "55902")

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
