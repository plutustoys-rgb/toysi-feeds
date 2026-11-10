#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_toysi_photo_proxy.py — 10.11.2026 (власник: «роби проксі»): фото Toysi на сайті йдуть через наш /img/t/ за воротами SITE_TOYSI_PHOTO_PROXY=1.
Інваріанти:
  • ворота вимкнено (за замовчуванням): збірка як була — фото https://toysi.ua/p/…, preconnect/dns-prefetch на toysi.ua на місці;
  • ворота ввімкнено: https://toysi.ua/p/<шлях> -> https://plutustoys.com.ua/img/t/<шлях> лише для [NN/][enl_]<цифри>.jpg|png|webp; PHOTO_OVERRIDES теж;
  • інші хости (images.prom.ua, наш власний) і невідповідні шляхи — без змін; не рядок — без винятку;
  • повна збірка з увімкненими воротами: жодного «toysi.ua» у HTML (видимий, og:image, JSON-LD, preload, preconnect) і в index.json;
  • ворота вимкнено + увімкнено: решта виходу збірки (кількість файлів, назви, ціни) та сама.
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
for k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID", "SITE_BUILD_LIMIT", "SITE_TOYSI_PHOTO_PROXY"):
    os.environ.pop(k, None)
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


T = bs.toysi_proxy_photo
chk("ворота за замовчуванням вимкнено", bs.TOYSI_PHOTO_PROXY is False)
urls = ["https://toysi.ua/p/enl_265377.jpg", "https://toysi.ua/p/3/enl_265377.jpg", "https://toysi.ua/p/12/265377.png", "https://toysi.ua/p/2/enl_255663.webp"]
chk("вимкнено: усі URL без змін", all(T(u) == u for u in urls))

bs.TOYSI_PHOTO_PROXY = True
try:
    chk("увімкнено: /p/enl_N.jpg -> /img/t/enl_N.jpg", T(urls[0]) == "https://plutustoys.com.ua/img/t/enl_265377.jpg")
    chk("увімкнено: /p/3/enl_N.jpg -> /img/t/3/enl_N.jpg", T(urls[1]) == "https://plutustoys.com.ua/img/t/3/enl_265377.jpg")
    chk("увімкнено: png/webp, без enl_, дворозрядна тека", T(urls[2]) == "https://plutustoys.com.ua/img/t/12/265377.png" and T(urls[3]) == "https://plutustoys.com.ua/img/t/2/enl_255663.webp")
    others = ["https://images.prom.ua/123456789_w640_h640_foto.jpg", "https://plutustoys.com.ua/img/p/x101-1.jpg", "https://plutustoys.com.ua/img/dropt/dropt-101-1.jpg",
              "https://toysi.ua/p/../etc/passwd.jpg", "https://toysi.ua/p/3/enl_1.jpg?x=1", "https://toysi.ua/p/a/enl_1.jpg", "https://toysi.ua/p/123/enl_1.jpg",
              "https://toysi.ua.evil.com/p/enl_1.jpg", "http://toysi.ua/p/enl_1.jpg", "https://toysi.ua/p/enl_1.gif", "https://toysi.ua/p/enl_1.jpg/extra", "", "https://toysi.ua/p/enl_.jpg"]
    chk("інші хости й невідповідні шляхи — без змін", all(T(u) == u for u in others))
    chk("не рядок — без винятку, без змін", T(None) is None and T(5) == 5)
    chk("PHOTO_OVERRIDES (мертві головні фото) теж проксуються", all(T(u).startswith("https://plutustoys.com.ua/img/t/") for u in bs.PHOTO_OVERRIDES.values()))
finally:
    bs.TOYSI_PHOTO_PROXY = False


# ── повна збірка на синтетичному каталозі, двічі: ворота вимкнено / ввімкнено
def item(pid, name, category, pics, price=300.0):
    return {"id": str(pid), "name": name, "vendor": "Test", "price": price, "stock": 5, "category_name": category, "category_id": "10",
            "pictures": pics, "description": "Опис товару достатньої довжини для картки, щоб він не вважався тонким і мав нормальний вигляд.", "barcode": ""}


