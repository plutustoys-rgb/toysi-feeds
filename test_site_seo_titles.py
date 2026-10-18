#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_seo_titles.py — 18.10.2026 (SEO-аудит): (1) title ≤ 65 знаків ПІСЛЯ декодування HTML (у сирому HTML «"» = &quot; і «рахується» 6 знаками — звідси хибні «довгі» title в аудиті);
(2) title унікальні: коли обрізання дало однакові (особливо «— немає в наявності»), додається «№id»; (3) JSON-LD Product: brand зі specs, інакше з vendor Toysi (сміття — ні), gtin лише з валідного штрихкоду (GS1)."""
import contextlib
import html
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["SITE_PICK_DATE"] = "2026-10-18"
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


def item(pid, name, stock=5, vendor="", barcode="", desc="Опис товару достатньої довжини для картки сайту, понад сорок символів."):
    return {"id": str(pid), "name": name, "vendor": vendor, "price": 300 + pid % 50, "stock": stock, "category_name": "Велосипеди", "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": desc, "params": [], "url": f"https://toysi.ua/{pid}", "barcode": barcode}


LONG = 'Велосипед дитячий 16 дюймів двоколісний з допоміжними колесами та кошиком "Спорт"'
catalog = {}
# 3 OOS і 2 в наявності з однаковим початком (після обрізання title збігаються)
for i, st in ((101, 0), (102, 0), (103, 0), (104, 5), (105, 5)):
    catalog[str(i)] = item(i, f"{LONG} модель {i}", stock=st)
catalog["110"] = item(110, "Лялька «Софі»", vendor="Hasbro")                       # brand зі vendor
catalog["111"] = item(111, "М'яч футбольний", vendor="Китай")                      # сміття — brand не ставимо
catalog["112"] = item(112, "Конструктор «Місто»", vendor="LEGO", desc="Опис конструктора достатньої довжини понад сорок символів.<br><b>Бренд:</b> Cobi")   # brand зі specs має пріоритет
catalog["113"] = item(113, "Пазл 100 елементів", vendor="Trefl", barcode="4006381333931")  # валідний EAN-13
catalog["114"] = item(114, "Пазл 200 елементів", vendor="Trefl", barcode="4006381333932")  # невалідна контрольна цифра
catalog["115"] = item(115, "Пазл 300 елементів", vendor="Trefl", barcode="123")
for i in range(120, 140):
    catalog[str(i)] = item(i, f"Звичайна іграшка номер {i}")
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.CATEGORY_GROUPS_FILE = str(Path(tempfile.mkdtemp()) / "none.json")
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()


def page(i):
    return (out / f"product-{i}.html").read_text(encoding="utf-8")


titles = {}
for f in out.glob("product-*.html"):
    t = re.search(r"<title>(.*?)</title>", f.read_text(encoding="utf-8"), re.S).group(1).strip()
    titles[f.name] = html.unescape(t)
chk(f"усі title ≤ 65 знаків після декодування HTML ({len(titles)} карток, max {max(len(t) for t in titles.values())})", all(len(t) <= 65 for t in titles.values()))
dups = [t for t in set(titles.values()) if list(titles.values()).count(t) > 1]
chk(f"title унікальні (дублів: {len(dups)}), у т.ч. 3 OOS і 2 в наявності з однаковим початком", not dups and len([f for f in titles if f in {f"product-{i}.html" for i in range(101, 106)}]) == 5)
t101 = titles["product-101.html"]
chk(f"OOS-картка: «№id» перед суфіксом, суфікс «— немає в наявності» на місці: {t101}", "№101" in t101 and t101.endswith("— немає в наявності | PlutusToys"))
t110 = titles["product-110.html"]
chk(f"унікальна коротка назва без змін (без «№»): {t110}", t110 == "Лялька «Софі» — купити | PlutusToys")


def ld(i):
    m = re.search(r'<script type="application/ld\+json">(\{.*?"@type": "Product".*?)</script>', page(i).replace("\u003c", "<"), re.S)
    return json.loads(m.group(1))


chk("brand зі vendor Toysi, коли у специфікаціях нема (Hasbro)", ld(110).get("brand", {}).get("name") == "Hasbro")
chk("сміттєвий vendor («Китай») — brand НЕ ставимо", "brand" not in ld(111))
chk("brand зі специфікацій має пріоритет над vendor (Cobi, не LEGO)", ld(112).get("brand", {}).get("name") == "Cobi")
chk("валідний EAN-13 → gtin13 у Product", ld(113).get("gtin13") == "4006381333931")
chk("невалідна контрольна цифра і короткий код → gtin не ставимо", not any(k.startswith("gtin") for k in ld(114)) and not any(k.startswith("gtin") for k in ld(115)))
chk("_valid_gtin: 8/12/13/14 цифр, нулі й сміття відсікаються", bs._valid_gtin("4006381333931") == ("gtin13", "4006381333931") and bs._valid_gtin("0000000000000") is None and bs._valid_gtin("abc") is None and bs._valid_gtin("") is None)
# ступінчасті суфікси (SEO 18.10): повний суфікс → без бренду → без суфікса; назву не ріжемо, поки вона вміщується
short, mid, longer, huge = "Лялька «Софі»", "А" * 50, "Б" * 60, "В" * 70
chk("коротка назва: повний суфікс «— купити | PlutusToys»", bs.product_title(short).endswith("— купити | PlutusToys"))
chk(f"середня назва (50 зн.): без бренду, лишається «— купити»: {bs.product_title(mid)[-20:]}", bs.product_title(mid) == mid + " — купити")
chk("довга назва (60 зн.): без суфікса взагалі, назва ціла", bs.product_title(longer) == longer)
chk("дуже довга (70 зн.): обрізання по межі слова з «…» і повним суфіксом, ≤65", bs.product_title(huge).endswith("— купити | PlutusToys") and len(bs.product_title(huge)) <= 65)
chk("OOS: ступені так само («— немає в наявності» лишається без бренду)", bs.product_title("А" * 40, False) == "А" * 40 + " — немає в наявності" and bs.product_title(short, False).endswith("— немає в наявності | PlutusToys"))
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
