#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_hero_pick.py — 13.10.2026 (SMM, рішення власника): кнопка героя «Нехай Плутус вибере» замість «Набори в подарунок» (дубль із чипом); рядок «Нова Пошта · оплата…» із героя прибрано.
Кнопка веде на ВИПАДКОВИЙ товар із сьогоднішнього пулу добірки (до 40 позицій, лише в наявності) на картку з ?pick=plutus; подія select_item «plutus_pick»; «Усі іграшки» без підкреслення (підкреслення на hover); на кнопці нема анімацій. Реальний Chromium 1280 і 390."""
import http.server
import json
import html as H
import os
import re
import shutil
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["SITE_GA4_ID"] = "G-TEST123456"
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


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    chk("playwright доступний", False)
    sys.exit(1)

W = ["Калина", "Мальви", "Барвінок", "Соняшник", "Лелека", "Журавель", "Ромашка", "Волошка", "Тополя", "Берегиня", "Веселка", "Дзвіночок", "Рушничок", "Колосок", "Мрія"]
prods = []
for i in range(60):
    prods.append({"id": str(6000 + i), "name": f"Іграшка «Лінія{W[i % 15]}{i}»", "category": f"Кат{i}", "price": 200 + i * 7, "contribution": 120, "stock": 5, "photo": "data:image/gif;base64,R0lGODlhAQABAAAAACw="})
prods.append({"id": "6990", "name": "Іграшка «Немає в наявності»", "category": "Кат-нема", "price": 300, "contribution": 120, "stock": 0, "photo": "data:image/gif;base64,R0lGODlhAQABAAAAACw="})
prods.append({"id": "6991", "name": "Уцінка. Іграшка «Уцінена»", "category": "Кат-уц", "price": 300, "contribution": 120, "stock": 5, "photo": "data:image/gif;base64,R0lGODlhAQABAAAAACw="})
cats = {}
for p in prods:
    cats.setdefault(p["category"], []).append(p)
cat_list = sorted(cats, key=lambda c: -len(cats[c]))
cat_slug = {c: f"c{i}" for i, c in enumerate(cat_list)}
root = Path(tempfile.mkdtemp())
os.environ["SITE_PICK_DATE"] = "2026-10-13"
bs.OUT = str(root)
bs.write_home(prods, cats, cat_list, cat_slug)
shutil.copytree(HERE / "site" / "assets", root / "assets")
html = (root / "index.html").read_text(encoding="utf-8")
m = re.search(r'<a class="btn" id="plutus-pick" href="catalog.html" data-pool="([^"]*)">Нехай Плутус вибере</a><a class="hero-all" href="catalog.html">Усі іграшки</a>', html)
chk("герой: кнопка «Нехай Плутус вибере» (#plutus-pick, пул у data-pool), під нею «Усі іграшки»", m is not None)
pool = json.loads(H.unescape(m.group(1))) if m else []
ids = {p[1] for p in pool}
chk(f"пул: 40 позицій (не 10), усі в наявності, без «Уцінки», без повторів {len(pool)}", len(pool) == 40 and "6990" not in ids and "6991" not in ids and len(ids) == 40 and all(p[0] == f"product-{p[1]}.html" for p in pool))
chk("«Набори в подарунок» і рядка «Нова Пошта · оплата» у героєві нема; чипи на місці", "Набори в подарунок</a>" not in html.split('<div class="hero2">')[1].split("</div></div>")[0] and "Нова Пошта ·" not in html and "оплата при отриманні" not in html)
css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
chk("CSS: «Усі іграшки» без підкреслення за замовчуванням, підкреслення на hover/focus; анімацій нема", re.search(r"\.hero2 \.hero-all\{[^}]*text-decoration:none", css) and ".hero2 .hero-all:hover" in css and "animation" not in css)
# без пулу — кнопка веде в каталог без data-pool
chk("без придатних товарів кнопка лишається посиланням на каталог (без data-pool)", bs.plutus_pool_attr([]) == "")

os.chdir(root)


class Hd(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), Hd)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
EV = "(window.dataLayer||[]).map(a=>Array.from(a)).filter(a=>a[1]==='select_item').map(a=>[a[2].item_list_id,a[2].item_list_name,a[2].items[0].item_id,a[2].items[0].item_name])"
with sync_playwright() as p:
    br = p.chromium.launch()
    for w in (1280, 390):
        pg = br.new_page(viewport={"width": w, "height": 800})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.goto(f"{BASE}/index.html", wait_until="load")
        pg.wait_for_timeout(700)
        lay = pg.evaluate("(()=>{const b=document.getElementById('plutus-pick').getBoundingClientRect(); const a=document.querySelector('.hero2 .hero-all'); const r=a.getBoundingClientRect(); return [Math.round(b.width), Math.round(b.bottom), Math.round(r.top), getComputedStyle(a).textDecorationLine, document.documentElement.scrollWidth<=window.innerWidth, getComputedStyle(document.getElementById('plutus-pick')).animationName]})()")
        chk(f"{w}px: кнопка видима, «Усі іграшки» під нею, без підкреслення, без горизонтального скролу, без анімації {lay}", lay[0] > 150 and lay[2] >= lay[1] - 2 and lay[3] == "none" and lay[4] and lay[5] == "none")
        pg.hover(".hero2 .hero-all")
        chk(f"{w}px: «Усі іграшки» на hover підкреслена", pg.evaluate("getComputedStyle(document.querySelector('.hero2 .hero-all')).textDecorationLine") == "underline")
        seen = set()
        for _ in range(6):
            pg.goto(f"{BASE}/index.html", wait_until="load")
            pg.evaluate("window.dataLayer=window.dataLayer||[]")
            pg.click("#plutus-pick")
            pg.wait_for_url(re.compile(r"product-\d+\.html\?pick=plutus$"), timeout=5000)
            u = pg.url
            mm = re.search(r"product-(\d+)\.html\?pick=plutus$", u)
            seen.add(mm.group(1) if mm else None)
        chk(f"{w}px: клік веде на product-<id>.html?pick=plutus із пулу; за 6 кліків різні товари {sorted(seen)}", None not in seen and seen <= ids and len(seen) >= 2)
        pg.goto(f"{BASE}/index.html", wait_until="load")
        pg.wait_for_timeout(300)
        pg.evaluate("document.addEventListener('click', e => { if (e.target.closest('#plutus-pick')) { window.__nav = true; } }, true)")
        pg.evaluate("window.dataLayer=window.dataLayer||[]")
        pg.click("#plutus-pick")
        pg.wait_for_timeout(50)
        ev = []
        try:
            ev = pg.evaluate(EV)
        except Exception:
            pass
        chk(f"{w}px: подія select_item «plutus_pick» (item_list_id/name, id і назва товару з пулу) {ev}", (ev and ev[0][0] == "plutus_pick" and ev[0][1] == "plutus_pick" and ev[0][2] in ids and ev[0][3]))
        chk(f"{w}px: без помилок JS", errs == [])
        pg.close()
    # без JS: кнопка — звичайне посилання на каталог
    ctx = br.new_context(java_script_enabled=False)
    pg = ctx.new_page()
    pg.goto(f"{BASE}/index.html")
    chk("без JS кнопка веде в catalog.html", pg.get_attribute("#plutus-pick", "href") == "catalog.html")
    ctx.close()
    br.close()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
