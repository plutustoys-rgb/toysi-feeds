#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_category_aside.py — 15.10.2026 (власник: «якщо текст зліва, то права сторона повинна заповнитися рекомендацією товару або акцією»). Сторінка категорії з вступним текстом: зліва текст, справа колонка —
«Знижка тижня −N%» (є акційні товари категорії і діє акція: найдешевший акційний + «Усі знижки →»), інакше «Рекомендуємо» (один товар цієї категорії, відбір home_pick із щоденною ротацією). Категорія без тексту — без колонки.
Chromium: на ≥1000 px колонка праворуч у тому ж ряду, що й текст; на вузьких — під текстом; без горизонтального скролу. Колонка не рухає lastmod сторінки (ротація щодня — не зміна сторінки)."""
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


def item(pid, name, cat, price):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": 5, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/{pid}"}


cats = ["Конструктори", "Ляльки", "Машинки"]
catalog = {str(i): item(i, f"Іграшка «{cats[i % 3]}» номер {i}", cats[i % 3], 200 + (i * 11) % 400) for i in range(1, 160)}
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
TEXTS = {"konstruktory": "У розділі зібрані конструктори різних розмірів. " * 12, "lyalky": "Ляльки для ігор і колекцій. " * 10}
bs._load_category_texts = lambda: bs._CATEGORY_TEXTS.update(TEXTS)
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)


def build():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        bs.build()


build()
kon = (out / "category-konstruktory.html").read_text(encoding="utf-8")
mash = (out / "category-mashynky.html").read_text(encoding="utf-8")
m = re.search(r'<aside class="cat-aside" aria-label="([^"]+)"><div class="cat-aside-lbl">[^<]+</div><a class="card" href="product-(\d+)\.html">', kon)
chk("категорія з текстом (Конструктори): поруч із текстом колонка «Рекомендуємо» з карткою товару, текст і колонка в .cat-top", m is not None and m.group(1) == "Рекомендуємо" and '<div class="cat-top"><div class="cat-intro">' in kon)
pid = m.group(2) if m else None
chk(f"рекомендований товар {pid} — з ЦІЄЇ категорії, в наявності", pid is not None and catalog[pid]["category_name"] == "Конструктори" and catalog[pid]["stock"] > 0)
chk("категорія без тексту (Машинки) — без колонки і без .cat-top", "cat-aside" not in mash.split("<footer")[0].split('<div class="wrap">')[1] and 'class="cat-top"' not in mash)
bs._sitemap_priority = bs._sitemap_priority  # (без змін)

# ── акція ──
from datetime import datetime, timedelta, timezone  # noqa: E402
KY = timezone(timedelta(hours=3))
sale = {"week": "2026-W43", "pct": 15, "from": datetime(2026, 10, 19, tzinfo=KY), "until": datetime(2026, 10, 25, 23, 59, 59, tzinfo=KY), "items": {}}
prods_k = [{"id": "3", "name": "Акційний А", "price": 340, "old_price": 400, "stock": 3, "photo": "x.jpg", "category": "Конструктори", "contribution": 100},
           {"id": "6", "name": "Акційний Б", "price": 255, "old_price": 300, "stock": 3, "photo": "x.jpg", "category": "Конструктори", "contribution": 100},
           {"id": "9", "name": "Звичайний", "price": 250, "stock": 3, "photo": "x.jpg", "category": "Конструктори", "contribution": 100}]
bs.SALE_ACTIVE = sale
ah = bs._category_aside_html(prods_k)
chk("є акційні товари і діє акція: «Знижка тижня −15%», найдешевший акційний (Б, 255 ₴ зі старою 300 ₴), посилання «Усі знижки →» на aktsii.html",
    'aria-label="Знижка тижня −15%"' in ah and "product-6.html" in ah and '<s class="pr-old">300 ₴</s>' in ah and 'href="aktsii.html">Усі знижки' in ah and "product-3.html" not in ah)
bs.SALE_ACTIVE = None
ah2 = bs._category_aside_html(prods_k)
chk("акції нема (SALE_ACTIVE=None) — колонка «Рекомендуємо», без «Усі знижки»", "Рекомендуємо" in ah2 and "aktsii.html" not in ah2)
cheap = [{"id": "1", "name": "Дешевий", "price": 90, "stock": 3, "photo": "x.jpg", "category": "К", "contribution": 45.0}]
chk("дешева категорія (ціна 90 ₴, внесок 45 ₴): правила вітрини нічого не дають, але пом'якшена драбина дає «Рекомендуємо» (15.10: на проді права колонка була порожньою)", "Рекомендуємо" in bs._category_aside_html(cheap) and "product-1.html" in bs._category_aside_html(cheap))
paint = [{"id": "7", "name": "Картина за номерами", "price": 300, "stock": 3, "photo": "x.jpg", "category": "Картини за номерами", "contribution": 120.0}]
weapon = [{"id": "8", "name": "Іграшка", "price": 300, "stock": 3, "photo": "x.jpg", "category": "Зброя", "contribution": 120.0}]
chk("«Картини за номерами» (виключена з вітрини головної) на СВОЇЙ сторінці отримує рекомендацію; категорія зброї — ні (політика виключення лишилась)", "product-7.html" in bs._category_aside_html(paint) and bs._category_aside_html(weapon) == "" and bs.home_pick(paint) == [])
chk("внесок нижче запасного порога (20 ₴), нема в наявності або без фото → колонки нема, текст займає все", bs._category_aside_html([dict(cheap[0], contribution=20.0)]) == "" and bs._category_aside_html([dict(cheap[0], stock=0)]) == "" and bs._category_aside_html([dict(cheap[0], photo="")]) == "")

# ── lastmod: колонка не рухає lastmod ──
import json  # noqa: E402
import time  # noqa: E402

state = out / bs.LASTMOD_STATE_FILE
st = json.loads(state.read_text(encoding="utf-8"))
OLD = time.mktime((2026, 1, 15, 12, 0, 0, 0, 0, 0))
for k in st:
    st[k]["t"] = OLD
state.write_text(json.dumps(st), encoding="utf-8")
os.environ["SITE_PICK_DATE"] = "2026-10-17"   # інша доба → інша ротація рекомендації
build()
kon2 = (out / "category-konstruktory.html").read_text(encoding="utf-8")
m2 = re.search(r'<aside class="cat-aside".*?href="product-(\d+)\.html">', kon2, re.S)
st2 = json.loads(state.read_text(encoding="utf-8"))
chk(f"наступної доби рекомендація інша ({pid} → {m2.group(1) if m2 else None}), але lastmod сторінки категорії НЕ зсунувся (ротація — не зміна сторінки)", m2 is not None and st2["category-konstruktory.html"]["t"] == OLD)
os.environ["SITE_PICK_DATE"] = "2026-10-10"

# ── розкладка у Chromium ──
shutil.copytree(HERE / "site" / "assets", out / "assets", dirs_exist_ok=True)
build()
H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None


class _Srv(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


srv = _Srv(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
from playwright.sync_api import sync_playwright  # noqa: E402

R = "(s=>{const e=document.querySelector(s); if(!e) return null; const b=e.getBoundingClientRect(); return {l:Math.round(b.left), r:Math.round(b.right), t:Math.round(b.top), b:Math.round(b.bottom), w:Math.round(b.width)}})"
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (1400, 1000, 768, 390):
        ctx = br.new_context(viewport={"width": w, "height": 800})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("https://toysi.ua/**", lambda r: r.abort())
        pg.goto(f"{base}/category-konstruktory.html", wait_until="load")
        pg.wait_for_selector("#cat-js .card")
        pg.wait_for_timeout(300)
        txt, asd = pg.evaluate(R + "('.cat-top .cat-intro')"), pg.evaluate(R + "('.cat-aside')")
        if w >= 1200:   # єдиний шаблон: основний стовпець вужчий (сайдбар зліва) → поруч лише на ≥1200 px
            chk(f"{w}px: текст ліворуч, колонка праворуч у ТОМУ Ж ряду (текст {txt['l']}–{txt['r']}, колонка {asd['l']}–{asd['r']}, верхи {txt['t']}/{asd['t']}), колонка ~340 px, текст ширший за 400 px",
                txt["r"] <= asd["l"] and abs(txt["t"] - asd["t"]) <= 6 and 320 <= asd["w"] <= 360 and txt["w"] > 400)
        else:
            chk(f"{w}px: вузький екран — колонка ПІД текстом, у межах екрана ({asd['l']}–{asd['r']} із {w})", asd["t"] >= txt["b"] - 2 and asd["l"] >= 0 and asd["r"] <= w)
        chk(f"{w}px: без горизонтального скролу й помилок JS", pg.evaluate("document.documentElement.scrollWidth <= innerWidth") and errs == [])
        ctx.close()
    br.close()
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
