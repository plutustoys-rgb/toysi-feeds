#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_brand_category.py — 21.10.2026 (SEO, варіант А; пілот «посилена брендова категорія» Trefl): НОВОГО URL нема, посилюється існуюча category-pazly-trefl.html:
унікальний текст 120–180 слів лише з наших даних (виробник, кількість моделей, діапазон деталей з назв, вік лише якщо є в назвах «3+», ціни від/до), title/H1 не змінені, JSON-LD CollectionPage
(BreadcrumbList і ItemList лишаються), sitemap-priority (не categories), посилання «Усі пазли Trefl» на картках цієї категорії, посилання з категорії «Пазли» і з hub. Текст лише на 1-й сторінці;
< 5 моделей — тексту нема; статичний текст SEO для slug має пріоритет."""
import contextlib
import io
import json
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
DESC = "Опис товару достатньої довжини для картки сайту, понад сорок символів."


def toysi(pid, name, cat, price=300):
    return {"id": str(pid), "name": name, "vendor": "Trefl" if "Trefl" in cat else "Інший", "price": price, "stock": 5, "category_name": cat, "category_id": "10",
            "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "description": DESC, "params": {}, "url": "x"}


def build(catalog):
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    bs.CATEGORY_GROUPS_FILE = str(out / "none.json")
    bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
    bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        bs.build()
    return out


PIECES = [24, 60, 100, 160, 200, 260, 500, 500, 1000, 1000, 1500, 2000]
cat = {}
n = 1
for i in range(30):   # 30 моделей → дві сторінки категорії
    pc = PIECES[i % len(PIECES)]
    cat[str(n)] = toysi(n, f'Пазли - {pc} елм. - "Модель {i}{" Стіч" if i < 3 else ""}{" Marvel" if i in (5, 6) else ""}" Trefl', "Пазли Trefl", price=150 + i * 10)
    n += 1
for i in range(6):
    cat[str(n)] = toysi(n, f'Пазли - ({40 + i} елм.) - "Інший бренд {i}"', "Пазли", price=200)
    n += 1
for i in range(8):
    cat[str(n)] = toysi(n, f"Лялька Тест {i}", "Ляльки", price=250)
    n += 1
out = build(cat)
page = (out / "category-pazly-trefl.html").read_text(encoding="utf-8")
intro = re.search(r'<div class="cat-intro">(.*?)</div>', page, re.S)
text = re.sub(r"<[^>]+>", " ", intro.group(1)) if intro else ""
words = re.findall(r"[\w'’ʼ+-]+", text)
chk(f"категорія «Пазли Trefl»: є унікальний вступ, {len(words)} слів (120–180)", intro is not None and 120 <= len(words) <= 180)
_idx = json.loads((out / "index-t.json").read_text(encoding="utf-8"))
_pr = [x["pr"] for x in _idx if x["c"] == "Пазли Trefl"]
_fmt = lambda v: f"{v:,}".replace(",", " ")   # noqa: E731 — _fmt_int: тисячі через nbsp
pmin, pmax = min(_pr), max(_pr)
chk(f"текст: виробник, кількість моделей = 30, діапазон деталей від 24 до 2000, ціни від {pmin} до {pmax} ₴ (з побудованого індексу)", "польський виробник пазлів і настільних ігор" in text and "30 моделей" in text
    and "від 24 до 2000" in text and f"від {pmin} до {pmax} ₴" in text.replace(" ", " ").replace("  ", " "))
chk("сюжети лише з назв і лише коли їх ≥ 3: «Стіч» (3 моделі) згадано, «Marvel» (2 моделі) — ні", "популярні сюжети: Стіч" in text and "Marvel" not in text)
chk("вікових позначок у назвах нема → речення про вік НЕМАЄ", "вікові позначки" not in text)
allowed = {"30", "24", "2000", str(pmin), str(pmax)}
nums = set(re.findall(r"\d+", text.replace("\xa0", "")))
chk(f"кожна цифра в тексті — з даних (нічого вигаданого): {sorted(nums)}", nums <= allowed)
chk("title і H1 НЕ змінені: «Пазли Trefl — купити в Україні | PlutusToys», H1 «Пазли Trefl»", "<title>Пазли Trefl — купити в Україні | PlutusToys</title>" in page and re.search(r"<h1[^>]*>Пазли Trefl</h1>", page) is not None)
chk("canonical на себе", 'rel="canonical" href="https://plutustoys.com.ua/category-pazly-trefl.html"' in page)
types = re.findall(r'"@type":\s*"(\w+)"', page)
chk(f"JSON-LD: BreadcrumbList і ItemList лишилися, додано CollectionPage ({sorted(set(types))})", {"BreadcrumbList", "ItemList", "CollectionPage"} <= set(types))
cp = re.search(r'\{[^{}]*"@type":\s*"CollectionPage".*?\}\s*\}', page, re.S)
chk("CollectionPage: name «Пазли Trefl», url сторінки, about Brand Trefl", cp is not None and '"name": "Пазли Trefl"' in cp.group(0) and "category-pazly-trefl.html" in cp.group(0) and '"Brand"' in cp.group(0) and '"Trefl"' in cp.group(0))
page2 = (out / "category-pazly-trefl_2.html").read_text(encoding="utf-8")
chk("2-га сторінка категорії: вступу й CollectionPage НЕМАЄ (текст не дублюємо)", "cat-intro" not in page2 and "CollectionPage" not in page2)
pri = (out / "sitemap-priority.xml").read_text(encoding="utf-8")
cats_sm = (out / "sitemap-categories.xml").read_text(encoding="utf-8") if (out / "sitemap-categories.xml").exists() else ""
chk("sitemap: category-pazly-trefl.html у sitemap-priority, у sitemap-categories НЕ дублюється", "category-pazly-trefl.html" in pri and "category-pazly-trefl.html" not in cats_sm)
prod_t = (out / "product-1.html").read_text(encoding="utf-8")
prod_o = (out / "product-31.html").read_text(encoding="utf-8")
chk("картка Trefl: посилання «Усі пазли Trefl →» на category-pazly-trefl.html; на картці з іншої категорії його нема", 'class="brandlink" href="category-pazly-trefl.html">Усі пазли Trefl →</a>' in prod_t and "brandlink" not in prod_o)
hub = (out / "categories.html").read_text(encoding="utf-8")
chk("hub /categories.html веде на category-pazly-trefl.html", 'href="category-pazly-trefl.html"' in hub)
pz = (out / "category-pazly.html").read_text(encoding="utf-8")
chk("категорія «Пазли» веде на «Пазли Trefl» (блок «Схожі категорії»)", 'href="category-pazly-trefl.html"' in pz)
other = (out / "category-liali.html").read_text(encoding="utf-8") if (out / "category-liali.html").exists() else (out / sorted(p.name for p in out.glob("category-*.html") if "lial" in p.name)[0]).read_text(encoding="utf-8")
chk("інша категорія (Ляльки): вступу/CollectionPage бренду нема", "польський виробник" not in other and "CollectionPage" not in other)

# менше 5 моделей — тексту нема
small = {k: v for k, v in cat.items() if v["category_name"] != "Пазли Trefl"}
for i in range(3):
    small[f"t{i}"] = toysi(900 + i, f'Пазли - {100 + i} елм. - "Мало {i}" Trefl', "Пазли Trefl")
out2 = build(small)
pg_small = (out2 / "category-pazly-trefl.html").read_text(encoding="utf-8")
chk("< 5 моделей: вступу нема (нічого вигадувати), сторінка й title на місці", "cat-intro" not in pg_small and "<title>Пазли Trefl — купити в Україні | PlutusToys</title>" in pg_small)

# вік лише з назв
aged = dict(cat)
aged["a1"] = toysi(950, 'Пазли - 24 елм. 3+ - "Малюкам" Trefl', "Пазли Trefl", price=160)
aged["a2"] = toysi(951, 'Пазли - 2в1 + Мемо - "Не вік" Trefl', "Пазли Trefl", price=170)
out3 = build(aged)
t3 = re.sub(r"<[^>]+>", " ", re.search(r'<div class="cat-intro">(.*?)</div>', (out3 / "category-pazly-trefl.html").read_text(encoding="utf-8"), re.S).group(1))
chk("вікова позначка «3+» з назви → «від 3+ до 3+»; «2в1 + Мемо» віком НЕ вважається", "від 3+ до 3+" in t3 and "від 1+" not in t3)

# статичний текст SEO для slug має пріоритет
_tmp = Path(tempfile.mkdtemp()) / "category_texts.json"
_tmp.write_text(json.dumps({"pazly-trefl": "Статичний текст від SEO."}, ensure_ascii=False), encoding="utf-8")
_orig_file = bs._CATEGORY_TEXTS_FILE
bs._CATEGORY_TEXTS_FILE = str(_tmp)
out4 = build(cat)
bs._CATEGORY_TEXTS_FILE = _orig_file
pg4 = (out4 / "category-pazly-trefl.html").read_text(encoding="utf-8")
chk("статичний текст із category_texts.json має пріоритет над динамічним", "Статичний текст від SEO." in pg4 and "польський виробник" not in pg4)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n_ in F:
        print("  -", n_)
    sys.exit(1)
print("ALL OK")