catalog = {}
for i in range(1, 41):
    catalog[str(i)] = item(i, f"Іграшка номер {i}", "Машинки" if i % 2 else "Ляльки", [f"https://toysi.ua/p/enl_{i}.jpg", f"https://toysi.ua/p/2/enl_{i}.jpg"], price=250.0 + i)
catalog["265377"] = item(265377, "Лялька з мертвим фото", "Ляльки", ["https://toysi.ua/p/enl_265377.jpg"])
catalog["701"] = item(701, "Машинка зі сторонніх фото", "Машинки", ["https://images.prom.ua/777_w640_h640_foto.jpg"])
bs.tp.fetch_toysi_catalog = lambda: catalog
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
import telegram_notify  # noqa: E402
telegram_notify.send_throttled_alert = lambda *a, **k: True
bs.LIMIT = 0
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None


def build(gate):
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    bs.TOYSI_PHOTO_PROXY = gate
    try:
        bs.build()
    finally:
        bs.TOYSI_PHOTO_PROXY = False
    return out


def texts(out):
    return {p.name: p.read_text(encoding="utf-8", errors="replace") for p in out.iterdir() if p.suffix in (".html", ".json", ".xml", ".txt") and not p.name.startswith(".")}   # службовий .htlastmod_state.json — хеші сторінок, природно різні


off = texts(build(False))
on = texts(build(True))
off_all = "\n".join(off.values())
on_all = "\n".join(on.values())

chk("вимкнено: фото toysi.ua у сторінці картки, у index.json, preconnect на місці",
    "https://toysi.ua/p/enl_5.jpg" in off["product-5.html"] and "https://toysi.ua/p/enl_5.jpg" in off["index.json"] and 'rel="preconnect" href="https://toysi.ua"' in off["product-5.html"])
chk("ввімкнено: жодного «toysi.ua» у жодному HTML/JSON/XML збірки (видимий текст, og:image, twitter:image, JSON-LD, preload, preconnect, index.json)",
    "toysi.ua" not in on_all)
p5 = on["product-5.html"]
chk("ввімкнено: <img>, og:image, JSON-LD image, preload — на https://plutustoys.com.ua/img/t/enl_5.jpg",
    f'<img src="https://plutustoys.com.ua/img/t/enl_5.jpg"' in p5 and 'og:image" content="https://plutustoys.com.ua/img/t/enl_5.jpg"' in p5
    and '"image": ["https://plutustoys.com.ua/img/t/enl_5.jpg"]' in p5.replace('"image":["', '"image": ["') and 'rel="preload" as="image" href="https://plutustoys.com.ua/img/t/enl_5.jpg"' in p5)
ix_on = {x["id"]: x for x in json.loads(on["index.json"])}
chk("ввімкнено: index.json p — /img/t/…; картка з PHOTO_OVERRIDES → /img/t/3/enl_265377.jpg; фото images.prom.ua без змін",
    ix_on["5"]["p"] == "https://plutustoys.com.ua/img/t/enl_5.jpg" and ix_on["265377"]["p"] == "https://plutustoys.com.ua/img/t/3/enl_265377.jpg" and ix_on["701"]["p"] == "https://images.prom.ua/777_w640_h640_foto.jpg")
chk("ввімкнено: preconnect/dns-prefetch на toysi.ua прибрано, решта <head> на місці", 'rel="preconnect" href="https://toysi.ua"' not in p5 and 'rel="stylesheet"' in p5)
chk("ворота не змінюють набір файлів і назви/ціни в index.json", sorted(off) == sorted(on)
    and [(x["id"], x["n"], x["pr"]) for x in json.loads(off["index.json"])] == [(x["id"], x["n"], x["pr"]) for x in json.loads(on["index.json"])])


def strip_photo(t):
    t = re.sub(r"https://(?:toysi\.ua/p|plutustoys\.com\.ua/img/t)/[0-9A-Za-z_/.]+", "PHOTO", t)
    return re.sub(r'<link rel="(?:preconnect|dns-prefetch)" href="https://toysi\.ua"[^>]*>\n?', "", t)


diff_files = [n for n in off if strip_photo(off[n]) != strip_photo(on[n])]
chk("ворота змінюють ЛИШЕ адреси фото і два preconnect-рядки (решта байтів ідентична): " + (", ".join(diff_files[:5]) or "—"), not diff_files)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
