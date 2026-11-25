# -*- coding: utf-8 -*-
"""test_social_poster_filters.py — відсів і ротація товарів для соц-постера (запит SMM/власника 25.11: водяні пістолети в IG взимку). Офлайн, синтетичні дані."""
import sys
from datetime import date

sys.path.insert(0, ".")
import social_auto_poster as sp

fails = 0


def chk(name, ok):
    global fails
    print(("[OK ] " if ok else "[FAIL] ") + name)
    if not ok:
        fails += 1


def P(sku, name, price="200.00 UAH"):
    return {"sku": str(sku), "name": name, "url": "https://x/%s" % sku, "image": "https://i/%s.jpg" % sku, "price": price}


items = [P(1, "Водяний пістолет великий"), P(2, "Бластер Nerf"), P(3, "Літній надувний круг"), P(4, "Лялька Барбі Модниця"), P(5, "Конструктор Лего Сіті"),
         P(6, "Пазл 100 елементів"), P(7, "Настільна гра Діно"), P(8, "Набір для творчості Слайм"), P(9, "М'яка іграшка Ведмедик"), P(10, "Дешева дрібничка", "50.00 UAH"),
         P(11, "Дорогий набір", "2500.00 UAH"), P(12, "Водяна кулька літня")]
winter = sp._safe_and_rotated(items, today=date(2026, 11, 25))
skus = [p["sku"] for p in winter]
chk("зброя/імітація (пістолет, бластер) НЕ береться", "1" not in skus and "2" not in skus)
chk("взимку водяні/літні/надувні НЕ беруться", "3" not in skus and "12" not in skus)
chk("ціна поза 80–1500 не береться (50 і 2500)", "10" not in skus and "11" not in skus)
chk("придатні лишаються (6 тем по одному)", set(skus) == {"4", "5", "6", "7", "8", "9"})
summer = sp._safe_and_rotated(items, today=date(2026, 7, 15))
chk("влітку літній/надувний круг дозволений, зброя — ні", "3" in [p["sku"] for p in summer] and "1" not in [p["sku"] for p in summer] and "2" not in [p["sku"] for p in summer])
a = [p["sku"] for p in sp._safe_and_rotated(items, today=date(2026, 11, 25))]
b = [p["sku"] for p in sp._safe_and_rotated(items, today=date(2026, 11, 26))]
chk("ротація: наступного дня першою йде інша тема", a[0] != b[0])
chk("ціна невідома (порожня) — товар не відсіюється за ціною", [p["sku"] for p in sp._safe_and_rotated([P(20, "Пазл казковий", "")], today=date(2026, 11, 25))] == ["20"])
print("\nALL OK" if not fails else "\nFAILS: %d" % fails)
sys.exit(1 if fails else 0)
