#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_footer_cats.py — 16.10.2026 (SEO: ≥30 видимих текстових посилань на /category-… після прибирання блоку «Популярні категорії» з головної; власник: «якщо щось треба залишити для кращої індексації — залишай»).
У футері КОЖНОЇ сторінки — перелік 30 найбільших категорій (без «Уцінки») звичайними <a>, у порядку кількості товарів; не display:none, не <details>; плиток/пігулок на головному екрані нема."""
import contextlib
import io
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["SITE_PICK_DATE"] = "2026-10-10"
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402
import site_photo_probe  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


site_photo_probe.run = lambda idx: None


def item(pid, name, cat):
    return {"id": str(pid), "name": name, "vendor": "T", "price": 200 + pid % 200, "stock": 5, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/{pid}"}


catalog, pid = {}, 1
cats = [(f"Категорія {i:02d}", 40 - i) for i in range(34)] + [("Уцінка", 90), ('Кубики & <b>"Ціна"</b>', 3)]   # 34 звичайні (убувають), «Уцінка» найбільша, одна з небезпечними символами
for name, n in cats:
    for j in range(n):
        catalog[str(pid)] = item(pid, f"{name} товар {pid}", name)
        pid += 1
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.CATEGORY_GROUPS_FILE = str(Path(tempfile.mkdtemp()) / "none.json")
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()


def fc(name):
    h = (out / name).read_text(encoding="utf-8")
    m = re.search(r'<nav class="fcats" aria-label="Категорії іграшок">(.*?)</nav>', h, re.S)
    return h, m


home, m = fc("index.html")
links = re.findall(r'<a href="(category-[^"]+\.html)">([^<]*)</a>', m.group(1)) if m else []
chk(f"головна: у футері рівно {bs.FOOTER_CATS_MAX} посилань на категорії", m is not None and len(links) == 30)
chk("головна: усього ≥ 30 посилань на /category-… у HTML (критерій SEO)", len(re.findall(r'href="category-[^"]+\.html"', home)) >= 30)
chk("посилання ведуть на ІСНУЮЧІ сторінки; «Уцінки» нема; порядок за кількістю (Категорія 00 першою)", all((out / h).exists() for h, _ in links) and not any("Уцінк" in t for _, t in links) and links[0][1] == "Категорія 00" and [t for _, t in links][:3] == ["Категорія 00", "Категорія 01", "Категорія 02"])
chk("видимого напису «Категорії» нема (власник 16.10: «по переліку і так зрозуміло»), доступну назву лишає aria-label", 'class="fch"' not in home and ">Категорії<" not in m.group(0) and 'aria-label="Категорії іграшок"' in m.group(0))
chk("перелік — звичайні <a> у <nav>: без display:none, hidden, <details>", m is not None and "hidden" not in m.group(0) and "<details" not in m.group(0) and "display:none" not in m.group(0))
css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
rule = re.search(r"\.foot \.fcats\{([^}]*)\}", css)
chk("CSS .fcats не ховає блок (display:none/visibility:hidden/clip) і приглушений (12px)", rule is not None and "display:none" not in rule.group(1) and "hidden" not in rule.group(1) and "font-size:12px" in rule.group(1))
for pg in ("catalog.html", "category-kategoriia-00.html", "contacts.html"):
    if (out / pg).exists():
        h, mm = fc(pg)
        chk(f"{pg}: той самий перелік у футері", mm is not None and len(re.findall(r'<a href="category-', mm.group(1))) == 30)
prod = sorted(out.glob("product-*.html"))[0]
h, mm = fc(prod.name)
chk(f"{prod.name}: картка товару теж має перелік у футері", mm is not None and len(re.findall(r'<a href="category-', mm.group(1))) == 30)
chk("екрануються символи в назві (якщо потрапила у 30) — літерального <b> у футері нема", all("<b>" not in fc(p)[1].group(1) for p in ("index.html",)))
chk("плиток «Категорії»/пігулок «Популярні категорії» на головній НЕ повернуто", 'class="catrow"' not in home and 'class="catlinks"' not in home and "<h2>Популярні категорії</h2>" not in home and "<h2>Категорії</h2>" not in home)
_a = '<main>X</main><nav class="fcats" aria-label="Категорії іграшок"><a href="category-a.html">А</a></nav><p>t</p>'
_b = '<main>X</main><nav class="fcats" aria-label="Категорії іграшок"><a href="category-b.html">Б</a> · <a href="category-c.html">В</a></nav><p>t</p>'
chk("lastmod: зміна переліку категорій у футері НЕ рухає lastmod сторінки (глобальний «хром», як чипи/sr-pill)", bs._LASTMOD_FLOAT_RX.sub("", _a) == bs._LASTMOD_FLOAT_RX.sub("", _b) and "<main>X</main>" in bs._LASTMOD_FLOAT_RX.sub("", _a))
bs.FOOTER_CATS[:] = []
chk("без збірки (FOOTER_CATS порожній) блоку нема й footer() не падає", 'class="fcats"' not in bs.footer())
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
