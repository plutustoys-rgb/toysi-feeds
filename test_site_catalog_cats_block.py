#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_catalog_cats_block.py — 22.10.2026 (SEO: Googlebot робить 9 782 запити за 14 діб на /catalog.html і лише 0,1 % на /category-; Консультант схвалив варіант Б: блок категорій унизу catalog.html, футер і головну не чіпати).
Інваріанти:
(1) /catalog.html (стор. 1) має блок «Категорії товарів (N)» з видимими текстовими посиланнями на ВСІ категорії навігації (групи + «Окремі розділи»), без прихованої «Уцінки»;
(2) набір посилань у блоці = набору посилань хаба /categories.html (одне джерело: _cat_hub_sections), кожне веде на існуючий файл;
(3) блока НЕМАЄ на сторінках пагінації /catalog_2.html, на головній, у категоріях, групах і футері (власник 15.10 і 19.10: головну й футер не чіпаємо; футер = хаб, а не перелік);
(4) хаб /categories.html після рефакторингу — той самий (заголовки груп, «Окремі розділи», лічильники)."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
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


LAYOUT = {"Творчість": ("Картини", "Розмальовки", "Пісок"), "Транспорт": ("Самокати", "Велосипеди"), "Уцінка": ("Уцінені товари",)}
catalog, pid = {}, 1
for g, subs in LAYOUT.items():
    for k, sub in enumerate(subs):
        for j in range(30 + 6 * k):   # >PER_PAGE товарів сумарно → є сторінки пагінації catalog_2.html
            catalog[str(pid)] = item(pid, f"{sub} модель {pid}", sub, 200 + (pid * 13) % 300)
            pid += 1
for j in range(7):
    catalog[str(pid)] = item(pid, f"Сирота {pid}", "Сирота", 300 + j)
    pid += 1
for j in range(6):   # категорія з назвою, що потребує екранування
    catalog[str(pid)] = item(pid, f"Спецсимволи {pid}", 'Сирота <b>&"x', 100 + j)
    pid += 1
for j in range(6):   # NOINDEX_CATEGORIES (власник): не має потрапити ні в хаб, ні в блок
    catalog[str(pid)] = item(pid, f"Азартна {pid}", "Азартні", 100 + j)
    pid += 1
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
gj = Path(tempfile.mkdtemp()) / "category_groups.json"
gj.write_text(json.dumps({"groups": [
    {"name": "Творчість", "display": "Товари для творчості", "subs": ["Картини", "Розмальовки", "Пісок"]},
    {"name": "Транспорт", "subs": ["Самокати", "Велосипеди"]},
    {"name": "Уцінка", "subs": ["Уцінені товари"], "hide": True}]}, ensure_ascii=False), encoding="utf-8")
bs.CATEGORY_GROUPS_FILE = str(gj)
bs.GROUP_SUB_MIN = 1
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()

cat = (out / "catalog.html").read_text(encoding="utf-8")
m = re.search(r'<section class="chub catblock"[^>]*>(.*?)</section>\s*</div></div>', cat, re.S)
blk = m.group(0) if m else ""
chk("catalog.html: блок «Категорії товарів» є, унизу сторінки (перед закриттям колонки каталогу)", bool(m) and cat.index('class="chub catblock"') > cat.index('id="cat-static"'))
links = re.findall(r'href="(category-[^"]+\.html)"', blk)
names = re.findall(r'<li><a href="category-[^"]+\.html">([^<]+)</a>', blk)
chk(f"у блоці всі категорії, що є в хабі (групи + «Окремі розділи»): {names}", sorted(names) == sorted(["Картини", "Розмальовки", "Пісок", "Самокати", "Велосипеди", "Уцінені товари", "Сирота", 'Сирота &lt;b&gt;&amp;&quot;x']))
head = re.search(r'<h2 class="catblock-h">Категорії товарів \((\d+)\)</h2>', blk)
chk("заголовок містить кількість категорій і вона збігається з числом посилань", bool(head) and int(head.group(1)) == len(links) == 8)
chk("кожне посилання блоку веде на існуючий файл", all((out / l).exists() for l in links) and len(set(links)) == len(links))
chk("є заголовки груп і «Окремі розділи» (Сирота)", "Товари для творчості" in blk and "Транспорт" in blk and "Окремі розділи" in blk)
hub = (out / "categories.html").read_text(encoding="utf-8")
hub_links = re.findall(r'<li><a href="(category-[^"]+\.html)">', hub)
chk("набір посилань блоку = набору посилань хаба categories.html (одне джерело)", sorted(links) == sorted(hub_links))
chk("хаб без змін по суті: H1, «8 категорій у 3 розділах», групи й лічильники", "<h1 class=\"page\">Усі категорії товарів</h1>" in hub and "8 категорій у 3 розділах" in hub
    and re.search(r'<li><a href="category-[^"]+\.html">Картини</a> <span>\d+</span></li>', hub) is not None)

chk("прихована за NOINDEX_CATEGORIES категорія «Азартні» не потрапила ні в блок, ні в хаб; назва зі спецсимволами екранована (нема сирого <b>)", "Азартні" not in blk and "Азартні" not in hub and "<b>&" not in blk and "Сирота &lt;b&gt;" in blk)
chk("catalog_2.html (пагінація) БЕЗ блоку", (out / "catalog_2.html").exists() and "catblock" not in (out / "catalog_2.html").read_text(encoding="utf-8"))
home = (out / "index.html").read_text(encoding="utf-8")
chk("головна без блоку (власник 15.10)", "catblock" not in home and "chub" not in home)
foot = re.search(r"<footer.*?</footer>", cat, re.S).group(0)
chk("футер: лише хаб «Усі категорії товарів», переліку категорій нема (власник 19.10)", 'href="categories.html"' in foot and "category-" not in foot)
others = [p for p in list(out.glob("category-*.html")) + list(out.glob("group-*.html")) if "catblock" in p.read_text(encoding="utf-8")]
chk("у сторінках категорій і груп блоку нема", others == [])

# (5) відступи в реальному браузері: на вузькому екрані заголовок і список не торкаються краю (аудит: .catblock перебивав бокові відступи .chub)
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
if sync_playwright:
    import http.server
    import shutil
    import socketserver
    import threading
    shutil.copytree(HERE / "site" / "assets", out / "assets", dirs_exist_ok=True)
    os.chdir(out)

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        for w in (390, 1280):
            pg = br.new_context(viewport={"width": w, "height": 900}).new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.route("**/googletagmanager.com/**", lambda r: r.abort())
            pg.goto(f"{base}/catalog.html", wait_until="load")
            pg.wait_for_timeout(400)
            r = pg.evaluate("""(()=>{const b=document.querySelector('.catblock'); if(!b) return null; const h=b.querySelector('.catblock-h').getBoundingClientRect(), l=b.querySelector('.chub-l a').getBoundingClientRect();
                return {h:h.left, l:l.left, w:innerWidth, overflow:document.documentElement.scrollWidth>innerWidth}})()""")
            chk(f"{w}px: заголовок і список блоку відступають від краю екрана ≥ 12px і без горизонтального скролу ({r})", r is not None and r["h"] >= 12 and r["l"] >= 12 and not r["overflow"] and errs == [])
            pg.context.close()
        br.close()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
