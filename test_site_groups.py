#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_groups.py — 15.10.2026 (власник: «у Toysi гарна розкладка за категоріями, чому ми нею не користуємось?», скетч затверджено). Групи Toysi (site/category_groups.json) → сторінки груп group-*.html:
сайдбар підкатегорій зліва (клік фільтрує на місці, href веде на сторінку підкатегорії), H1 окремим рядком, фільтри ПІД ним і липкі, на ПК списку категорій у фільтрах нема (його замінює сайдбар), на телефоні сайдбару нема, підкатегорія — зі списку;
«Усі категорії» = лише групи (+ «Окремі розділи» для категорій поза видимими групами, щоб жодна не лишилась без посилання); групи в sitemap; shipped-файл розкладки валідний. Chromium."""
import contextlib
import functools
import http.server
import io
import json
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


# фікстура: група «Творчість» (3 підкатегорії), група «Транспорт» (2), прихована «Уцінка», категорія без групи «Сирота»
LAYOUT = {"Творчість": ("Картини", "Розмальовки", "Пісок"), "Транспорт": ("Самокати", "Велосипеди"), "Уцінка": ("Уцінені товари",)}
catalog, pid = {}, 1
for g, subs in LAYOUT.items():
    for k, sub in enumerate(subs):
        for j in range(10 + 6 * k):
            catalog[str(pid)] = item(pid, f"{sub} модель {pid}", sub, 200 + (pid * 13) % 300)
            pid += 1
for j in range(5):
    catalog[str(pid)] = item(pid, f"Транспортна дрібниця {pid}", "Транспорт", 150 + j)   # власна категорія групи (товари прямо в корені) -> «Інше»
    pid += 1
for j in range(7):
    catalog[str(pid)] = item(pid, f"Сирота {pid}", "Сирота", 300 + j)
    pid += 1
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
gj = Path(tempfile.mkdtemp()) / "category_groups.json"
gj.write_text(json.dumps({"groups": [
    {"name": "Творчість", "display": "Товари для творчості", "subs": ["Картини", "Розмальовки", "Пісок", "Немає на сайті"]},
    {"name": "Транспорт", "subs": ["Самокати", "Велосипеди"]},
    {"name": "Уцінка", "subs": ["Уцінені товари"], "hide": True},
    {"name": "Порожня", "subs": ["Нема такої"]}]}, ensure_ascii=False), encoding="utf-8")
bs.CATEGORY_GROUPS_FILE = str(gj)
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()
files = sorted(f.name for f in out.glob("group-*.html"))
chk(f"сторінки груп: лише видимі групи з ≥ {bs.GROUP_MIN_PRODUCTS} товарів ({files}); прихована «Уцінка» і порожня — ні", files == ["group-tovary-dlia-tvorchosti.html", "group-transport.html"])
g1 = (out / "group-tovary-dlia-tvorchosti.html").read_text(encoding="utf-8")
body = g1.split('<div class="wrap">')[1].split("<footer")[0]
n_group = 10 + 16 + 22
chk(f"сайдбар: «Усі товари ({n_group})» активна + 3 підкатегорії за кількістю (спадання), кожна — посилання на свою сторінку категорії з data-sub; «Немає на сайті» не показано",
    f'<a class="sb-i all on" href="group-tovary-dlia-tvorchosti.html" data-sub=""><span>Усі товари</span><em>{n_group}</em></a>' in g1
    and re.findall(r'<a class="sb-i" href="(category-[a-z0-9-]+\.html)" data-sub="([^"]+)"><span>[^<]+</span><em>(\d+)</em>', g1) == [(f"category-{bs.slugify(n)}.html", n, str(c)) for n, c in (("Пісок", 22), ("Розмальовки", 16), ("Картини", 10))]
    and "Немає на сайті" not in g1)
chk("H1 = назва групи (display), панель .cat-bar з контролами data-group (JSON підкатегорій), крихти «Головна › Усі категорії › Група», статична перша сторінка (≤ PER_PAGE карток) у #cat-static",
    '<h1 class="page cat-h1">Товари для творчості</h1>' in g1 and 'data-group=' in g1 and "Пісок" in re.search(r"data-group='([^']+)'", g1).group(1).replace("&quot;", '"') and 'class="crumbs"' in body
    and 0 < len(re.findall(r'<a class="card"', g1.split('id="cat-static"')[1])) <= bs.PER_PAGE)
cats_html = (out / "categories.html").read_text(encoding="utf-8")
tiles = []
chk("стара «Усі категорії» (categories.html) = перехід на /catalog.html: meta refresh, noindex, canonical catalog.html; у sitemap її нема", 'http-equiv="refresh" content="0; url=catalog.html"' in cats_html and "noindex" in cats_html and 'rel="canonical" href="https://plutustoys.com.ua/catalog.html"' in cats_html and "/categories.html</loc>" not in (out / "sitemap-static.xml").read_text(encoding="utf-8"))
cat_page = (out / "catalog.html").read_text(encoding="utf-8")
navs = re.findall(r'<a class="sb-i sb-nav" href="([^"]+)"><span>([^<]+)</span><em>(\d+)</em>', cat_page)
chk(f"«Усі категорії» (/catalog.html): сайдбар «Категорії» лише з групами (кожна веде на свою сторінку; без «Усі товари», без підкатегорій), H1 «Усі категорії», крихти «Головна › Усі категорії», слова «Каталог» як пункту нема: {[(n[1], n[2]) for n in navs]}",
    [n[1] for n in navs] == ["Товари для творчості", "Транспорт"] and all(n[0].startswith("group-") for n in navs) and '<div class="sb-h">Категорії</div>' in cat_page and "Усі товари" not in cat_page.split('<footer')[0].split('<aside class="sb"')[1].split("</aside>")[0]
    and '<h1 class="page cat-h1">Усі категорії<span class="sr-only"> — каталог іграшок</span></h1>' in cat_page and 'class="crumbs"' in cat_page)
g_side = (out / "group-tovary-dlia-tvorchosti.html").read_text(encoding="utf-8").split('<aside class="sb"')[1].split("</aside>")[0]
chk("сторінка групи: надпису «Підкатегорії» нема, перший пункт «Усі товари»", "Підкатегорії</div>" not in g_side and 'class="sb-h"' not in g_side and g_side.split("<span>")[1].startswith("Усі товари"))
home_html = (out / "index.html").read_text(encoding="utf-8")
catrow = re.search(r'<div class="catrow">(.*?)</div></a></div>', home_html, re.S)
rows = re.findall(r'<a class="cattile" href="([^"]+)">', catrow.group(1) + "</div></a>") if catrow else []
chk(f"головна: блок «Категорії» = плитки ГРУП (власник 15.10: «не бачу змін за скетчем»), посилання на group-*.html: {rows}", rows[:2] == ["group-tovary-dlia-tvorchosti.html", "group-transport.html"] and all(r.startswith("group-") for r in rows))
sm = (out / "sitemap-categories.xml").read_text(encoding="utf-8")
chk("групи в sitemap (сегмент categories)", "/group-tovary-dlia-tvorchosti.html</loc>" in sm and "/group-transport.html</loc>" in sm)
shipped = json.loads((HERE / "site" / "category_groups.json").read_text(encoding="utf-8"))
sg = [g for g in shipped["groups"] if not g.get("hide")]
chk(f"site/category_groups.json у репо: {len(shipped['groups'])} груп, {sum(len(g['subs']) for g in shipped['groups'])} підкатегорій, у видимих — назви й підписи непорожні, без дублів підкатегорій між групами",
    len(shipped["groups"]) >= 20 and all(g["name"] and g["subs"] for g in shipped["groups"]) and len({s for g in shipped["groups"] for s in g["subs"]}) == sum(len(g["subs"]) for g in shipped["groups"]))
chk("«Роботи, Машини та Зброя» показується як «Роботи та машини» (підпис; категорії зброї не змінені)", any(g["name"] == "Роботи, Машини та Зброя" and g.get("display") == "Роботи та машини" for g in shipped["groups"]))

g2 = (out / "group-transport.html").read_text(encoding="utf-8")
subs2 = re.findall(r'<a class="sb-i" href="(category-[a-z0-9-]+\.html)" data-sub="([^"]+)"><span>([^<]+)</span><em>(\d+)</em>', g2)
chk(f"власна категорія групи (товари прямо в корені «Транспорт») — остання підкатегорія «Інше», до загального числа й не в «Окремих розділах»: {[(x[2], x[3]) for x in subs2]}",
    [x[2] for x in subs2][-1] == "Інше" and subs2[-1][1] == "Транспорт" and subs2[-1][3] == "5" and "category-transport.html" not in tiles and re.search(r'<a class="sb-i all on"[^>]*><span>Усі товари</span><em>(\d+)</em>', g2).group(1) == str(sum(int(x[3]) for x in subs2)))

# ── Chromium ──
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

R = "(s=>{const e=document.querySelector(s); if(!e) return null; const b=e.getBoundingClientRect(); return {l:Math.round(b.left), r:Math.round(b.right), t:Math.round(b.top), b:Math.round(b.bottom), w:Math.round(b.width), d:getComputedStyle(e).display}})"
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (1400, 390):
        wide = w >= 1000
        ctx = br.new_context(viewport={"width": w, "height": 800})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("https://toysi.ua/**", lambda r: r.abort())
        pg.goto(f"{base}/group-tovary-dlia-tvorchosti.html", wait_until="load")
        pg.wait_for_selector("#cat-js .card")
        pg.wait_for_timeout(300)
        h1, ctl, sb, grid_, sel = (pg.evaluate(R + f"('{s}')") for s in (".cat-h1", "#cat-controls", ".sb", "#cat-js", "#cf-cat"))
        total = int(re.search(r"Знайдено: (\d+)", pg.evaluate("document.getElementById('cat-count').textContent")).group(1))
        chk(f"{w}px: у групі {total} товарів (усі підкатегорії), лише з її підкатегорій", total == n_group and pg.evaluate("[...document.querySelectorAll('#cat-js .card .nm')].every(e=>/^(Картини|Розмальовки|Пісок) модель/.test(e.textContent))"))
        chk(f"{w}px: назва групи окремим рядком, ФІЛЬТРИ ПІД нею (h1 низ {h1['b']} ≤ фільтри верх {ctl['t']})", h1["b"] <= ctl["t"] + 2)
        if wide:
            chk(f"{w}px: сайдбар зліва від сітки ({sb['l']}–{sb['r']} | сітка від {grid_['l']}), список категорії у фільтрах схований ({sel['d']})", sb["r"] <= grid_["l"] and sel["d"] == "none")
            pg.click('.sb-i[data-sub="Розмальовки"]')
            pg.wait_for_timeout(300)
            cnt = pg.evaluate("document.getElementById('cat-count').textContent")
            ok_names = pg.evaluate("[...document.querySelectorAll('#cat-js .card .nm')].every(e=>e.textContent.indexOf('Розмальовки')===0)")
            on = pg.evaluate("[...document.querySelectorAll('.sb-i.on')].map(a=>a.getAttribute('data-sub'))")
            chk(f"{w}px: клік «Розмальовки» в сайдбарі фільтрує НА МІСЦІ (без переходу): {cnt}, активна лише вона {on}, URL лишився {pg.url.split('/')[-1]}", "Знайдено: 16" in cnt and ok_names and on == ["Розмальовки"] and pg.url.endswith("group-tovary-dlia-tvorchosti.html"))
            pg.click('.sb-i.all')
            pg.wait_for_timeout(300)
            chk(f"{w}px: «Усі товари» повертає всі {n_group}", f"Знайдено: {n_group}" in pg.evaluate("document.getElementById('cat-count').textContent") and pg.evaluate("[...document.querySelectorAll('.sb-i.on')].length") == 1)
        else:
            chk(f"{w}px: сайдбару нема, підкатегорія вибирається зі списку у фільтрах ({sel['d']})", sb["d"] == "none" and sel["d"] != "none")
            pg.select_option("#cf-cat", "Пісок")
            pg.wait_for_timeout(300)
            cnt_txt = pg.evaluate("document.getElementById('cat-count').textContent")
            chk(f"{w}px: вибір «Пісок» зі списку фільтрує: {cnt_txt}", "Знайдено: 22" in cnt_txt)
        # прокрутка: фільтри (і на ПК сайдбар) лишаються на екрані
        pg.evaluate("window.scrollTo(0, 1200)")
        pg.wait_for_timeout(300)
        ctl2 = pg.evaluate(R + "('#cat-controls')")
        chk(f"{w}px: після прокрутки фільтри лишились під шапкою (top {ctl2['t']} ≈ {h1.get('hdr', 64)})", 60 <= ctl2["t"] <= 80)
        if wide:
            sb2 = pg.evaluate(R + "('.sb')")
            chk(f"{w}px: після прокрутки сайдбар лишився на екрані (top {sb2['t']}, у межах вікна)", 0 < sb2["t"] < 200)
        chk(f"{w}px: без горизонтального скролу й помилок JS", pg.evaluate("document.documentElement.scrollWidth <= innerWidth") and errs == [])
        ctx.close()
    br.close()
# ── «Усі категорії» (/catalog.html): група веде на свою сторінку; на телефоні список категорій згруповано ──
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (1400, 390):
        wide = w >= 1000
        ctx = br.new_context(viewport={"width": w, "height": 800})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("https://toysi.ua/**", lambda r: r.abort())
        pg.goto(f"{base}/catalog.html", wait_until="load")
        pg.wait_for_selector("#cat-js .card")
        pg.wait_for_timeout(300)
        cnt0 = pg.evaluate("document.getElementById('cat-count').textContent")
        h1b, ctl = pg.evaluate(R + "('.cat-h1')"), pg.evaluate(R + "('#cat-controls')")
        chk(f"/catalog.html {w}px: «Усі категорії» — усі товари ({cnt0}), назва окремим рядком, фільтри під нею", pg.evaluate("document.querySelector('.cat-h1').textContent").startswith("Усі категорії") and h1b["b"] <= ctl["t"] + 2 and re.search(r"Знайдено: (\d+)", cnt0) is not None)
        if wide:
            pg.click('.sb-nav[href="group-transport.html"]')
            pg.wait_for_url("**/group-transport.html")
            pg.wait_for_selector("#cat-js .card")
            chk(f"/catalog.html {w}px: клік по категорії «Транспорт» відкриває сторінку групи ({pg.url.split('/')[-1]}), а не фільтрує на місці; там сайдбар підкатегорій і «Усі товари»", pg.evaluate("document.querySelector('.cat-h1').textContent") == "Транспорт" and pg.evaluate("document.querySelector('.sb .sb-i.all span').textContent") == "Усі товари")
        else:
            groups_opt = pg.evaluate("[...document.querySelectorAll('#cf-cat optgroup')].map(g=>g.label)")
            chk(f"/catalog.html {w}px: сайдбару нема, список категорій у фільтрах згруповано (optgroup): {groups_opt}", pg.evaluate(R + "('.sb')")["d"] == "none" and groups_opt[:2] == ["Товари для творчості", "Транспорт"])
            pg.select_option("#cf-cat", "Самокати")
            pg.wait_for_timeout(300)
            chk(f"/catalog.html {w}px: вибір підкатегорії «Самокати» зі списку: {pg.evaluate('document.getElementById(\"cat-count\").textContent')}", "Знайдено: 10" in pg.evaluate("document.getElementById('cat-count').textContent"))
        chk(f"/catalog.html {w}px: без горизонтального скролу й помилок JS", pg.evaluate("document.documentElement.scrollWidth <= innerWidth") and errs == [])
        ctx.close()
    br.close()
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
