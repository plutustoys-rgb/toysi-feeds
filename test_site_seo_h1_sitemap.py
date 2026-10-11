#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_seo_h1_sitemap.py — замовлення SEO 11.10.2026 + знахідки Lighthouse mobile:
  • H1 категорії = лише назва («Картини за номерами»), без «Каталог •»; повний каталог — «Каталог іграшок»;
  • вступний текст категорії (site/category_texts.json, слот під H1 над списком): лише 1-ша сторінка, абзаци за порожнім рядком, HTML екранується, нема тексту — нема блоку;
  • перші 4 картки списку без loading=lazy, першій fetchpriority=high (LCP-елемент списку був lazy: Lighthouse LCP 5,3–6,5 с);
  • sitemap: лише 1-ша сторінка категорії/каталогу (без `_N`), усі картки на місці; canonical і rel=prev/next на пагінації не змінено;
  • головна: «Популярні категорії» 24–30 посилань на існуючі сторінки категорій, без «Уцінки», у порядку кількості товарів.
Повний build() на синтетичному каталозі; мережа не потрібна."""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
for k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID"):
    os.environ.pop(k, None)
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def item(pid, name, category, price=300.0):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": 5, "category_name": category, "category_id": "10",
            "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "barcode": ""}


catalog = {}
pid = 1
big = "Картини за номерами"                                  # 60 товарів → 3 сторінки по 24
for i in range(60):
    catalog[str(pid)] = item(pid, f"Картина номер {i}", big, price=250 + i)
    pid += 1
for c in range(40):                                          # ще 40 категорій; кількість товарів спадає: Кат0 найбільша
    for j in range(max(1, 30 - c)):
        catalog[str(pid)] = item(pid, f"Товар {c}-{j}", f"Кат{c:02d}")
        pid += 1
for j in range(50):                                          # Уцінка: найбільша серед решти, але в «популярних» її бути не має
    catalog[str(pid)] = item(pid, f"Уцінка. Товар {j}", "Уцінка")
    pid += 1

out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.tp.fetch_toysi_catalog = lambda: catalog
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None

# тексти категорій: підміна файлу з текстами
texts_file = out / "category_texts.json"
texts_file.write_text(json.dumps({"kartyny-za-nomeramy": "Перший абзац <b>з тегом</b> & амперсандом.\n\nДругий абзац про картини.", "kat00": "", "nonexistent": "Нема такої категорії"}, ensure_ascii=False), encoding="utf-8")
bs._CATEGORY_TEXTS_FILE = str(texts_file)
bs.build()

slug = bs.slugify(big)
p1 = (out / f"category-{slug}.html").read_text(encoding="utf-8")
p2 = (out / f"category-{slug}_2.html").read_text(encoding="utf-8")
chk("H1 категорії = лише назва (без «Каталог •»), стор.1", re.search(r'<h1 class="page">Картини за номерами</h1>', p1) is not None and "Каталог •" not in p1)
chk("H1 категорії = лише назва, стор.2 (title/canonical пагінації не змінено)", re.search(r'<h1 class="page">Картини за номерами</h1>', p2) is not None
    and f'rel="canonical" href="https://plutustoys.com.ua/category-{slug}_2.html"' in p2 and "сторінка 2" in re.search(r"<title>(.*?)</title>", p2).group(1))
cat_html = (out / "catalog.html").read_text(encoding="utf-8")
chk("повний каталог: H1 «Каталог іграшок»", '<h1 class="page">Каталог іграшок</h1>' in cat_html)
chk("крихти «Каталог ›» на сторінці категорії лишились", 'href="catalog.html">Каталог</a>' in p1)

# вступний текст
chk("вступний текст: на 1-й сторінці, 2 абзаци, HTML екрановано", p1.count('<div class="cat-intro">') == 1 and p1.count("<p>Перший абзац &lt;b&gt;з тегом&lt;/b&gt; &amp; амперсандом.</p>") == 1 and "<p>Другий абзац про картини.</p>" in p1)
chk("вступний текст: на пагінації (стор.2) НЕ дублюється", "cat-intro" not in p2)
chk("вступний текст: стоїть між H1 і списком товарів", p1.index("<h1") < p1.index('class="cat-intro"') < p1.index('<div class="grid">'))
other = (out / f"category-{bs.slugify('Кат05')}.html").read_text(encoding="utf-8")
chk("категорія без тексту (і з порожнім значенням) — блоку нема", "cat-intro" not in other and "cat-intro" not in (out / "category-kat00.html").read_text(encoding="utf-8"))
chk("текст для неіснуючої категорії нічого не створює і не падає", not (out / "category-nonexistent.html").exists())
bs._CATEGORY_TEXTS_FILE = str(out / "nope.json")
chk("нема файлу / битий JSON → порожньо, без винятку", bs._load_category_texts() == {})
(out / "bad.json").write_text("{not json", encoding="utf-8")
bs._CATEGORY_TEXTS_FILE = str(out / "bad.json")
chk("битий JSON → порожньо, без винятку", bs._load_category_texts() == {})

# eager у списках
imgs = re.findall(r'<a class="card" href="product-\d+\.html"><div class="ph"><img ([^>]*)>', p1)
chk("список: перші 4 картки без lazy, решта — lazy", len(imgs) == 24 and all("lazy" not in x for x in imgs[:4]) and all("lazy" in x for x in imgs[4:]))
chk("список: fetchpriority=high лише в першої картки", 'fetchpriority="high"' in imgs[0] and sum(1 for x in imgs if "fetchpriority" in x) == 1)

# sitemap
sm = (out / "sitemap.xml").read_text(encoding="utf-8")
locs = re.findall(r"<loc>https://plutustoys.com.ua/([^<]*)</loc>", sm)
chk("sitemap: 1-ша сторінка категорії є, `_2`/`_3` немає", f"category-{slug}.html" in locs and not any(re.search(r"_\d+\.html$", x) for x in locs))
chk("sitemap: catalog.html є, catalog_2.html нема", "catalog.html" in locs and "catalog_2.html" not in locs)
prods_in = {x for x in locs if x.startswith("product-")}
chk(f"sitemap: усі {len(catalog)} карток на місці (шлях до карток не залежить від пагінації)", len(prods_in) == len(catalog))
chk("пагінація лишилась доступною: rel=next на стор.1 і rel=prev на стор.2", f'rel="next" href="category-{slug}_2.html"' in p1 and f'rel="prev" href="category-{slug}.html"' in p2)

# головна: популярні категорії
home = (out / "index.html").read_text(encoding="utf-8")
m = re.search(r'<nav class="catlinks" aria-label="Популярні категорії">(.*?)</nav>', home, re.S)
links = re.findall(r'<a href="(category-[^"]+\.html)">([^<]*) <span>(\d+)</span></a>', m.group(1)) if m else []
chk("головна: блок «Популярні категорії» з 30 посиланнями", m is not None and len(links) == 30 and "<h2>Популярні категорії</h2>" in home)
chk("головна: «Уцінки» серед популярних нема", not any("Уцінк" in t for _, t, _ in links))
chk("головна: посилання ведуть на ІСНУЮЧІ сторінки категорій", all((out / h).exists() for h, _, _ in links))
counts = [int(c) for _, _, c in links]
chk("головна: порядок за кількістю товарів (спадно), лічильник правильний (Картини за номерами = 60 першою)", counts == sorted(counts, reverse=True) and links[0][1] == "Картини за номерами" and counts[0] == 60)

# «Уцінка» не потрапляє в популярні й тоді, коли звичайних категорій МАЛО (інакше сортування «Уцінка в кінці» її б не ховало)
def _mk(i, cat):
    return {"id": str(9000 + i), "name": f"Т{i}", "price": 300, "category": cat, "photo": "https://x/1.jpg", "stock": 3, "desc": "", "contribution": 150.0}


few = {"Ляльки": [_mk(1, "Ляльки"), _mk(2, "Ляльки")], "Пазли": [_mk(3, "Пазли")], "Уцінка": [_mk(i, "Уцінка") for i in range(10, 15)]}
bs.write_home([p for v in few.values() for p in v], few, ["Ляльки", "Пазли", "Уцінка"], {c: bs.slugify(c) for c in few})
h3 = (out / "index.html").read_text(encoding="utf-8")
m3 = re.search(r'<nav class="catlinks"[^>]*>(.*?)</nav>', h3, re.S)
chk("мало категорій: у «Популярних» лише звичайні, «Уцінки» нема, рахунок правильний", m3 is not None and "Уцінк" not in m3.group(1) and ">Ляльки <span>2</span>" in m3.group(1) and ">Пазли <span>1</span>" in m3.group(1))

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ SEO: H1/слот тексту/eager/sitemap/популярні категорії — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
