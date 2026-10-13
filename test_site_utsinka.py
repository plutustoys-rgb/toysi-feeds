#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_utsinka.py — 13.10.2026 (власник/SMM): кнопка героя «Уцінка» → /utsinka.html. На сторінку збираються УСІ придатні уцінені товари (категорія «Уцінка/Уценка» або «Уцінка.» в назві):
у наявності, з фото, з ВІДОМИМ внеском ≥ 0 (збиткові не показуємо), не репліки зброї; сортування ціна зростаюче; пагінація як у каталозі (24); над сіткою помітний блок про дефект/комплектацію + лінк на «Повернення»,
без тверджень про відмову в поверненні (юридичний зміст — лише з дозволу власника); бейдж «Уцінка» на картці. Без активних уцінених — сторінки й кнопки нема (в героєві «Усі іграшки»)."""
import http.server
import os
import re
import shutil
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


IMG = "data:image/gif;base64,R0lGODlhAQABAAAAACw="
_n = [0]


def P(name, price=40, contrib=5.0, stock=3, cat="Уцінка", photo=IMG):
    _n[0] += 1
    return {"id": str(5000 + _n[0]), "name": name, "category": cat, "price": price, "contribution": contrib, "stock": stock, "photo": photo}


good = [P(f"Уцінка. Іграшка «Тест{i}» дефект", price=300 - i * 5, contrib=3 + i) for i in range(30)]
good.append(P("Конструктор в уцінці", price=77, cat="Уценка"))                  # за категорією (рос. написання)
bad = {
    "loss": P("Уцінка. Збиткова", contrib=-0.01),
    "unknown": P("Уцінка. Невідома собівартість", contrib=None),
    "oos": P("Уцінка. Немає", stock=0),
    "nofoto": P("Уцінка. Без фото", photo=""),
    "weapon": P("Уцінка. Пістолет іграшковий, стріляє кульками", contrib=50),
}
normal = [P(f"Звичайна іграшка «Кат{i}»", price=200 + i, contrib=80, cat=f"Звичайна{i}") for i in range(30)]
prods = good + list(bad.values()) + normal
pick = bs.utsinka_pick(prods)
ids = {p["id"] for p in pick}
chk(f"відбір: усі придатні ({len(good)}), жодної з відсіяних (збиткова, невідома собівартість, нема залишку/фото, зброя), звичайні товари не потрапляють", ids == {p["id"] for p in good} and not (ids & {b["id"] for b in bad.values()}))
chk("сортування: ціна зростаюче", [p["price"] for p in pick] == sorted(p["price"] for p in pick))
chk("внесок рівно 0 — придатний (поріг ≥ 0)", len(bs.utsinka_pick([P("Уцінка. Нуль", contrib=0.0)])) == 1)

cats = {}
for p in prods:
    cats.setdefault(p["category"], []).append(p)
cat_list = sorted(cats, key=lambda c: -len(cats[c]))
cat_slug = {c: f"c{i}" for i, c in enumerate(cat_list)}
root = Path(tempfile.mkdtemp())
os.environ["SITE_PICK_DATE"] = "2026-10-13"
bs.OUT = str(root)
written = bs.write_utsinka(prods, cat_list, cat_slug)
chk(f"записано utsinka.html + пагінація (31 товар → 2 сторінки по {bs.PER_PAGE}): {sorted(written)}", written == {"utsinka.html", "utsinka_2.html"})
h1 = (root / "utsinka.html").read_text(encoding="utf-8")
h2 = (root / "utsinka_2.html").read_text(encoding="utf-8")
chk("H1 «Уцінені іграшки», title/description без Нової Пошти, canonical = utsinka.html", '<h1 class="page">Уцінені іграшки</h1>' in h1 and "Уцінені іграшки — знижені ціни | PlutusToys" in h1 and "Пошт" not in re.search(r'<meta name="description" content="([^"]*)"', h1).group(1) and 'rel="canonical" href="https://plutustoys.com.ua/utsinka.html"' in h1)
chk("блок над сіткою: рівно текст SMM + лінк «Умови повернення» → returns.html", 'Уцінений товар:</b> має дефект або неповну комплектацію — це вказано в назві/описі. Перед покупкою прочитайте опис. <a href="returns.html">Умови повернення</a>' in h1)
chk("блок над сіткою є й на сторінці 2", "md-notice" in h2 and "Сторінка 2 з 2" in h2)
chk("у блоці НЕМАЄ тверджень про відмову в поверненні («не є підставою», «не підлягає»)", not re.search(r"не є підстав|не підлягає|без права", h1, re.I))
chk("перша сторінка — 24 картки, друга — решта 7; у кожної бейдж «Уцінка»", len(re.findall(r'<a class="card"', h1)) == 24 and len(re.findall(r'<a class="card"', h2)) == 7 and h1.count('class="md-badge">Уцінка</span>') == 24)
chk("без категорійних чипів і панелі сорт/фільтр каталогу; крихти Головна › Уцінені іграшки; rel=next/prev", 'class="chips"' not in h1 and "cat-controls" not in h1 and "catalog-controls" not in h1 and '"Уцінені іграшки"' in h1 and 'rel="next" href="utsinka_2.html"' in h1 and 'rel="prev" href="utsinka.html"' in h2)
chk("першою на сторінці — найдешевша", pick[0]["name"] in h1 and h1.index(pick[0]["name"]) < h1.index(pick[-1]["name"]) if pick[-1]["name"] in h1 else True)
tile_n = bs.tile(normal[0])
chk("бейдж «Уцінка» на звичайних товарах не з’являється", "md-badge" not in tile_n and "md-badge" in bs.tile(good[0]))
css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")


def lum(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


pairs = {"бейдж": ("#7a3a00", "#efe6dc"), "блок": ("#6b3300", "#fff1de"), "лінк у блоці": ("#8a3b00", "#fff1de")}
chk(f"контраст бейджа й блоку ≥ 4.5:1 {({k: round(ratio(*v), 1) for k, v in pairs.items()})}", all(ratio(*v) >= 4.5 for v in pairs.values()) and all(c in css for v in pairs.values() for c in v))

# немає придатних — нічого не пишемо, у героєві «Усі іграшки»
root2 = Path(tempfile.mkdtemp())
bs.OUT = str(root2)
chk("без придатних уцінених сторінку не пишемо", bs.write_utsinka(normal, cat_list, cat_slug) == set() and not (root2 / "utsinka.html").exists())

# головна: кнопка героя
allp = prods
cats_h = {}
for p in allp:
    cats_h.setdefault(p["category"], []).append(p)
cl = sorted(cats_h, key=lambda c: -len(cats_h[c]))
cs = {c: f"c{i}" for i, c in enumerate(cl)}
bs.OUT = str(root)
bs.write_home(allp, cats_h, cl, cs)
home = (root / "index.html").read_text(encoding="utf-8")
chk("герой: кнопка «Уцінка» → utsinka.html + «Усі іграшки»; «Нехай Плутус вибере» нема", '<div class="act"><a class="btn" href="utsinka.html">Уцінка</a><a class="hero-all" href="catalog.html">Усі іграшки</a></div>' in home and "Плутус вибере" not in home)
bs.OUT = str(root2)
cn = {}
for p in normal:
    cn.setdefault(p["category"], []).append(p)
cln = sorted(cn, key=lambda c: -len(cn[c]))
bs.write_home(normal, cn, cln, {c: f"c{i}" for i, c in enumerate(cln)})
home2 = (root2 / "index.html").read_text(encoding="utf-8")
chk("герой без уцінених: лише кнопка «Усі іграшки» (посилання на неіснуючу сторінку нема)", '<div class="act"><a class="btn" href="catalog.html">Усі іграшки</a></div>' in home2 and "utsinka" not in home2)

# Chromium
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    chk("playwright доступний", False)
    sync_playwright = None
if sync_playwright:
    shutil.copytree(HERE / "site" / "assets", root / "assets")
    os.chdir(root)

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        for w in (1280, 390):
            pg = br.new_page(viewport={"width": w, "height": 800})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.route("**/googletagmanager.com/**", lambda r: r.abort())
            pg.goto(f"{base}/index.html", wait_until="load")
            pg.wait_for_timeout(500)
            pg.click(".hero2 .btn")
            pg.wait_for_url(re.compile(r"/utsinka\.html$"), timeout=5000)
            pg.wait_for_timeout(300)
            info = pg.evaluate("(()=>{const n=document.querySelector('.md-notice'); const r=n.getBoundingClientRect(); const b=document.querySelector('.card .md-badge'); const br=b.getBoundingClientRect(); return [r.width>200, r.top<500, getComputedStyle(n).display, br.width>20 && br.height>10, document.documentElement.scrollWidth<=window.innerWidth, document.querySelectorAll('.grid .card').length]})()")
            chk(f"{w}px: клік по кнопці героя відкриває /utsinka.html; блок видно у першому екрані, бейдж видно, горизонтального скролу нема, 24 картки {info}", info[0] and info[1] and info[2] != "none" and info[3] and info[4] and info[5] == 24 and errs == [])
            pg.close()
        br.close()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
