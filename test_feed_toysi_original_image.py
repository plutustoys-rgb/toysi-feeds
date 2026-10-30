#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_feed_toysi_original_image.py — Google/Meta/Bing: фото Toysi `enl_N.jpg` (500 px) → оригінал `N.jpg` (SEO 30.10, GMC image_too_small_for_high_resolution).
ІНВАРІАНТИ: заміна лише рівно https://toysi.ua/p/enl_<цифри>.jpg; вкладені шляхи (/p/3/enl_…), Prom-фото й чужі хости не чіпаємо;
FEED_TOYSI_ORIGINAL_IMAGE=0 повертає старе; у зібраному елементі фіду image_link — оригінал. `python test_feed_toysi_original_image.py` → exit 0/1.
"""
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "site"))

import generate_google_feed as gg  # noqa: E402

F = []


def chk(label, ok):
    print(("[OK ] " if ok else "[FAIL] ") + label)
    if not ok:
        F.append(label)


os.environ.pop("FEED_TOYSI_ORIGINAL_IMAGE", None)
f = gg._toysi_original_image
chk("типово УВІМКНЕНО", gg.toysi_original_image_on() is True)
chk("enl_297821 → оригінал", f("https://toysi.ua/p/enl_297821.jpg") == "https://toysi.ua/p/297821.jpg")
chk("вкладений шлях /p/3/enl_ не чіпаємо (фото-override сайту)", f("https://toysi.ua/p/3/enl_265377.jpg") == "https://toysi.ua/p/3/enl_265377.jpg")
chk("Prom-фото не чіпаємо", f("https://images.prom.ua/6883260421_w1024_h1024_x.jpg") == "https://images.prom.ua/6883260421_w1024_h1024_x.jpg")
chk("чужий хост з enl_ не чіпаємо", f("https://example.com/p/enl_5.jpg") == "https://example.com/p/enl_5.jpg")
chk("не-jpg / нецифровий id / параметри не чіпаємо", all(f(u) == u for u in ("https://toysi.ua/p/enl_5.png", "https://toysi.ua/p/enl_abc.jpg", "https://toysi.ua/p/enl_5.jpg?x=1")))
chk("порожнє/None → як було", f("") == "" and f(None) is None)
os.environ["FEED_TOYSI_ORIGINAL_IMAGE"] = "0"
chk("FEED_TOYSI_ORIGINAL_IMAGE=0 → старе посилання", f("https://toysi.ua/p/enl_297821.jpg") == "https://toysi.ua/p/enl_297821.jpg")
os.environ.pop("FEED_TOYSI_ORIGINAL_IMAGE", None)

# у зібраному елементі фіду (Google/Meta/Bing беруть it["image_link"] звідси)
def item(pid, pics):
    return {"id": pid, "name": "Звичайний конструктор", "vendor": "Test", "price": 200.0, "stock": 5, "category_name": "Різне", "category_id": "1",
            "pictures": pics, "description": "Опис товару достатньої довжини для фіда.", "barcode": ""}


def links(cat):
    items, _st = gg.build_feed_items(cat, {}, {}, {}, None, None)
    return {i["pid"]: i["image_link"] for i in items}


cat = {"1": item("1", ["https://toysi.ua/p/enl_1.jpg"]), "2": item("2", ["https://toysi.ua/p/3/enl_2.jpg"])}
got = links(cat)
chk("елемент фіду: enl_1 → /p/1.jpg; вкладений /p/3/enl_2 лишився", got.get("1") == "https://toysi.ua/p/1.jpg" and got.get("2") == "https://toysi.ua/p/3/enl_2.jpg")
os.environ["FEED_TOYSI_ORIGINAL_IMAGE"] = "0"
chk("елемент фіду: FEED_TOYSI_ORIGINAL_IMAGE=0 → enl_ як було", links(cat).get("1") == "https://toysi.ua/p/enl_1.jpg")
os.environ.pop("FEED_TOYSI_ORIGINAL_IMAGE", None)

print()
print("ALL OK" if not F else f"FAIL: {len(F)}")
sys.exit(1 if F else 0)
