#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_search_overlay.py — 13.10.2026 (власник, тестувальник #16): з пошуку немає виходу, «Назад» виводить із САЙТУ. Реальний Chromium (1280 і 390):
відкриття пошуку кладе ОДИН запис в історію; «Назад», Escape, клік по фону і «Готово» закривають оверлей, лишаючи на сторінці й не лишаючи зайвих записів; перехід на результат не дає подвійного «Назад»;
порожній стан не порожній (підказка, популярні категорії, «Усі іграшки»)."""
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
prods = [{"id": str(9300 + i), "name": f"Конструктор «Лінія{i}»" if i % 2 else f"Лялька «Серія{i}»", "category": f"Категорія{i % 4}", "price": 200 + i, "contribution": 120, "stock": 5, "photo": IMG, "desc": ""} for i in range(12)]
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
(out / "index.json").write_text(json.dumps([{"id": p["id"], "n": p["name"], "pr": p["price"], "p": IMG, "c": p["category"], "s": 1} for p in prods], ensure_ascii=False), encoding="utf-8")
shutil.copytree(HERE / "site" / "assets", out / "assets")
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

        def fresh():
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.route("**/googletagmanager.com/**", lambda r: r.abort())
            pg.goto("about:blank")
            pg.goto(f"{BASE}/index.html", wait_until="load")
            pg.wait_for_timeout(400)
            return pg, errs

        # 1. відкриття: один запис в історії; порожній стан не порожній
        pg, errs = fresh()
        h0 = pg.evaluate("history.length")
        pg.click("#open-search")
        pg.wait_for_timeout(200)
        empty = pg.evaluate("(()=>{const r=document.querySelector('#search-overlay .results'); return [r.textContent.trim().length>20, r.querySelectorAll('.sr-pill').length, !!r.querySelector('.sr-all a[href=\"catalog.html\"]'), history.length]})()")
        chk(f"{w}px: відкриття пошуку — оверлей, ОДИН новий запис в історії, порожній стан: підказка + категорії + «Усі іграшки» {empty}", pg.evaluate(ON) and empty[0] and empty[1] >= 4 and empty[2] and empty[3] == h0 + 1)
        # 2. «Назад»
        pg.go_back()
        pg.wait_for_timeout(300)
        chk(f"{w}px: «Назад» закриває оверлей і ЛИШАЄ на сайті (не виходить на about:blank): {pg.url}", pg.url.endswith("/index.html") and not pg.evaluate(ON))
        # 3. Escape
        pg.click("#open-search")
        pg.wait_for_timeout(150)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)
        chk(f"{w}px: Escape закриває, сторінка та сама, зайвого запису нема (history.length як на початку: {pg.evaluate('history.length')} vs {h0})", not pg.evaluate(ON) and pg.url.endswith("/index.html") and pg.evaluate("history.length") <= h0 + 1)
        pg.go_back()
        pg.wait_for_timeout(300)
        chk(f"{w}px: після Escape один «Назад» веде геть з головної (зайвих записів пошук не лишив): {pg.url}", pg.url.startswith("about:blank"))
        # 4. клік по фону
        pg, errs = fresh()
        pg.click("#open-search")
        pg.wait_for_timeout(150)
        box = pg.evaluate("(()=>{const r=document.querySelector('#search-overlay .results').getBoundingClientRect(); return [r.left+r.width/2, r.bottom-30]})()")
        pg.mouse.click(box[0], box[1])
        pg.wait_for_timeout(300)
        chk(f"{w}px: клік по порожньому фону закриває оверлей", not pg.evaluate(ON) and pg.url.endswith("/index.html"))
        # 5. «Готово»
        pg.click("#open-search")
        pg.wait_for_timeout(150)
        pg.click("#close-search")
        pg.wait_for_timeout(300)
        chk(f"{w}px: «Готово» закриває без зайвих записів", not pg.evaluate(ON) and pg.evaluate("history.length") <= h0 + 1)
        # 6. пошук → результат → «Назад» повертає на ту саму сторінку одним кроком
        pg, errs = fresh()
        pg.click("#open-search")
        pg.fill("#search-overlay input", "лялька")
        pg.wait_for_timeout(700)
        n_res = pg.evaluate("document.querySelectorAll('#search-overlay .sr').length")
        chk(f"{w}px: пошук «лялька» дає результати {n_res}", n_res >= 3)
        pg.click("#search-overlay .sr")
        pg.wait_for_url("**/product-*.html", timeout=5000)
        chk(f"{w}px: клік по результату веде на картку товару: {pg.url}", "/product-" in pg.url)
        pg.go_back()
        pg.wait_for_timeout(500)
        chk(f"{w}px: «Назад» із картки повертає на головну ОДНИМ кроком (не на порожній запис пошуку): {pg.url}", pg.url.endswith("/index.html") and not pg.evaluate(ON))
        pg.go_back()
        pg.wait_for_timeout(300)
        chk(f"{w}px: ще один «Назад» — геть із сайту (подвійного «Назад» нема): {pg.url}", pg.url.startswith("about:blank"))
        # 7. стерли запит — знову порожній стан
        pg, errs = fresh()
        pg.click("#open-search")
        pg.fill("#search-overlay input", "лялька")
        pg.wait_for_timeout(500)
        pg.fill("#search-overlay input", "л")
        pg.dispatch_event("#search-overlay input", "input")
        pg.wait_for_timeout(500)
        chk(f"{w}px: запит < 2 літер повертає порожній стан (категорії), не порожній екран", pg.evaluate("document.querySelectorAll('#search-overlay .sr-pill').length") >= 4 and pg.evaluate("document.querySelectorAll('#search-overlay .sr').length") == 0)
        chk(f"{w}px: без помилок JS", errs == [])
        ctx.close()
        # 8. запит, набраний ПОКИ індекс вантажиться, не губиться: результати з'являються без додаткового вводу (раніше callback відкидався)
        ctx = br.new_context(viewport={"width": w, "height": 800}, reduced_motion="reduce")
        pg = ctx.new_page()
        held = []
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("**/index.json*", lambda r, _rq: held.append(r))
        pg.goto("about:blank")
        pg.goto(f"{BASE}/index.html", wait_until="load")
        pg.wait_for_timeout(400)
        pg.click("#open-search")
        pg.fill("#search-overlay input", "лялька")
        pg.wait_for_timeout(600)
        none_yet = pg.evaluate("document.querySelectorAll('#search-overlay .sr').length")
        for r in held:
            r.fulfill(status=200, content_type="application/json", body=(out / "index.json").read_text(encoding="utf-8"))
        pg.wait_for_timeout(800)
        got = pg.evaluate("document.querySelectorAll('#search-overlay .sr').length")
        chk(f"{w}px: запит набрано до завершення завантаження індексу — результати з'являються самі після завантаження ({none_yet} → {got})", none_yet == 0 and got >= 3)
        ctx.close()
        # 9. індекс не завантажився — повідомлення, не порожній екран
        ctx = br.new_context(viewport={"width": w, "height": 800}, reduced_motion="reduce")
        pg = ctx.new_page()
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("**/index.json*", lambda r, _rq: r.abort())
        pg.goto("about:blank")
        pg.goto(f"{BASE}/index.html", wait_until="load")
        pg.wait_for_timeout(400)
        pg.click("#open-search")
        pg.fill("#search-overlay input", "лялька")
        pg.wait_for_timeout(1000)
        chk(f"{w}px: збій завантаження індексу — повідомлення «Не вдалося завантажити пошук», не порожній екран", "Не вдалося завантажити пошук" in pg.evaluate("document.querySelector('#search-overlay .results').textContent"))
        ctx.close()
    br.close()

os.environ.pop("SITE_PICK_DATE", None)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
