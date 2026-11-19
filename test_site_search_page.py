#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_search_page.py — 18.11.2026 (власник: «набираю пошуковий запит, натискаю Enter — нічого не відбувається… кнопка Готово теж не допомагає;
зі списку перехід є, але треба бачити і загальну картину»).
Enter у пошуку і «Готово» з набраним запитом (≥2 символи) ведуть на search.html?q=… — сторінку з УСІМА результатами (сітка карток, «Показати ще», сортування);
порожній запит / 1 символ — Enter нічого не робить, «Готово» закриває оверлей (як було). Сторінка noindex, Disallow у robots.txt, без JS-помилок; 1280 і 390 px.
Реальний браузер (playwright)."""
import http.server
import json
import os
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


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    chk("playwright доступний", False)
    sys.exit(1)

IMG = "data:image/gif;base64,R0lGODlhAQABAAAAACw="
prods = []
for i in range(60):   # 60 ляльок (> 48: перевіряємо «Показати ще») + 6 конструкторів
    prods.append({"id": str(9300 + i), "name": f"Лялька «Серія{i}»", "category": "Ляльки", "price": 100 + i * 3, "contribution": 120, "stock": 5, "photo": IMG})
for i in range(6):
    prods.append({"id": str(9400 + i), "name": f"Конструктор «Лінія{i}»", "category": "Конструктори", "price": 500 + i, "contribution": 120, "stock": 5, "photo": IMG})
cats = {}
for p in prods:
    cats.setdefault(p["category"], []).append(p)
cl = sorted(cats, key=lambda c: -len(cats[c]))
cs = {c: f"c{i}" for i, c in enumerate(cl)}
bs.SEARCH_POPULAR[:] = [(c, f"category-{cs[c]}.html") for c in cl][:8]
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
os.environ["SITE_PICK_DATE"] = "2026-03-05"
bs.write_home(prods, cats, cl, cs)
bs.write_search_page()
idx = [{"id": p["id"], "n": p["name"], "pr": p["price"], "p": IMG, "c": p["category"], "s": 1} for p in prods]
(out / "index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
(out / "index-t.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
(out / "index-rt.json").write_text("[]", encoding="utf-8")
shutil.copytree(HERE / "site" / "assets", out / "assets")

sp_html = (out / "search.html").read_text(encoding="utf-8")
chk("search.html: noindex, форма GET на search.html з полем q, контейнери результатів, не в sitemap-статиці", 'content="noindex' in sp_html and 'action="search.html"' in sp_html and 'name="q"' in sp_html
    and 'id="sp-grid"' in sp_html and 'id="sp-more"' in sp_html and 'class="grid"' in sp_html)
robots_dir = Path(tempfile.mkdtemp())
bs.OUT = str(robots_dir)
bs.write_robots()
chk("robots.txt: Disallow: /search.html (нескінченні варіанти запитів)", "Disallow: /search.html" in (robots_dir / "robots.txt").read_text(encoding="utf-8"))
bs.OUT = str(out)
os.chdir(out)


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
ON = "document.getElementById('search-overlay').classList.contains('on')"

with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (1280, 390):
        ctx = br.new_context(viewport={"width": w, "height": 800}, reduced_motion="reduce")

        def fresh(url="index.html"):
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.route("**/googletagmanager.com/**", lambda r: r.abort())
            pg.goto("about:blank")
            pg.goto(f"{BASE}/{url}", wait_until="load")
            pg.wait_for_timeout(500)
            return pg, errs

        # 1. Enter з набраним запитом → search.html?q=…, усі збіги
        pg, errs = fresh()
        pg.click("#open-search")
        pg.fill("#search-overlay input", "лялька")
        pg.wait_for_timeout(500)
        pg.press("#search-overlay input", "Enter")
        pg.wait_for_url("**/search.html?q=*", timeout=5000)
        pg.wait_for_timeout(900)
        n = pg.evaluate("document.querySelectorAll('#sp-grid .card').length")
        meta = pg.evaluate("document.getElementById('sp-meta').textContent")
        h1 = pg.evaluate("document.getElementById('sp-h1').textContent")
        chk(f"{w}px: Enter у пошуку → search.html: URL {pg.url.split('/')[-1]}, H1 «{h1}», знайдено 60, показано {n} (≤48), {meta[:60]!r}", "search.html?q=" in pg.url and h1 == "Пошук: «лялька»" and n == 48 and "Знайдено 60" in meta)
        geo = pg.evaluate("(()=>{const r=e=>document.querySelector(e).getBoundingClientRect(); const wr=document.querySelector('.wrap').getBoundingClientRect(); return [r('#sp-q').width, r('.sp-form .btn').width, r('#sp-grid').width, wr.width]})()")
        chk(f"{w}px: розкладка сторінки результатів (скарга власника 19.11): поле вводу ширше за кнопку, кнопка компактна, сітка на всю ширину контенту (не вузька колонка): {[round(x) for x in geo]}",
            geo[0] > geo[1] * 1.8 and geo[1] < 220 and geo[2] >= geo[3] - 40)
        chk(f"{w}px: у полі сторінки результатів — той самий запит; title містить запит", pg.evaluate("document.getElementById('sp-q').value") == "лялька" and "лялька" in pg.title())
        pg.click("#sp-more")
        pg.wait_for_timeout(300)
        n2 = pg.evaluate("document.querySelectorAll('#sp-grid .card').length")
        chk(f"{w}px: «Показати ще» дає всі 60 ({n2}) і ховає кнопку", n2 == 60 and pg.evaluate("document.getElementById('sp-more-wrap').hidden"))
        pg.click("[data-sp-sort=cheap]")
        pg.wait_for_timeout(300)
        prices = pg.evaluate("Array.from(document.querySelectorAll('#sp-grid .card .pr')).map(e=>parseInt(e.textContent))")
        chk(f"{w}px: сортування «Дешевші»: ціни за зростанням, перша картка {prices[:3]}", prices == sorted(prices) and prices[0] == 100)
        pg.click("[data-sp-sort=dear]")
        pg.wait_for_timeout(300)
        prices = pg.evaluate("Array.from(document.querySelectorAll('#sp-grid .card .pr')).map(e=>parseInt(e.textContent))")
        chk(f"{w}px: «Дорожчі»: ціни за спаданням", prices == sorted(prices, reverse=True))
        pg.click("#sp-grid .card")
        pg.wait_for_url("**/product-*.html", timeout=5000)
        chk(f"{w}px: клік по картці веде на товар: {pg.url.split('/')[-1]}", "/product-" in pg.url)
        pg.go_back()
        pg.wait_for_timeout(500)
        chk(f"{w}px: «Назад» із товару повертає на результати", "search.html?q=" in pg.url)
        pg.go_back()
        pg.wait_for_timeout(500)
        chk(f"{w}px: «Назад» із результатів повертає на головну ОДНИМ кроком (оверлей не лишає зайвого запису): {pg.url.split('/')[-1]}", pg.url.endswith("/index.html"))
        pg.go_back()
        pg.wait_for_timeout(400)
        chk(f"{w}px: наступний «Назад» виходить із сайту (about:blank), а не на ще один index.html: {pg.url[:11]}", pg.url.startswith("about:blank"))
        chk(f"{w}px: без JS-помилок на шляху пошуку", not errs)
        pg.close()

        # 2. «Готово» з набраним запитом → результати; порожнє «Готово» закриває
        pg, errs = fresh()
        pg.click("#open-search")
        pg.fill("#search-overlay input", "конструктор")
        pg.wait_for_timeout(400)
        pg.click("#close-search")
        pg.wait_for_url("**/search.html?q=*", timeout=5000)
        pg.wait_for_timeout(700)
        n_done = pg.evaluate("document.querySelectorAll('#sp-grid .card').length")
        chk(f"{w}px: «Готово» з набраним запитом → сторінка результатів: 6 конструкторів ({n_done})", "search.html?q=" in pg.url and n_done == 6)
        pg.close()
        pg, errs = fresh()
        pg.click("#open-search")
        pg.wait_for_timeout(150)
        pg.click("#close-search")
        pg.wait_for_timeout(300)
        chk(f"{w}px: «Готово» з порожнім полем лише закриває оверлей (як було)", not pg.evaluate(ON) and pg.url.endswith("/index.html"))
        # 3. Enter з 1 символом / порожній — нічого
        pg.click("#open-search")
        pg.fill("#search-overlay input", "л")
        pg.press("#search-overlay input", "Enter")
        pg.wait_for_timeout(400)
        chk(f"{w}px: Enter з 1 символом не переходить (лишаємось у оверлеї)", pg.url.endswith("/index.html") and pg.evaluate(ON))
        pg.close()

        # 4. пряме посилання, нічого не знайдено, <2 символів, спецсимволи
        pg, errs = fresh("search.html?q=" + "конструктор")
        chk(f"{w}px: пряма адреса ?q=конструктор: 6 результатів, H1 з запитом", pg.evaluate("document.querySelectorAll('#sp-grid .card').length") == 6 and "конструктор" in pg.evaluate("document.getElementById('sp-h1').textContent"))
        pg.close()
        pg, errs = fresh("search.html?q=" + "xyzнемає")
        chk(f"{w}px: нічого не знайдено — повідомлення з посиланням на каталог, без карток", "Нічого не знайдено" in pg.evaluate("document.getElementById('sp-meta').textContent") and pg.evaluate("document.querySelectorAll('#sp-grid .card').length") == 0
            and pg.evaluate("!!document.querySelector('#sp-meta a[href=\"catalog.html\"]')"))
        pg.close()
        pg, errs = fresh("search.html?q=a")
        chk(f"{w}px: ?q=a (1 символ) — підказка, без падіння", "щонайменше 2" in pg.evaluate("document.getElementById('sp-meta').textContent") and not errs)
        pg.close()
        pg, errs = fresh("search.html")
        chk(f"{w}px: без ?q — підказка, без падіння", "щонайменше 2" in pg.evaluate("document.getElementById('sp-meta').textContent") and not errs)
        pg.close()
        pg, errs = fresh("search.html?q=" + "%3Cscript%3Ealert(1)%3C%2Fscript%3E")
        chk(f"{w}px: ?q=<script> — екранується, скрипт не виконується (XSS)", pg.evaluate("document.querySelectorAll('#sp-meta script, #sp-h1 script').length") == 0 and not errs)
        pg.close()
        # 5. форма на сторінці результатів (Enter у її полі) — нова адреса
        pg, errs = fresh("search.html?q=" + "лялька")
        pg.fill("#sp-q", "конструктор")
        pg.press("#sp-q", "Enter")
        pg.wait_for_url("**/search.html?q=*", timeout=5000)
        pg.wait_for_timeout(700)
        chk(f"{w}px: форма на сторінці результатів: новий запит → 6 результатів", pg.evaluate("document.querySelectorAll('#sp-grid .card').length") == 6)
        pg.close()
        ctx.close()
    br.close()
print("\n" + ("ALL OK" if not F else f"FAIL: {len(F)}"))
sys.exit(1 if F else 0)
