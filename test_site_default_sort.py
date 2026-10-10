#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_default_sort.py — порядок «Рекомендовані» каталогу (рішення Консультанта 10.10.2026; зауваження Тестувальника: перший екран = велосипед 10 434 ₴).
Інваріанти: кошик A першим (фото, у наявності, НЕ уцінка, внесок >0, ціна 97–381 ₴), B — решта; порядок ДЕТЕРМІНОВАНИЙ (фіксоване зерно, не залежить від
порядку входу); дорогі/дешеві/уцінка не зникають, лише після A; на першому екрані немає уцінки й позицій без фото. Самодостатній, мережа не потрібна."""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "site"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import build_site as b  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def mk(i, price, cat="Машинки", name=None, photo="x.jpg", stock=3, contrib=50.0):
    return {"id": str(i), "name": name or f"Іграшка {i}", "price": price, "category": cat, "photo": photo, "stock": stock, "contribution": contrib, "desc": ""}


random.seed(5)
items = []
for i in range(300):
    items.append(mk(i, random.choice([17, 50, 150, 200, 380, 600, 1500, 10434])))
items.append(mk(9001, 150, cat="Уцінка", name="Уцінка. Машинка - пошкоджена упаковка"))
items.append(mk(9002, 150, photo=""))
items.append(mk(9003, 150, contrib=None))
items.append(mk(9004, 150, contrib=-5.0))
items.append(mk(9005, 150, stock=0))
items.append(mk(9006, 96))
items.append(mk(9007, 382))
items.append(mk(9008, 97))
items.append(mk(9009, 381))
A = [p for p in items if b._rec_bucket(p) == 0]
r = b.rec_order(items)
k = len(A)
chk("кошик A: рівно ті, що пройшли всі умови", {p["id"] for p in r[:k]} == {p["id"] for p in A} and k > 50)
chk("перший екран (24): усі ціни в 97–381", all(b.REC_PRICE_LO <= p["price"] <= b.REC_PRICE_HI for p in r[:24]))
chk("перший екран: немає уцінки/без фото/з невідомим чи від'ємним внеском/без залишку", all(p["photo"] and p["stock"] > 0 and not b._is_markdown_item(p) and p["contribution"] and p["contribution"] > 0 for p in r[:24]))
chk("межі включно: 97 і 381 — у A; 96 і 382 — ні", {"9008", "9009"} <= {p["id"] for p in r[:k]} and not ({"9006", "9007"} & {p["id"] for p in r[:k]}))
chk("ніщо не зникло: B після A, довжина та сама", len(r) == len(items) and {p["id"] for p in r} == {p["id"] for p in items})
shuf = items[:]
random.Random(1).shuffle(shuf)
chk("детермінізм: той самий результат при іншому порядку входу", [p["id"] for p in b.rec_order(shuf)] == [p["id"] for p in r])
chk("це не сортування за ціною/внеском: у A ≥3 різних цін поспіль на першому екрані", len({p["price"] for p in r[:24]}) >= 3)
chk("«Уцінка» розпізнається в назві й категорії (Уценка теж)", b._is_markdown_item(mk(1, 150, cat="Уцінка")) and b._is_markdown_item(mk(1, 150, name="Уценка. Лялька")) and not b._is_markdown_item(mk(1, 150)))
print()
if F:
    print("FAILED:", F)
    sys.exit(1)
print("ALL OK")
