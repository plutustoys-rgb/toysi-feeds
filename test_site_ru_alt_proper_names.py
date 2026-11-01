#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_ru_alt_proper_names.py — гейт «власні назви» для site/category_ru_alt.json (SEO 01.11).
Власна назва (марка) у рядку «Також шукають» мусить бути в назвах товарів ЦІЄЇ категорії; загальні слова — ні.
  1. «конструктор Wader» на категорії, де нема жодного Wader → порушення (рівно хибний випадок SEO)
  2. «погремушки для новорождённых» на «Брязкальцях» → чисто (заради цього рядок і існує)
  3. марка є в назвах → чисто; транслітерація марки з назви категорії → чисто; виняток із ALLOW → чисто
  4. фактичний site/category_ru_alt.json проти порожнього каталогу не падає (лише повертає список)"""
import sys
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
import build_site as bs  # noqa: E402

fails = []


def check(name, cond):
    print(("✅ " if cond else "❌ ") + name)
    if not cond:
        fails.append(name)


cats = {
    "Дерев'яні конструктори": [{"name": "Конструктор дерев'яний Автомобіль"}, {"name": "Wader Вантажівка"}],
    "Брязкальця": [{"name": "Брязкальце Мишка"}],
    "Пазли Castorland": [{"name": "Пазл 500 елементів"}],
    "Антистрес": [{"name": "М'яка іграшка-антистрес"}],
}
slug = {c: s for c, s in zip(cats, ["derevyani-konstruktory", "bryazkaltsya", "pazly-castorland", "antistres"])}
bad = bs.ru_alt_proper_name_issues({"derevyani-konstruktory": "конструктор Lego"}, cats, slug)
check("марка Lego без опори в категорії → порушення", [b[2] for b in bad] == ["Lego"])
bad = bs.ru_alt_proper_name_issues({"antistres": "антистрес Bubble Pops"}, cats, slug)
check("Bubble Pops без опори → порушення", {b[2] for b in bad} == {"Bubble", "Pops"} or "Bubble" in {b[2] for b in bad})
check("загальні слова на «Брязкальцях» не караються", bs.ru_alt_proper_name_issues({"bryazkaltsya": "погремушки для новорождённых, погремушка"}, cats, slug) == [])
check("марка Wader є в назвах категорії → чисто", bs.ru_alt_proper_name_issues({"derevyani-konstruktory": "конструктор Wader"}, cats, slug) == [])
check("виняток ALLOW (Касторленд) → чисто", bs.ru_alt_proper_name_issues({"pazly-castorland": "пазлы Касторленд"}, cats, slug) == [])
check("slug без сторінки не падає й не дає порушення", bs.ru_alt_proper_name_issues({"nema-takoi": "Lego"}, cats, slug) == [])
check("фактичний category_ru_alt.json не падає на порожньому каталозі", isinstance(bs.ru_alt_proper_name_issues(bs._load_category_ru_alt(), {}, {}), list))
print("ALL OK" if not fails else f"FAIL: {len(fails)}")
sys.exit(1 if fails else 0)
