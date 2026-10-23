#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_product_code_copy.py — 23.10.2026 (власник обрав варіант A: код товару під назвою, з кнопкою «Копіювати»).
Реальний браузер, картка товару на 390 і 1280 px:
(1) клік «Копіювати» кладе в буфер саме код (PT-…), кнопка на мить каже «Скопійовано» і повертається до «Копіювати»;
(2) без clipboard API працює запасний шлях (textarea + execCommand), а збій показує «Не вдалося», не ламаючи сторінку;
(3) клік по кнопці НЕ кладе товар у кошик і не спричиняє навігацію (кошик порожній, URL той самий);
(4) вигляд: рядок коду стоїть між назвою і ціною, одним рядком (висота ≤ 30 px), без горизонтального скролу, зона дотику кнопки ≥ 32 px;
(5) без помилок JS."""
import http.server
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

out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.LIQPAY_LIVE = True
prod = {"id": "9701", "name": "Картина по номерах «Святковий настрій» 40×50 см", "category": "Картини за номерами", "price": 468, "contribution": 100, "stock": 3, "photo": "", "desc": "<b>Бренд:</b> Rosa", "rt": 0, "noindex": False, "dup_of": ""}
bs.write_product(prod, related=None, cat_slug={}, similar=[])
shutil.copytree(HERE / "site" / "assets", out / "assets")
os.chdir(out)


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"

with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (390, 1280):
        ctx = br.new_context(viewport={"width": w, "height": 900})
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.goto(f"{BASE}/product-9701.html", wait_until="load")
        pg.wait_for_timeout(400)
        url0 = pg.url
        chk(f"{w}px: рядок коду є, код PT-9701, кнопка «Копіювати»", pg.evaluate("document.querySelector('.sku .sku-code').textContent") == "PT-9701" and pg.evaluate("document.querySelector('.sku-copy').textContent") == "Копіювати")
        pg.click(".sku-copy")
        pg.wait_for_timeout(250)
        clip = pg.evaluate("navigator.clipboard.readText()")
        chk(f"{w}px: у буфері саме код ({clip!r}), кнопка «Скопійовано»", clip == "PT-9701" and pg.evaluate("document.querySelector('.sku-copy').textContent") == "Скопійовано")
        pg.wait_for_timeout(2000)
        chk(f"{w}px: через ≈2 с кнопка знову «Копіювати»", pg.evaluate("document.querySelector('.sku-copy').textContent") == "Копіювати")
        chk(f"{w}px: клік не додав товар у кошик і не змінив сторінку", pg.evaluate("localStorage.getItem('pt_cart_v1')") in (None, "{}", "") and pg.url == url0)
        m = pg.evaluate("""(()=>{const s=document.querySelector('.sku'), h=document.querySelector('h1.prod').getBoundingClientRect(), p=document.querySelector('.price-row').getBoundingClientRect(), r=s.getBoundingClientRect(), b=document.querySelector('.sku-copy').getBoundingClientRect();
            return {h:Math.round(r.height), between:(r.top>=h.bottom-1 && r.bottom<=p.top+1), btnH:Math.round(b.height), overflow:document.documentElement.scrollWidth>innerWidth}})()""")
        chk(f"{w}px: рядок між назвою і ціною, висота {m['h']} px ≤ 30, зона кнопки {m['btnH']} px ≥ 32, без горизонтального скролу", m["between"] and m["h"] <= 30 and m["btnH"] >= 32 and not m["overflow"])
        chk(f"{w}px: без помилок JS", errs == [])
        ctx.close()

    # запасний шлях: без clipboard API, execCommand працює
    ctx = br.new_context(viewport={"width": 390, "height": 900})
    pg = ctx.new_page()
    pg.add_init_script("Object.defineProperty(navigator,'clipboard',{value:undefined}); window.__copied=null; document.execCommand=function(c){ if(c==='copy'){ var a=document.activeElement; window.__copied=(document.querySelector('textarea[readonly]')||{}).value||null; return true; } return false; };")
    pg.route("**/googletagmanager.com/**", lambda r: r.abort())
    pg.goto(f"{BASE}/product-9701.html", wait_until="load")
    pg.wait_for_timeout(300)
    pg.click(".sku-copy")
    pg.wait_for_timeout(250)
    chk("без clipboard API: запасний шлях скопіював код і кнопка «Скопійовано»", pg.evaluate("window.__copied") == "PT-9701" and pg.evaluate("document.querySelector('.sku-copy').textContent") == "Скопійовано")
    chk("запасний шлях не лишає textarea на сторінці", pg.evaluate("document.querySelectorAll('textarea[readonly]').length") == 0)
    ctx.close()

    # збій копіювання: «Не вдалося», сторінка жива
    ctx = br.new_context(viewport={"width": 390, "height": 900})
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.add_init_script("Object.defineProperty(navigator,'clipboard',{value:{writeText:function(){ return Promise.reject(new Error('denied')); }}}); document.execCommand=function(){ return false; };")
    pg.route("**/googletagmanager.com/**", lambda r: r.abort())
    pg.goto(f"{BASE}/product-9701.html", wait_until="load")
    pg.wait_for_timeout(300)
    pg.click(".sku-copy")
    pg.wait_for_timeout(300)
    chk("збій буфера і запасного шляху: кнопка «Не вдалося», без помилок JS, код на місці", pg.evaluate("document.querySelector('.sku-copy').textContent") == "Не вдалося" and errs == [] and pg.evaluate("document.querySelector('.sku .sku-code').textContent") == "PT-9701")
    ctx.close()
    br.close()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
