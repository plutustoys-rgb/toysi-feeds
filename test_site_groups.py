#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_groups.py — 15.10.2026 (власник: «обираємо ЕДИНИЙ шаблон… вибір категорій, а після підкатегорій, і це на одній сторінці»; скетч group_sketch3). Групи Toysi (site/category_groups.json) + ЄДИНИЙ шаблон для повного каталогу, сторінок груп і категорій:
сайдбар-дерево зліва («Усі товари» → групи → підкатегорії вибраної групи), назва окремим рядком, фільтри під нею (липкі), на ПК списку категорій у фільтрах нема, на телефоні сайдбару нема, а список категорій згруповано (optgroup).
Клік фільтрує на місці, оновлює назву й адресу (pushState), «Назад» повертає; початковий стан (активне/розкрите) — уже в HTML. «Усі категорії» = перехід на каталог (без індексації). Категорії поза видимими групами — «Окремі розділи» в тому ж дереві. Chromium."""
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


# фікстура: група «Творчість» (3 підкатегорії, display «Товари для творчості»), «Транспорт» (2 + власна категорія), прихована «Уцінка», категорія без групи «Сирота»
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
TOTAL = len(catalog)
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
N_TV = 10 + 16 + 22   # «Товари для творчості»


def read(f):
    return (out / f).read_text(encoding="utf-8")


def heads_of(h):
    return re.findall(r'<a class="sb-i sb-gh(?: on)?" href="([^"]+)" data-g="(-?\d+)" data-sub=""><span>([^<]+)</span><em>(\d+)</em>', h)


files = sorted(f.name for f in out.glob("group-*.html"))
chk(f"сторінки груп: лише видимі групи з ≥ {bs.GROUP_MIN_PRODUCTS} товарів ({files}); прихована «Уцінка» і порожня — ні", files == ["group-tovary-dlia-tvorchosti.html", "group-transport.html"])
cat_html = read("catalog.html")
hs = heads_of(cat_html)
chk(f"каталог: дерево в сайдбарі — «Усі товари» (активна, {TOTAL}) + групи {[(h[2], h[3]) for h in hs]}; «Окремі розділи» — категорії поза видимими групами; усі підкатегорії згорнуті",
    f'class="sb-i all on" href="catalog.html" data-g="-1" data-sub=""><span>Усі товари</span><em>{TOTAL}</em>' in cat_html and [h[2] for h in hs] == ["Товари для творчості", "Транспорт", "Окремі розділи"]
    and "Сирота" in cat_html.split("Окремі розділи")[1] and "Уцінені товари" in cat_html.split("Окремі розділи")[1] and 'data-sel-g="-1"' in cat_html and cat_html.count('class="sb-subs" hidden') == 3)
g1 = read("group-tovary-dlia-tvorchosti.html")
body1 = g1.split('<div class="wrap">')[1].split("<footer")[0]
chk(f"група: той самий сайдбар-дерево, її голова активна й розкрита ({N_TV}), підкатегорії за кількістю (спадання), інші групи згорнуті; крихти «Головна › Каталог › Група»; H1 = display; data-sel-g=0",
    [h[2] for h in heads_of(g1)] == [h[2] for h in hs] and 'class="sb-grp open" data-g="0"' in g1 and re.search(r'<a class="sb-i sb-gh on" href="group-tovary-dlia-tvorchosti.html" data-g="0" data-sub=""><span>[^<]+</span><em>48</em>', g1)
    and re.findall(r'<a class="sb-i sb-sub" href="(category-[a-z0-9-]+\.html)" data-g="0" data-sub="([^"]+)"><span>[^<]+</span><em>(\d+)</em>', g1) == [(f"category-{bs.slugify(n)}.html", n, str(c)) for n, c in (("Пісок", 22), ("Розмальовки", 16), ("Картини", 10))]
    and '<h1 class="page cat-h1">Товари для творчості</h1>' in g1 and 'data-sel-g="0"' in g1 and 'class="crumbs"' in body1 and "Каталог" in body1.split('class="crumbs"')[1][:300]
    and 0 < len(re.findall(r'<a class="card"', g1.split('id="cat-static"')[1])) <= bs.PER_PAGE and g1.count('class="sb-subs" hidden') == 2)
c1 = read(f"category-{bs.slugify('Розмальовки')}.html")
chk("категорія: той самий шаблон — її група розкрита, підкатегорія активна, H1 = назва, data-sel-g/sel-sub, крихти «Каталог › Група › Категорія», чипів-категорій нема (їх замінює сайдбар)",
    'class="sb-grp open" data-g="0"' in c1 and re.search(r'<a class="sb-i sb-sub on" href="category-[a-z0-9-]+\.html" data-g="0" data-sub="Розмальовки">', c1) is not None and '<h1 class="page cat-h1">Розмальовки</h1>' in c1
    and 'data-sel-g="0" data-sel-sub="Розмальовки"' in c1 and 'class="chips"' not in c1.split('<div class="wrap">')[1].split("<footer")[0] and "Товари для творчості" in c1.split('class="crumbs"')[1][:400])
g2 = read("group-transport.html")
subs2 = re.findall(r'<a class="sb-i sb-sub(?: on)?" href="(category-[a-z0-9-]+\.html)" data-g="1" data-sub="([^"]+)"><span>([^<]+)</span><em>(\d+)</em>', g2)
chk(f"власна категорія групи (товари прямо в корені «Транспорт») — остання підкатегорія «Інше» (не окрема плитка): {[(x[2], x[3]) for x in subs2]}",
    [x[2] for x in subs2][-1] == "Інше" and subs2[-1][1] == "Транспорт" and subs2[-1][3] == "5")
cats_html = read("categories.html")
chk("«Усі категорії» (стара адреса) = перехід на каталог: meta refresh, noindex, canonical catalog.html; у sitemap її нема", 'http-equiv="refresh" content="0; url=catalog.html"' in cats_html and "noindex" in cats_html and 'rel="canonical" href="https://plutustoys.com.ua/catalog.html"' in cats_html
    and "/categories.html</loc>" not in read("sitemap-static.xml"))
home_html = read("index.html")
catrow = re.search(r'<div class="catrow">(.*?)</div></a></div>', home_html, re.S)
rows = re.findall(r'<a class="cattile" href="([^"]+)">', catrow.group(1) + "</div></a>") if catrow else []
chk(f"головна: блок «Категорії» = плитки ГРУП: {rows}", rows[:2] == ["group-tovary-dlia-tvorchosti.html", "group-transport.html"] and all(r.startswith("group-") for r in rows))
sm = read("sitemap-categories.xml")
chk("групи в sitemap (сегмент categories)", "/group-tovary-dlia-tvorchosti.html</loc>" in sm and "/group-transport.html</loc>" in sm)
shipped = json.loads((HERE / "site" / "category_groups.json").read_text(encoding="utf-8"))
chk(f"site/category_groups.json у репо: {len(shipped['groups'])} груп, {sum(len(g['subs']) for g in shipped['groups'])} підкатегорій, без дублів підкатегорій між групами; «Роботи, Машини та Зброя» підписано «Роботи та машини»",
    len(shipped["groups"]) >= 20 and all(g["name"] and g["subs"] for g in shipped["groups"]) and len({s for g in shipped["groups"] for s in g["subs"]}) == sum(len(g["subs"]) for g in shipped["groups"])
    and any(g["name"] == "Роботи, Машини та Зброя" and g.get("display") == "Роботи та машини" for g in shipped["groups"]))

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
CNT = "document.getElementById('cat-count').textContent"
H1T = "document.querySelector('.cat-h1').textContent"
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
        for fname, title, n_expect, sel_val in (("catalog.html", "Каталог", TOTAL, ""), ("group-tovary-dlia-tvorchosti.html", "Товари для творчості", N_TV, ""), (f"category-{bs.slugify('Розмальовки')}.html", "Розмальовки", 16, "Розмальовки")):
            pg.goto(f"{base}/{fname}", wait_until="load")
            pg.wait_for_selector("#cat-js .card")
            pg.wait_for_timeout(300)
            h1b, ctl, sb, grid_, sel = (pg.evaluate(R + f"('{s}')") for s in (".cat-h1", "#cat-controls", ".sb", "#cat-js", "#cf-cat"))
            chk(f"{w}px {fname}: назва «{title}» окремим рядком, фільтри ПІД нею; {pg.evaluate(CNT)}", pg.evaluate(H1T).strip().startswith(title) and h1b["b"] <= ctl["t"] + 2 and f"Знайдено: {n_expect}" in pg.evaluate(CNT))
            if wide:
                chk(f"{w}px {fname}: сайдбар зліва ({sb['l']}–{sb['r']} | сітка від {grid_['l']}), список категорії у фільтрах схований ({sel['d']})", sb["r"] <= grid_["l"] and sel["d"] == "none")
            else:
                og = pg.evaluate("[...document.querySelectorAll('#cf-cat optgroup')].map(g=>g.label)")
                cur = pg.evaluate("document.querySelector('#cf-cat').value")
                chk(f"{w}px {fname}: сайдбару нема, список категорій згруповано ({og}), вибрана підкатегорія відповідає сторінці ({cur!r})", sb["d"] == "none" and og[:2] == ["Товари для творчості", "Транспорт"] and cur == sel_val)
        # ── поведінка: зі сторінки категорії обираємо іншу групу/підкатегорію без перезавантаження, адреса й назва оновлюються, «Назад» повертає ──
        pg.goto(f"{base}/category-{bs.slugify('Розмальовки')}.html", wait_until="load")
        pg.wait_for_selector("#cat-js .card")
        pg.wait_for_timeout(300)
        if wide:
            pg.click('.sb-gh[data-g="1"]')
            pg.wait_for_timeout(300)
            chk(f"{w}px: зі сторінки «Розмальовки» клік по групі «Транспорт»: на місці — {pg.evaluate(CNT)}, назва → «{pg.evaluate(H1T)}», адреса → {pg.url.split('/')[-1]}, розкрита лише вона",
                "Знайдено: 31" in pg.evaluate(CNT) and pg.evaluate(H1T) == "Транспорт" and pg.url.endswith("group-transport.html")
                and pg.evaluate("[...document.querySelectorAll('.sb-grp.open')].map(d=>d.getAttribute('data-g'))") == ["1"])
            pg.click('.sb-sub[data-sub="Самокати"]')
            pg.wait_for_timeout(300)
            chk(f"{w}px: клік по підкатегорії «Самокати»: {pg.evaluate(CNT)}, назва «{pg.evaluate(H1T)}», адреса {pg.url.split('/')[-1]}", "Знайдено: 10" in pg.evaluate(CNT) and pg.evaluate(H1T) == "Самокати" and "category-" in pg.url)
            pg.go_back()
            pg.wait_for_timeout(400)
            chk(f"{w}px: «Назад» повертає до групи «Транспорт» ({pg.evaluate(CNT)}, {pg.url.split('/')[-1]})", "Знайдено: 31" in pg.evaluate(CNT) and pg.url.endswith("group-transport.html"))
            pg.click('.sb-i.all')
            pg.wait_for_timeout(300)
            chk(f"{w}px: «Усі товари» повертає весь каталог ({pg.evaluate(CNT)}), назва «Каталог», усі групи згорнуті", f"Знайдено: {TOTAL}" in pg.evaluate(CNT) and pg.evaluate(H1T).startswith("Каталог") and pg.evaluate("[...document.querySelectorAll('.sb-subs')].every(e=>e.hidden)"))
            pg.click('.sb-gh[data-g="0"]')
            pg.wait_for_timeout(300)
            gh = pg.evaluate("(()=>{const e=document.querySelector('.sb-gh.on'); const s=getComputedStyle(e); return [s.color, s.backgroundColor]})()")
            chk(f"{w}px: активна група видима — білий напис на акцентному фоні {gh} (а не на світлому)", gh == ["rgb(255, 255, 255)", "rgb(217, 119, 87)"])
        else:
            pg.select_option("#cf-cat", "Самокати")
            pg.wait_for_timeout(300)
            chk(f"{w}px: вибір «Самокати» зі списку: {pg.evaluate(CNT)}, назва «{pg.evaluate(H1T)}», адреса {pg.url.split('/')[-1]}", "Знайдено: 10" in pg.evaluate(CNT) and pg.evaluate(H1T) == "Самокати" and "category-" in pg.url)
        # прокрутка: фільтри (і на ПК сайдбар) лишаються на екрані
        pg.evaluate("window.scrollTo(0, 1200)")
        pg.wait_for_timeout(300)
        ctl2 = pg.evaluate(R + "('#cat-controls')")
        chk(f"{w}px: після прокрутки фільтри лишились під шапкою (top {ctl2['t']})", 60 <= ctl2["t"] <= 80)
        if wide:
            sb2 = pg.evaluate(R + "('.sb')")
            chk(f"{w}px: після прокрутки сайдбар лишився на екрані (top {sb2['t']})", 0 < sb2["t"] < 200)
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
