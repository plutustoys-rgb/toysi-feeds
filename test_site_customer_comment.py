#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_customer_comment.py — 13.10.2026 (запит власника): поле «Коментар до замовлення» у чекауті. Контракт з Код: order["customer_comment"] — рядок ≤300, очищений (керуючі символи й розриви рядків → пробіл,
пробіли стиснуто); Код зберігає його в orders.customer_comment і додає в comment для Toysi. Перевіряємо: розмітка форми, відправку з фронта, серверне очищення (типи, довжина, керуючі символи, юнікод)."""
import sys
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "site"))
import site_order_api as so  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


so.price_map = lambda: {"100": {"price": 250, "name": "Тест"}}
base = {"items": [{"id": "100", "qty": 1}], "name": "Іван Петренко", "phone": "0501234567", "city_name": "Київ", "warehouse_name": "відділення 5", "payment_method": "cod"}
NUL, TAB, CR, LF = chr(0), chr(9), chr(13), chr(10)

o, _ = so.build_order(dict(base, comment="Це подарунок, зателефонуйте перед відправкою"))
chk("коментар доходить у order[customer_comment] дослівно", o["customer_comment"] == "Це подарунок, зателефонуйте перед відправкою")
o, _ = so.build_order(base)
chk("без коментаря: порожній рядок (ключ є, значення '')", o["customer_comment"] == "")
for bad in (None, 123, ["a"], {"a": 1}, True):
    o, _ = so.build_order(dict(base, comment=bad))
    chk(f"не рядок ({bad!r}) → порожньо, замовлення не падає", o["customer_comment"] == "")
o, _ = so.build_order(dict(base, comment="x" * 1000))
chk("довжина обрізається до 300", len(o["customer_comment"]) == 300)
o, _ = so.build_order(dict(base, comment="  рядок" + CR + LF + "другий" + TAB + "третій" + NUL + "кінець  "))
chk("розриви рядків, табуляція, NUL → один пробіл; без керуючих символів і подвійних пробілів", o["customer_comment"] == "рядок другий третій кінець" and all(ord(ch) >= 32 for ch in o["customer_comment"]))
o, _ = so.build_order(dict(base, comment="Привіт 🎁 «подарунок» <b>x</b> & Ñ"))
chk("юнікод, емодзі, лапки, HTML-символи лишаються як текст (екранування — справа споживача)", o["customer_comment"] == "Привіт 🎁 «подарунок» <b>x</b> & Ñ")
o, _ = so.build_order(dict(base, comment=chr(0x2028) + "a" + chr(0x2029) + "b"))
chk("U+2028/U+2029 і C1-керуючі → пробіл", o["customer_comment"] == "a b")
o, _ = so.build_order(dict(base, comment=" " * 50 + "ab"))
chk("лише пробіли + текст → обрізано пробіли", o["customer_comment"] == "ab")
o, _ = so.build_order(dict(base, comment=("а" * 299) + " " + "б"))
chk("обрізка не лишає кінцевого пробілу", not o["customer_comment"].endswith(" ") and len(o["customer_comment"]) <= 300)

src = (HERE / "site" / "build_site.py").read_text(encoding="utf-8")
chk("форма: textarea #f-comment (maxlength=300, необов'язкове), підпис «Коментар до замовлення», перед способом оплати", 'id="f-comment" name="comment" rows="3" maxlength="300"' in src and "Коментар до замовлення" in src and src.index('id="f-comment"') < src.index("Спосіб оплати</label>"))
app = (HERE / "site" / "assets" / "app.js").read_text(encoding="utf-8")
chk("фронт відправляє comment (обрізаний до 300) у payload", 'comment:(document.getElementById("f-comment")' in app and ".slice(0,300)" in app)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
