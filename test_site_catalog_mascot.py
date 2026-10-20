#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_catalog_mascot.py — 20.10.2026 (власник: «плутуса треба анімувати як на головній… клікабельність та сама»): Плутус у порожньому місці панелі «Усі категорії»/категорії (під іконками шапки).
Той самий анімований Плутус, що в героя головної (poster + кліп, initMascot): розмітка #hero-mascot/#hero-mascot-clip з data-clips; реагує на клік/наведення; один екземпляр на сторінку; на головній розмітка не змінилась;
на службових сторінках (Уцінка/Акції/чипи) його нема; на вузьких екранах схований; панель не заходить під липку шапку (Плутус цілком усередині панелі)."""
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


def toysi(pid, cat):
    return {"id": str(pid), "name": f"Іграшка {pid}", "vendor": "T", "price": 250 + pid % 100, "stock": 5, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": "x"}


catalog = {str(i): toysi(i, "Машинки" if i % 2 else "Ляльки") for i in range(1, 61)}
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.CATEGORY_GROUPS_FILE = str(out / "none.json")
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()
shutil.copytree(HERE / "site" / "assets", out / "assets", dirs_exist_ok=True)

cat_page = sorted(p.name for p in out.glob("category-*.html") if "_" not in p.stem)[0]
for name in ("catalog.html", cat_page):
    h = (out / name).read_text(encoding="utf-8")
    bar = re.search(r'<div class="cat-bar">(.*?)</div>\s*(?:<script|<div id="cat-|<div class="cat-top|<nav|<p )', h, re.S)
    chk(f"{name}: у панелі (.cat-bar) є .cat-mascot з анімованим Плутусом (poster + кліп, data-clips), рівно один #hero-mascot", 'class="cat-bar"' in h and h.count('class="cat-mascot"') == 1 and h.count('id="hero-mascot"') == 1 and h.count('id="hero-mascot-clip"') == 1 and "data-clips=" in h)
    chk(f"{name}: poster lazy (на вузьких екранах схований CSS — не вантажиться), кліпи — ті самі, що на головній", 'loading="lazy"' in re.search(r'<img class="mascot" id="hero-mascot"[^>]*>', h).group(0))
home = (out / "index.html").read_text(encoding="utf-8")
chk("головна: Плутус у герої без змін (розмітка hero2 + mascot-wrap, один екземпляр, не lazy)", home.count('id="hero-mascot"') == 1 and '<div class="hero2"><span class="mascot-wrap"><img class="mascot" id="hero-mascot"' in home and 'loading="lazy"' not in re.search(r'<img class="mascot" id="hero-mascot"[^>]*>', home).group(0) and "cat-mascot" not in home)
# data-clips на каталозі = на головній (одна ротація)
dc = lambda h: re.search(r'data-clips="([^"]*)"', h).group(1)
chk("ротація рухів у каталозі та на головній однакова (data-clips)", dc(home) == dc((out / "catalog.html").read_text(encoding="utf-8")))
special = [n for n in ("utsinka.html", "aktsii.html", "halloween.html") if (out / n).exists()]
chk(f"службові сторінки ({special or 'не збудовано у фікстурі'}): Плутуса в панелі нема", all("cat-mascot" not in (out / n).read_text(encoding="utf-8") for n in special))
css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
chk("CSS: ті самі правила реакції, що на головній (курсор, без підсвітки тапу, .poke, .clip-on, .dip), схований ≤1100 px, панель вища лише коли є Плутус", all(x in css for x in (".cat-mascot .mascot-wrap.poke{transform:scale(.9)}", ".cat-mascot .mascot-wrap.clip-on #hero-mascot{visibility:hidden}", ".cat-mascot .mascot-wrap.dip{opacity:0}", "cursor:pointer", "@media(max-width:1100px){.cat-mascot{display:none}}", ".cat-bar:has(.cat-mascot)")) and "@keyframes" not in css and "animation" not in css)

H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
from playwright.sync_api import sync_playwright  # noqa: E402

GEOM = "(()=>{const w=document.querySelector('.cat-mascot .mascot-wrap').getBoundingClientRect(), b=document.querySelector('.cat-bar').getBoundingClientRect(), h=document.querySelector('header.top').getBoundingClientRect(); const ig=document.querySelector('header a[href*=instagram]').getBoundingClientRect(); return {wt:w.top, wb:w.bottom, wl:w.left, wr:w.right, bt:b.top, bb:b.bottom, hb:h.bottom, igc:ig.left+ig.width/2, vis:getComputedStyle(document.querySelector('.cat-mascot')).display}})()"
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for name in ("catalog.html", cat_page):
        pg = br.new_page(viewport={"width": 1700, "height": 700})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.goto(f"{base}/{name}", wait_until="load")
        pg.wait_for_timeout(700)
        g = pg.evaluate(GEOM)
        chk(f"{name}: на широкому екрані Плутус видно, цілком усередині панелі (не під шапкою) і біля іконок шапки (центр {round((g['wl'] + g['wr']) / 2)} px, іконка Instagram {round(g['igc'])} px)", g["vis"] != "none" and g["wt"] >= g["bt"] - 1 and g["wb"] <= g["bb"] + 1 and g["wt"] >= g["hb"] - 1 and abs((g["wl"] + g["wr"]) / 2 - g["igc"]) < 60)
        pg.mouse.wheel(0, 600)
        pg.wait_for_timeout(300)
        g2 = pg.evaluate(GEOM)
        chk(f"{name}: після прокрутки (панель липка) Плутус лишається цілком під шапкою", g2["wt"] >= g2["hb"] - 1 and g2["wb"] <= g2["bb"] + 1)
        pg.mouse.wheel(0, -2000)
        pg.wait_for_timeout(200)
        box = pg.locator(".cat-mascot .mascot-wrap").bounding_box()
        pg.wait_for_timeout(1500)
        pg.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        pg.wait_for_timeout(900)
        st = pg.evaluate("[document.querySelector('.cat-mascot .mascot-wrap').classList.contains('clip-on'), (document.getElementById('hero-mascot-clip').getAttribute('src')||'')]")
        chk(f"{name}: клік по Плутусу запускає рух так само, як на головній (clip-on={st[0]}, src={st[1].split('/')[-1][:24]})", st[0] is True and "plutus_" in st[1])
        chk(f"{name}: без помилок JS", errs == [])
        pg.close()
    pg = br.new_page(viewport={"width": 900, "height": 700})
    pg.route("**/googletagmanager.com/**", lambda r: r.abort())
    pg.goto(f"{base}/{cat_page}", wait_until="load")
    pg.wait_for_timeout(500)
    chk("вузький екран (900 px): Плутус схований (display:none), бо вільного місця нема", pg.evaluate("getComputedStyle(document.querySelector('.cat-mascot')).display") == "none")
    pg.close()
    br.close()
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
