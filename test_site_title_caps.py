#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_title_caps.py — 10.11.2026 (SEO, GMC title_all_caps): назва картки на сайті проходить feed_title_norm.normalize_title (той самий модуль, що й фіди).
Інваріанти:
  • normalize_site_title: назви-капсом виправляються; звичайні назви, латиниця, цифри — без змін;
  • запобіжник: результат приймається лише якщо відрізняється від оригіналу ТІЛЬКИ регістром (інакше — назва постачальника); виняток модуля не валить збірку;
  • обрізання до 150 символів (для фіду) для сайту не діє: довга назва нормалізується ПОВНІСТЮ;
  • детермінованість (не залежить від порядку/каталогу) та ідемпотентність: normalize(normalize(x)) == normalize(x);
  • повна збірка: сторінка, <title>, <h1>, JSON-LD name, index.json "n" — з виправленим регістром; не-капсова картка не змінилась.
Самодостатній, без мережі."""
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
for k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID", "SITE_BUILD_LIMIT"):
    os.environ.pop(k, None)
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def share(t):
    letters = [c for c in t if c.isalpha()]
    return sum(c.isupper() for c in letters) / len(letters) if letters else 0


N = bs.normalize_site_title
caps = ['МАШИНА МЕТАЛЕВА "АВТОПРОМ" 1:36 CHEVROLET CAMARO (ПОМАРАНЧЕВИЙ)', 'ПЕНАЛ "КОСМОНАВТ" (ТЕМНО-СИНІЙ)', "ЛЯЛЬКА БАРБІ У ПЛАТТІ", 'ПАЗЛ "МІСТО" 500 ЕЛЕМЕНТІВ']
chk("капс-назви: частка великих падає нижче 50 %, перша літера велика", all(share(N(t)) < 0.5 and N(t)[0].isupper() for t in caps))
chk('конкретно: капс-назва машинки і пенал нормалізуються до очікуваного вигляду', N(caps[0]) == 'Машина металева "Автопром" 1:36 CHEVROLET CAMARO (помаранчевий)' and N(caps[1]) == 'Пенал "Космонавт" (темно-синій)')
plain = ["Іграшка номер 5", "LEGO City Поліція", "Машинка Hot Wheels 1:64", "Набір для творчості, 24 предмети", ""]
chk("звичайні назви, латиниця, цифри — без змін", all(N(t) == t for t in plain))
chk("не рядок / None — без змін, без винятку", N(None) is None and N(5) == 5)

# ── запобіжник
real = bs._feed_normalize_title
try:
    bs._feed_normalize_title = lambda t, limit=150: "Зовсім інша назва"
    chk("запобіжник: результат, що змінює не лише регістр, відхиляється (назва постачальника)", N("ПЕНАЛ КОСМОНАВТ") == "ПЕНАЛ КОСМОНАВТ")
    bs._feed_normalize_title = lambda t, limit=150: t[:5]
    chk("запобіжник: обрізаний результат відхиляється", N("ПЕНАЛ КОСМОНАВТ ЧОРНИЙ") == "ПЕНАЛ КОСМОНАВТ ЧОРНИЙ")

    def boom(t, limit=150):
        raise RuntimeError("збій модуля")
    bs._feed_normalize_title = boom
    chk("виняток у модулі не валить збірку: назва як є", N("ПЕНАЛ КОСМОНАВТ") == "ПЕНАЛ КОСМОНАВТ")
    bs._feed_normalize_title = None
    chk("модуля нема (ImportError) — назва як є", N("ПЕНАЛ КОСМОНАВТ") == "ПЕНАЛ КОСМОНАВТ")
finally:
    bs._feed_normalize_title = real

# ── довга назва нормалізується повністю (без обрізання до 150)
long_t = "НАБІР ДЛЯ ТВОРЧОСТІ " + " ".join(["ДОДАТКОВИЙ ЕЛЕМЕНТ КОМПЛЕКТУ"] * 8)
chk("довга назва (>150): не обрізана, регістр виправлено", len(long_t) > 150 and len(N(long_t)) == len(long_t) and share(N(long_t)) < 0.5)

# ── детермінованість та ідемпотентність
sample = caps + plain + [long_t]
chk("ідемпотентність: N(N(x)) == N(x)", all(N(N(t)) == N(t) for t in sample))
chk("детермінованість: порядок виклику не впливає", [N(t) for t in sample] == [N(t) for t in reversed(sample[::-1])] and [N(t) for t in sample[::-1]][::-1] == [N(t) for t in sample])


# ── повна збірка на синтетичному каталозі
def item(pid, name, category, stock=5, price=300.0):
    return {"id": str(pid), "name": name, "vendor": "Test", "price": price, "stock": stock, "category_name": category, "category_id": "10",
            "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "description": "Опис товару достатньої довжини для картки, щоб він не вважався тонким і мав нормальний вигляд.", "barcode": ""}


catalog = {}
for i in range(1, 41):
    catalog[str(i)] = item(i, f"Іграшка номер {i}", "Машинки" if i % 2 else "Ляльки", price=250.0 + i)
catalog["601"] = item(601, 'ПЕНАЛ "КОСМОНАВТ" (ТЕМНО-СИНІЙ)', "Машинки")
catalog["603"] = item(603, "Лялька Маша рожева", "Ляльки")
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.tp.fetch_toysi_catalog = lambda: catalog
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
import telegram_notify  # noqa: E402
telegram_notify.send_throttled_alert = lambda *a, **k: True
bs.LIMIT = 0
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None
bs.build()

h601 = (out / "product-601.html").read_text(encoding="utf-8")
title601 = re.search(r"<title>(.*?)</title>", h601, re.S).group(1)
h1_601 = re.search(r"<h1[^>]*>(.*?)</h1>", h601, re.S).group(1)
ld601 = h601.split("application/ld+json", 1)[-1]
chk("601: <title>, <h1>, JSON-LD — з виправленим регістром", "Космонавт" in title601 and "Космонавт" in h1_601 and "Космонавт" in ld601)
chk("601: капсової версії на сторінці нема", "КОСМОНАВТ" not in h601 and "ТЕМНО-СИНІЙ" not in h601)
ix = {x["id"]: x for x in json.loads((out / "index.json").read_text(encoding="utf-8"))}
chk("index.json: n виправлена для 601; 603 (не капс) без змін", ix["601"]["n"] == 'Пенал "Космонавт" (темно-синій)' and ix["603"]["n"] == "Лялька Маша рожева")

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
