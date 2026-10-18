#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_footer_hub_link.py — 19.10.2026 (власник: «прибрати категорії у футері, а посилання «Усі категорії товарів» зробити перед «Про нас»»).
Заміняє test_site_footer_cats (16.10, перелік 30 категорій у футері): тепер у футері кожної сторінки ПЕРШИМ у верхньому рядку — посилання на хаб /categories.html, перелік категорій прибрано."""
import os
import re
import sys
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


f = bs.footer()
nav = re.search(r'<nav class="fnav">(.*?)</nav>', f, re.S).group(1)
links = re.findall(r'<a href="([^"]+)">([^<]*)</a>', nav)
chk("верхній рядок футера: першим «Усі категорії товарів» → categories.html, одразу перед «Про нас»", links[:2] == [("categories.html", "Усі категорії товарів"), ("about.html", "Про нас")])
chk("решта пунктів на місці: Доставка, Повернення, Контакти, оферта, політика", [t for _u, t in links[2:]] == ["Доставка", "Повернення", "Контакти", "Публічна оферта", "Політика конфіденційності"])
chk("переліку категорій у футері нема (власник 19.10): ні nav.fcats, ні посилань на category-…", 'class="fcats"' not in f and 'href="category-' not in f and not hasattr(bs, "FOOTER_CATS"))
css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
chk("CSS .fcats прибрано разом із блоком", "fcats" not in css)
_a = '<main>X</main><nav class="fnav"><a href="categories.html">Усі категорії товарів</a> · <a href="about.html">Про нас</a></nav>'
_b = '<main>X</main><nav class="fnav"><a href="about.html">Про нас</a></nav>'
chk("lastmod: додавання посилання на хаб у футер НЕ рухає lastmod сторінок (глобальний «хром»; інакше усі 20 тис. карток отримали б однакову дату)", bs._LASTMOD_FLOAT_RX.sub("", _a) == bs._LASTMOD_FLOAT_RX.sub("", _b))
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
