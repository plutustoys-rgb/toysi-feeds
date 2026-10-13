#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_card_default.py — 13.10.2026 (власник): оплата карткою — обраний спосіб за замовчуванням. Прод у бойовому режимі (LIQPAY_LIVE). Перевіряємо в Chromium ПЕРВИННИЙ рендер (шлях, яким раніше ніхто не йшов, тестувальник 13.10):
при завантаженні cart.html БЕЗ жодного кліку: обрано «Оплата карткою онлайн», доставка «орієнтовно 70–90 ₴ до 2 кг», підпис БЕЗ «з комісією за накладений платіж»; перемикання на «при отриманні» → 99–119 ₴ (кошик 468 ₴) і підпис із комісією; назад на картку — знову 70–90 ₴.
Окремо: без LIQPAY_LIVE (до ввімкнення оплати) за замовчуванням лишається накладений платіж; при кошику >3 000 ₴ — картка й так обрана."""
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

roots = {}
for live in (True, False):
    bs.LIQPAY_LIVE = live
    d = Path(tempfile.mkdtemp())
    bs.OUT = str(d)
    bs.write_cart([])
    shutil.copytree(HERE / "site" / "assets", d / "assets")
    roots[live] = d
html_live = (roots[True] / "cart.html").read_text(encoding="utf-8")
html_off = (roots[False] / "cart.html").read_text(encoding="utf-8")
chk("HTML live: картка ПЕРША в списку й checked, накладений другий і без checked", html_live.index('value="prepaid" checked') < html_live.index('value="cod"') and 'value="cod" checked' not in html_live and 'value="cod"> Оплата при отриманні' in html_live)
chk("HTML без LIQPAY_LIVE: накладений checked, картки нема (як було)", 'value="cod" checked' in html_off and 'value="prepaid"' not in html_off)


class Hd(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(root):
    os.chdir(root)
    srv = socketserver.TCPServer(("127.0.0.1", 0), Hd)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


BASE_LIVE = serve(roots[True])
cart468 = json.dumps({"1": {"id": "1", "name": "T", "price": 468, "photo": "", "qty": 1}})
cart3500 = json.dumps({"1": {"id": "1", "name": "T", "price": 3500, "photo": "", "qty": 1}})
VIEW = "(()=>{const t=id=>{const e=document.getElementById(id); return e? e.textContent.trim():null}; const r=document.querySelector('input[name=payment]:checked'); return {pay:r&&r.value, sum:t('sum-delivery'), lbl:t('sum-delivery-label'), aside:t('aside-delivery'), albl:t('aside-delivery-label')}})()"
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (1280, 390):
        ctx = br.new_context(viewport={"width": w, "height": 900}, reduced_motion="reduce")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("**/stock.json*", lambda r: r.abort())
        pg.add_init_script(f"localStorage.setItem('pt_cart_v1', JSON.stringify({cart468}))")
        pg.goto(f"{BASE_LIVE}/cart.html", wait_until="load")
        pg.wait_for_timeout(600)
        v0 = pg.evaluate(VIEW)
        chk(f"{w}px: ПЕРВИННИЙ рендер без кліків: обрана картка, доставка «орієнтовно 70–90 ₴ до 2 кг», підпис без комісії (і в підсумку, і в бічній картці) {v0}",
            v0["pay"] == "prepaid" and v0["sum"] == "орієнтовно 70–90 ₴ до 2 кг" and v0["aside"] == "орієнтовно 70–90 ₴ до 2 кг" and "комісі" not in v0["lbl"] and "комісі" not in v0["albl"])
        pg.check('input[name=payment][value=cod]')
        pg.wait_for_timeout(300)
        v1 = pg.evaluate(VIEW)
        chk(f"{w}px: перемикання на «при отриманні»: 99–119 ₴ і підпис із комісією {v1}", v1["pay"] == "cod" and "99–119" in v1["sum"] and "99–119" in v1["aside"] and "комісі" in v1["lbl"] and "комісі" in v1["albl"])
        pg.check('input[name=payment][value=prepaid]')
        pg.wait_for_timeout(300)
        v2 = pg.evaluate(VIEW)
        chk(f"{w}px: назад на картку: знову 70–90 ₴ без комісії {v2}", v2["pay"] == "prepaid" and v2["sum"] == "орієнтовно 70–90 ₴ до 2 кг" and "комісі" not in v2["lbl"])
        # відновлення форми браузером (bfcache/Firefox): pageshow перераховує під обране
        pg.evaluate("document.querySelector('input[name=payment][value=cod]').checked = true; window.dispatchEvent(new Event('pageshow'))")
        pg.wait_for_timeout(200)
        v3 = pg.evaluate(VIEW)
        chk(f"{w}px: pageshow із відновленим «при отриманні» — підсумок відповідає обраному {v3['sum']}", v3["pay"] == "cod" and "99–119" in v3["sum"])
        chk(f"{w}px: без помилок JS", errs == [])
        ctx.close()
    ctx = br.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    pg = ctx.new_page()
    pg.route("**/stock.json*", lambda r: r.abort())
    pg.add_init_script(f"localStorage.setItem('pt_cart_v1', JSON.stringify({cart3500}))")
    pg.goto(f"{BASE_LIVE}/cart.html", wait_until="load")
    pg.wait_for_timeout(500)
    v = pg.evaluate(VIEW)
    chk(f"кошик 3 500 ₴ (понад стелю накладеного): картка обрана, доставка 70–90 ₴ {v['pay']} / {v['sum']}", v["pay"] == "prepaid" and v["sum"] == "орієнтовно 70–90 ₴ до 2 кг")
    ctx.close()
    BASE_OFF = serve(roots[False])
    ctx = br.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    pg = ctx.new_page()
    pg.route("**/stock.json*", lambda r: r.abort())
    pg.add_init_script(f"localStorage.setItem('pt_cart_v1', JSON.stringify({cart468}))")
    pg.goto(f"{BASE_OFF}/cart.html", wait_until="load")
    pg.wait_for_timeout(500)
    v = pg.evaluate(VIEW)
    chk(f"без LIQPAY_LIVE: за замовчуванням накладений платіж, 99–119 ₴ {v['pay']} / {v['sum']}", v["pay"] == "cod" and "99–119" in v["sum"])
    ctx.close()
    br.close()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
