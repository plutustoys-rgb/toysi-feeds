#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_category_overrides.py — 23.10.2026 (SEO: у категоріях є чужі товари, категорія береться з поля Toysi, тож перенесення робимо даними: site/category_overrides.json).
Інваріанти:
(1) запис {id: назва існуючої категорії} переносить картку: index.json (c), сторінка нової категорії містить товар, стара — ні, крихти/категорія на сторінці товару нові;
(2) відкидаються ГОЛОСНО (товар лишається у своїй категорії): id не з цифр, цілі нема серед категорій сайту (нових категорій не створюємо), ціль прихована (NOINDEX_CATEGORIES), значення не рядок; службові ключі «_…» ігноруються;
(3) rt-картки не чіпаємо; перенесення в ту саму категорію — не рахується застосованим;
(4) без файла/порожній файл — поведінка як раніше (жодних змін)."""
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


def item(pid, name, cat):
    return {"id": str(pid), "name": name, "vendor": "T", "price": 200 + pid % 200, "stock": 5, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/p/{pid}"}


catalog = {}
for i in range(1, 31):
    catalog[str(i)] = item(i, f"Модель машини {i}", "Металеві моделі")
for i in range(31, 51):
    catalog[str(i)] = item(i, f"Розмальовка {i}", "Розмальовки")
catalog["60"] = item(60, "Чашка «Україна»", "Металеві моделі")      # чужий товар
catalog["80"] = item(80, "Лише без залишку", "Тільки OOS")
catalog["80"]["stock"] = 0                                          # категорія лише з товаром без залишку: нової сторінки перенесення не створює
for i in (81, 82):
    catalog[str(i)] = item(i, f"Арбалет {i}", "Арбалети і луки")   # NOINDEX_CATEGORIES[...] = True: картки noindex і поза sitemap
catalog["61"] = item(61, "Шахи магнітні", "Металеві моделі")        # чужий товар
for i in range(70, 76):
    catalog[str(i)] = item(i, f"Азартна гра {i}", "Азартні")          # прихована категорія (NOINDEX_CATEGORIES)
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
tmp = Path(tempfile.mkdtemp())
bs.CATEGORY_GROUPS_FILE = str(tmp / "none.json")
bs.SUPPLIER_RT_FILE = str(tmp / "no_rt.json")


def build_with(overrides, label):
    f = tmp / f"ov_{label}.json"
    if overrides is not None:
        f.write_text(json.dumps(overrides, ensure_ascii=False), encoding="utf-8")
    bs._CATEGORY_OVERRIDES_FILE = str(f)
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        bs.build()
    return out, err.getvalue()


def sm_all(out):
    return "".join(p.read_text(encoding="utf-8") for p in out.glob("sitemap-*.xml"))


def cat_file(out, slug_part):
    c = [p for p in out.glob("category-*.html") if slug_part in p.name]
    return c[0].read_text(encoding="utf-8") if c else ""


base, _ = build_with(None, "none")
idx0 = {x["id"]: x for x in json.loads((base / "index.json").read_text(encoding="utf-8"))}
chk("без файла перекриттів: чужі товари лишаються в «Металеві моделі» (c)", idx0["60"]["c"] == "Металеві моделі" and idx0["61"]["c"] == "Металеві моделі")

ov = {"_призначення": "службове", "9": "Тільки OOS", "81": "Розмальовки", "60": "Розмальовки", "61": "Розмальовки", "abc": "Розмальовки", "62": "Розмальовки", "5": "Нема такої категорії", "6": "Азартні", "7": 123, "8": "Металеві моделі", "rt-9": "Розмальовки"}
out, err = build_with(ov, "main")
idx = {x["id"]: x for x in json.loads((out / "index.json").read_text(encoding="utf-8"))}
chk("перенесені товари 60 і 61 тепер у категорії «Розмальовки» (index.json c)", idx["60"]["c"] == "Розмальовки" and idx["61"]["c"] == "Розмальовки")
roz = cat_file(out, "rozmalovky")
met = cat_file(out, "metalevi-modeli")
chk("сторінка «Розмальовки» містить перенесені товари, «Металеві моделі» їх уже не містить", "product-60.html" in roz and "product-61.html" in roz and "product-60.html" not in met and "product-61.html" not in met)
chk("«Металеві моделі» лишає свої 30 товарів (рахунок за index.json; сторінка категорії пагінується)", sum(1 for x in idx.values() if x["c"] == "Металеві моделі") == 30 and sum(1 for x in idx.values() if x["c"] == "Розмальовки") == 23)
p60 = (out / "product-60.html").read_text(encoding="utf-8")
chk("сторінка товару 60: категорія «Розмальовки» (в блоці категорії й у крихтах), «Металеві моделі» не згадується", 'class="cat">Розмальовки<' in p60 and "Металеві моделі" not in p60.split("<footer")[0])
chk("прихована ціль («Азартні») відкинута: товар 6 лишився у «Металеві моделі»", idx["6"]["c"] == "Металеві моделі")
chk("цілі нема серед категорій сайту («Нема такої категорії»): товар 5 лишився", idx["5"]["c"] == "Металеві моделі")
chk("id не з цифр («abc», «rt-9»), значення не рядок (7), id без товару (62), службовий ключ: нічого не ламають", "62" not in idx and idx["7"]["c"] == "Металеві моделі")
chk("перенесення в ту саму категорію (8 → «Металеві моделі») не змінює нічого", idx["8"]["c"] == "Металеві моделі")
chk("ціль лише з товарами без залишку («Тільки OOS») відкинута: нової сторінки категорії не з'явилось, товар 9 лишився", idx["9"]["c"] == "Металеві моделі" and not list(out.glob("category-*oos*.html")) and not list(out.glob("category-tilky*.html")))
chk("перенесення З noindex-категорії у видиму (81: «Арбалети і луки» → «Розмальовки»): картка видима й індексована (без noindex, є в sitemap)",
    idx.get("81", {}).get("c") == "Розмальовки" and 'content="noindex' not in (out / "product-81.html").read_text(encoding="utf-8").split("</head>")[0] and sm_all(out).count("/product-81.html") == 1)
chk("товар 82 лишився в «Арбалети і луки»: noindex і поза sitemap, як і раніше", 'content="noindex' in (out / "product-82.html").read_text(encoding="utf-8").split("</head>")[0] and sm_all(out).count("/product-82.html") == 0)
chk("попередження в stderr: відкинуті записи названо, збірка не впала", "category_overrides.json" in err and "відкинуто" in err)
sm = "".join(p.read_text(encoding="utf-8") for p in out.glob("sitemap-*.xml"))
chk("sitemap і індекс узгоджені: товар 60 є в sitemap рівно раз", sm.count("/product-60.html") == 1)

# рішення власника 28.10: нова категорія ЛИШЕ з переліку NEW_CATEGORIES_ALLOWED (ігри 18+ → «Ігри 16+ та 18+»); інші нові назви відкидаються як раніше
_old_allowed = bs.NEW_CATEGORIES_ALLOWED
bs.NEW_CATEGORIES_ALLOWED = ("Нова дозволена",)
try:
    out4, err4 = build_with({"61": "Нова дозволена", "5": "Інша нова"}, "newcat")
finally:
    bs.NEW_CATEGORIES_ALLOWED = _old_allowed
idx4 = {x["id"]: x for x in json.loads((out4 / "index.json").read_text(encoding="utf-8"))}
chk("дозволена нова категорія створюється: товар 61 в «Нова дозволена», сторінка категорії є і містить товар", idx4["61"]["c"] == "Нова дозволена" and any("product-61.html" in p.read_text(encoding="utf-8") for p in out4.glob("category-nova-*.html")))
chk("недозволена нова назва («Інша нова») відкинута: товар 5 лишився у «Металеві моделі»", idx4["5"]["c"] == "Металеві моделі" and not list(out4.glob("category-insha-nova*.html")))
chk("«Ігри 18+» у реальному переліку дозволених; файл репо містить 15 ігор 16+/18+ з цією ціллю", "Ігри 16+ та 18+" in bs.NEW_CATEGORIES_ALLOWED and sum(1 for v in json.loads((HERE / "site" / "category_overrides.json").read_text(encoding="utf-8-sig")).values() if v == "Ігри 16+ та 18+") == 15)

# порожній файл і файл лише зі службовим ключем — як без файла
out2, err2 = build_with({"_призначення": "лише службове"}, "empty")
idx2 = {x["id"]: x for x in json.loads((out2 / "index.json").read_text(encoding="utf-8"))}
chk("лише службовий ключ: жодних змін і без попереджень про перекриття", idx2["60"]["c"] == "Металеві моделі" and "category_overrides" not in err2)
(tmp / "ov_bad.json").write_text("{не json", encoding="utf-8")
bs._CATEGORY_OVERRIDES_FILE = str(tmp / "ov_bad.json")
out3 = Path(tempfile.mkdtemp())
bs.OUT = str(out3)
err3 = io.StringIO()
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err3):
    bs.build()
chk("зіпсований JSON: збірка не падає, товари на місці, у stderr є причина", (out3 / "product-60.html").exists() and "category_overrides.json" in err3.getvalue())

# файл у репо
repo = json.loads((HERE / "site" / "category_overrides.json").read_text(encoding="utf-8-sig"))
chk("site/category_overrides.json у репо: об'єкт, усі ключі або службові, або цифрові id, значення — рядки", isinstance(repo, dict) and all(k.startswith("_") or (re.fullmatch(r"[0-9]{1,12}", k) and isinstance(v, str) and v.strip()) for k, v in repo.items()))

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
