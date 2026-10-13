#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_return_line.py — 13.10.2026 (SMM/власник): біля «У кошик» на картці в наявності й у кошику один короткий рядок «Повернення якісного товару — 14 днів; зворотна пересилка за рахунок покупця» — рівно зміст сторінки «Повернення»
(14 днів, товар належної якості; зворотну пересилку товару належної якості несе покупець). Не додаємо: розділу про уцінку на returns.html, рядків «поверненню не підлягає» (рішення власника)."""
import os
import re
import sys
import tempfile
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


LINE = "Повернення якісного товару — 14 днів; зворотна пересилка за рахунок покупця"
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
p = {"id": "9501", "name": "Іграшка «Лінія»", "category": "Кат", "price": 250, "contribution": 100, "stock": 3, "photo": "data:image/gif;base64,R0lGODlhAQABAAAAACw=", "desc": ""}
oos = dict(p, id="9502", stock=0)
mk = dict(p, id="9503", name="Уцінка. Іграшка «Дефект»", category="Уцінка")
for x in (p, oos, mk):
    bs.write_product(x, related=None, cat_slug={}, similar=[])
bs.write_cart([])
pg = (out / "product-9501.html").read_text(encoding="utf-8")
chk("картка в наявності: рівно один рядок про повернення з точним текстом", pg.count(f'<p class="ret-line">{LINE}</p>') == 1)
chk("рядок стоїть ДО блоку «У кошик»/довіри (поряд із покупкою), не в кінці сторінки", pg.index("ret-line") < pg.index("trust-badges") and pg.index("ret-line") < pg.index('data-add='))
chk("картка без залишку: рядка нема (кнопка вимкнена)", "ret-line" not in (out / "product-9502.html").read_text(encoding="utf-8"))
chk("уцінений товар: той самий рядок (умови повернення не міняємо), БЕЗ «поверненню не підлягає»", "ret-line" in (out / "product-9503.html").read_text(encoding="utf-8") and "не підлягає" not in (out / "product-9503.html").read_text(encoding="utf-8"))
ct = (out / "cart.html").read_text(encoding="utf-8")
chk("кошик: один рядок про повернення під списком товарів", ct.count(f'class="ret-line ret-cart">{LINE}</p>') == 1 and ct.index('id="cart-body"') < ct.index("ret-cart"))
ret = re.sub(r"<[^>]+>", " ", bs._RETURNS)
chk("зміст збігається зі сторінкою «Повернення»: 14 днів, належної якості, зворотну пересилку несе покупець; розділу про уцінку там НЕМАЄ", "14 днів" in ret and "належної якості" in ret and "несе покупець" in ret and "уцін" not in ret.lower())
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
