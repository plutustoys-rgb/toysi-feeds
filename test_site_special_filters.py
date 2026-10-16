#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_special_filters.py — 16.10.2026 (власник про /utsinka.html: «не вистачає сортерів, збільши текст»): службові сторінки (Уцінка, Акції) мають ту саму закріплену панель, що й каталог:
крупна назва (cat-h1) + сортування/категорія/ціна, але працюють ЛИШЕ з товарами цієї сторінки (<script id=cat-ids>), а не з усім каталогом. Chromium."""
import contextlib
import functools
import http.server
import io
import os
import re
import shutil
import sys
import tempfile
import threading
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["SITE_PICK_DATE"] = "2026-10-21"
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


def item(pid, name, cat, price):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": 5, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/{pid}"}


catalog, pid = {}, 1
for i in range(40):
    catalog[str(pid)] = item(pid, f"Звичайна іграшка {pid}", f"Кат{i % 5}", 100 + i * 10)
    pid += 1
for i in range(30):
    catalog[str(pid)] = item(pid, f"Уцінка. Іграшка з дефектом {pid}", "Уцінка", 50 + i * 15)
    pid += 1
UT_IDS = {str(i) for i in range(41, 71)}
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.CATEGORY_GROUPS_FILE = str(Path(tempfile.mkdtemp()) / "none.json")
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()
u1 = (out / "utsinka.html").read_text(encoding="utf-8")
ids = set(re.search(r'<script type="application/json" id="cat-ids">(.*?)</script>', u1).group(1).strip("[]").replace('"', "").replace(" ", "").split(","))
chk(f"Уцінка: cat-bar із крупною назвою cat-h1, панель #cat-controls, #cat-ids = id уцінених ({len(ids)})", 'class="cat-bar"' in u1 and '<h1 class="page cat-h1">Уцінені іграшки</h1>' in u1 and 'id="cat-controls"' in u1 and ids and ids <= UT_IDS and not (ids & {str(i) for i in range(1, 41)}))
u2 = (out / "utsinka_2.html")
chk("Уцінка: стор. 2 лишається статичною (без панелі) — пагінація для краулера", (not u2.exists()) or 'id="cat-controls"' not in u2.read_text(encoding="utf-8"))

shutil.copytree(HERE / "site" / "assets", out / "assets", dirs_exist_ok=True)
H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None


class _Srv(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


srv = _Srv(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
from playwright.sync_api import sync_playwright  # noqa: E402

CARDS = "[...document.querySelectorAll('#cat-js .card')].map(c=>[c.getAttribute('href').replace(/\D/g,''), parseFloat(c.querySelector('.pr').textContent)])"
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (1280, 390):
        pg = br.new_page(viewport={"width": w, "height": 800})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("https://toysi.ua/**", lambda r: r.abort())
        pg.goto(f"{base}/utsinka.html", wait_until="load")
        pg.wait_for_selector("#cat-js .card")
        pg.wait_for_timeout(300)
        cnt = pg.evaluate("document.getElementById('cat-count').textContent")
        cards = pg.evaluate(CARDS)
        chk(f"{w}px: у JS-сітці лише уцінені ({cnt}), жодного звичайного товару", f"Знайдено: {len(ids)}" in cnt and cards and all(c[0] in ids for c in cards))
        catvis = pg.evaluate("(()=>{const e=document.getElementById('cf-cat'); const r=e.getBoundingClientRect(); return [e.hidden, getComputedStyle(e).display, r.width]})()")
        chk(f"{w}px: списку категорій на службовій сторінці нема (власник 16.10: «підкатегорії по 1 товару — для чого?»), лишились сортування й ціна {catvis}", catvis[0] is True and catvis[1] == "none" and catvis[2] == 0 and pg.evaluate("!!document.getElementById('cf-sort') && !!document.getElementById('cf-min')"))
        if w >= 1000:
            pg.select_option("#cf-sort", "dear")
            pg.wait_for_timeout(300)
            cards = pg.evaluate(CARDS)
            prices = [c[1] for c in cards]
            chk(f"{w}px: «Спершу дорожчі» сортує лише уцінені (перша {prices[0]:.0f} ₴, далі за спаданням)", prices == sorted(prices, reverse=True) and all(c[0] in ids for c in cards))
            pg.fill("#cf-min", "300")
            pg.wait_for_timeout(500)
            cnt2 = pg.evaluate("document.getElementById('cat-count').textContent")
            cards = pg.evaluate(CARDS)
            chk(f"{w}px: фільтр «ціна від 300» працює серед уцінених ({cnt2})", all(c[1] >= 300 for c in cards) and all(c[0] in ids for c in cards) and 0 < len(cards) < len(ids))
            hh = pg.evaluate("[...document.querySelectorAll('h1.cat-h1')].map(e=>parseFloat(getComputedStyle(e).fontSize))")
            chk(f"{w}px: назва крупніша за колишню (≥ 24 px): {hh}", hh and hh[0] >= 24)
        chk(f"{w}px: без помилок JS", errs == [])
        pg.close()
    br.close()
# Акції: розмітка сторінки з панеллю і прихованою назвою
bs.SALE_ACTIVE = {"week": "w", "pct": 15, "from": None, "until": None, "items": {}}
sale = [dict(id=str(900 + i), name=f"Акційна іграшка {i}", category=f"А{i % 3}", price=200 + i, old_price=260 + i, contribution=90, stock=5, photo="data:image/gif;base64,R0lGODlhAQABAAAAACw=", desc="") for i in range(30)]
bs.write_aktsii(sale)
a1 = (out / "aktsii.html").read_text(encoding="utf-8")
chk("Акції: панель сортування є, назва — лише sr-only (видимого заголовка нема), #cat-ids = 30 id", 'id="cat-controls"' in a1 and '<h1 class="page cat-h1 sr-only">Акції</h1>' in a1 and a1.count('"9') >= 30)
bs.SALE_ACTIVE = None
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
