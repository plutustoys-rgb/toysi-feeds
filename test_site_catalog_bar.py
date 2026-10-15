#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_catalog_bar.py — 15.10.2026 (власник, скетч затверджено): повний каталог — «Каталог» в одну лінію з сортуванням/категоріями/ціною; панель ЛИПКА під шапкою (сортування й категорії не зникають при прокрутці); на вузьких екранах липне ряд
[сортування][категорія][Ціна], ціна від/до + «Скинути» розкриваються кнопкою «Ціна»; без хлібних крихт і «Сторінка 1 з N» на стор.1. Chromium, 4 ширини. SEO: H1 для читачів екрана лишається «Каталог іграшок», BreadcrumbList у <head>."""
import contextlib
import functools
import http.server
import io
import os
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


cats = ["Конструктори", "Ляльки", "Машинки", "Настільні ігри", "Пазли", "М'які іграшки"]
catalog = {str(i): item(i, f"Іграшка «{cats[i % 6]}» номер {i}", cats[i % 6], 150 + (i * 7) % 500) for i in range(1, 260)}
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()
shutil.copytree(HERE / "site" / "assets", out / "assets", dirs_exist_ok=True)
html = (out / "catalog.html").read_text(encoding="utf-8")
html2 = (out / "catalog_2.html").read_text(encoding="utf-8")
body1 = html.split('<div class="wrap">')[1].split("<footer")[0]
chk("розмітка стор.1: панель .cat-bar з H1 «Каталог» (+sr-only «іграшок») і контролами; хлібних крихт і «Сторінка 1 з N» нема; BreadcrumbList у <head> лишається",
    '<div class="cat-bar"><h1 class="page cat-h1">Каталог<span class="sr-only"> іграшок</span></h1><div id="cat-controls"' in html and 'class="crumbs"' not in body1 and "Сторінка 1 з" not in body1 and '"BreadcrumbList"' in html)
chk("сторінка 2 (статична, для SEO): «Сторінка 2 з N» лишається, крихт нема, панелі нема", "Сторінка 2 з" in html2 and 'id="cat-controls"' not in html2 and 'class="crumbs"' not in html2.split('<div class="wrap">')[1].split("<footer")[0])
chk("порядок контролів: сортування → категорія → «Ціна» → ціна від/до → «Скинути»; «Скинути» всередині панелі ціни", html.index('id="cf-sort"') < html.index('id="cf-cat"') < html.index('id="cf-price-toggle"') < html.index('id="cf-min"') < html.index('id="cf-max"') < html.index('id="cf-reset"') and html.index('id="cf-price-panel"') < html.index('id="cf-reset"'))

H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None


class _Srv(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


srv = _Srv(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
from playwright.sync_api import sync_playwright  # noqa: E402

R = "(s=>{const e=document.querySelector(s); if(!e) return null; const b=e.getBoundingClientRect(); return {l:Math.round(b.left), r:Math.round(b.right), t:Math.round(b.top), b:Math.round(b.bottom), w:Math.round(b.width), h:Math.round(b.height), d:getComputedStyle(e).display}})"
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w, h in ((1400, 800), (1000, 800), (768, 800), (390, 800)):
        wide = w >= 1000
        ctx = br.new_context(viewport={"width": w, "height": h})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("https://toysi.ua/**", lambda r: r.abort())
        pg.goto(f"{base}/catalog.html", wait_until="load")
        pg.wait_for_selector("#cat-js .card")
        pg.wait_for_timeout(300)
        g = {k: pg.evaluate(R + f"('{s}')") for k, s in (("hdr", "header.top"), ("h1", ".cat-h1"), ("sort", "#cf-sort"), ("cat", "#cf-cat"), ("tog", "#cf-price-toggle"), ("pan", "#cf-price-panel"), ("min", "#cf-min"), ("rst", "#cf-reset"), ("cnt", "#cat-count"))}
        hh = g["hdr"]["h"]
        if wide:
            row = [g["h1"], g["sort"], g["cat"], g["min"], g["rst"]]
            chk(f"{w}px: «Каталог», сортування, категорія, ціна від, «Скинути» — в ОДНІЙ лінії (центри по вертикалі в межах 6 px) {[ (x['t'] + x['b']) // 2 for x in row]}", max((x["t"] + x["b"]) / 2 for x in row) - min((x["t"] + x["b"]) / 2 for x in row) <= 6)
            chk(f"{w}px: порядок зліва направо: Каталог → сортування → категорія → ціна → Скинути; кнопка «Ціна» схована", g["h1"]["r"] <= g["sort"]["l"] and g["sort"]["r"] <= g["cat"]["l"] and g["cat"]["r"] <= g["min"]["l"] and g["min"]["r"] <= g["rst"]["l"] and g["tog"]["d"] == "none")
        else:
            chk(f"{w}px: ряд [сортування][категорія][Ціна] в одній лінії, ціна від/до схована до натискання «Ціна»", abs((g["sort"]["t"] + g["sort"]["b"]) - (g["cat"]["t"] + g["cat"]["b"])) <= 6 and abs((g["sort"]["t"] + g["sort"]["b"]) - (g["tog"]["t"] + g["tog"]["b"])) <= 6 and g["tog"]["d"] != "none" and g["pan"]["d"] == "none")
        # прокрутка: сортування й категорія лишаються на екрані прямо під шапкою
        pg.evaluate("window.scrollTo(0, 1500)")
        pg.wait_for_timeout(300)
        s2 = {k: pg.evaluate(R + f"('{s}')") for k, s in (("sort", "#cf-sort"), ("cat", "#cf-cat"), ("hdr", "header.top"))}
        ys = pg.evaluate("window.scrollY")
        chk(f"{w}px: після прокрутки на {ys} px сортування й категорія ЛИШИЛИСЬ під шапкою (top {s2['sort']['t']} ≥ {hh}, у межах екрана), шапка теж на місці", ys > 500 and hh <= s2["sort"]["t"] <= hh + 24 and s2["cat"]["t"] == s2["sort"]["t"] or (abs(s2["cat"]["t"] - s2["sort"]["t"]) <= 4 and ys > 500 and hh <= s2["sort"]["t"] <= hh + 24 and s2["hdr"]["t"] == 0))
        hit = pg.evaluate("(()=>{const e=document.getElementById('cf-sort'); const b=e.getBoundingClientRect(); const t=document.elementFromPoint(b.left+b.width/2,b.top+b.height/2); return e===t||e.contains(t)})()")
        chk(f"{w}px: прикріплене сортування клікабельне (його не перекриває ні шапка, ні картки)", hit)
        pg.select_option("#cf-sort", "cheap")
        pg.wait_for_timeout(300)
        first = pg.evaluate("[...document.querySelectorAll('#cat-js .card .pr')].slice(0,3).map(e=>parseInt(e.textContent))")
        chk(f"{w}px: сортування з липкої панелі працює (спершу дешевші: {first})", first == sorted(first))
        if not wide:
            pg.evaluate("window.scrollTo(0, 0)")
            pg.click("#cf-price-toggle")
            pg.wait_for_timeout(200)
            op = pg.evaluate(R + "('#cf-price-panel')")
            vw = pg.evaluate("innerWidth")
            chk(f"{w}px: «Ціна» розкриває панель від/до/«Скинути» в межах екрана ({op['l']}…{op['r']} із {vw}), aria-expanded=true", op["d"] != "none" and op["l"] >= 0 and op["r"] <= vw and pg.evaluate("document.getElementById('cf-price-toggle').getAttribute('aria-expanded')") == "true")
            pg.fill("#cf-min", "200")
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(300)
            chk(f"{w}px: Escape закриває панель, кнопка «Ціна» підсвічена, коли фільтр ціни активний", pg.evaluate("getComputedStyle(document.getElementById('cf-price-panel')).display") == "none" and pg.evaluate("document.getElementById('cf-price-toggle').classList.contains('on')"))
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
