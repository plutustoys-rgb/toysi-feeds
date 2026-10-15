#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_header_cart.py — 15.10.2026 (власник, варіант Б зі скетча): кошик — крайній праворуч акцентна «пігулка» (іконка + «Кошик» + лічильник), пошук займає вільне місце (порожнього проміжку нема); на ≤560 px — іконка з лічильником. Chromium, 6 ширин:
порядок пошук → підтримка → (ІГ/ФБ/пошта на ≥900) → кошик, кошик притиснутий до правого краю шапки, фон акцентний, лічильник видно при товарі й нема при порожньому, кошик реально клікабельний (шапка його не перекриває)."""
import functools
import http.server
import os
import shutil
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


h = bs.header()
chk("розмітка: у кошику іконка, підпис «Кошик» (.cart-lbl) і лічильник #cart-badge; aria-label=Кошик лишається", 'aria-label="Кошик"' in h and '<span class="cart-lbl">Кошик</span>' in h and 'id="cart-badge"' in h)
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.write_cart([])
shutil.copytree(HERE / "site" / "assets", out / "assets")
H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None


class _Srv(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):   # Chromium рве з'єднання — не помилка тесту і не «Traceback» для гейта
        pass


srv = _Srv(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
from playwright.sync_api import sync_playwright  # noqa: E402

GEO = """(()=>{const r=s=>{const e=document.querySelector(s); if(!e) return null; const b=e.getBoundingClientRect(); return {l:Math.round(b.left), r:Math.round(b.right), w:Math.round(b.width), h:Math.round(b.height), d:getComputedStyle(e).display}};
 const cs=getComputedStyle(document.querySelector('.cartbtn'));
 return {search:r('.search'), chat:r('.chatwrap'), soc:r('.hsoc'), cart:r('.cartbtn'), lbl:r('.cart-lbl'), bg:cs.backgroundColor, hr:Math.round(document.querySelector('header.top').getBoundingClientRect().right), vw:innerWidth, sw:document.documentElement.scrollWidth}})()"""
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (1656, 1280, 900, 700, 390, 320):
        ctx = br.new_context(viewport={"width": w, "height": 800})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.goto(f"{base}/cart.html", wait_until="load")
        g = pg.evaluate(GEO)
        wide = w >= 561
        order_ok = g["search"]["r"] <= g["chat"]["l"] and g["chat"]["r"] <= g["cart"]["l"] and (g["soc"]["d"] == "none" or (g["chat"]["r"] <= g["soc"]["l"] and g["soc"]["r"] <= g["cart"]["l"]))
        edge_ok = g["hr"] - g["cart"]["r"] <= 24 and g["sw"] <= g["vw"]
        if wide:
            pill_ok = g["cart"]["h"] == 40 and g["lbl"]["d"] != "none" and g["cart"]["w"] > 70 and g["bg"] == "rgb(217, 119, 87)"
        else:
            pill_ok = g["cart"]["w"] == 40 and g["lbl"]["d"] == "none"
        chk(f"{w}px: порядок пошук → підтримка → значки → кошик; кошик притиснутий до правого краю шапки ({g['hr'] - g['cart']['r']} px), без гор. скролу", order_ok and edge_ok)
        chk(f"{w}px: кошик {'акцентна «пігулка» з написом (40 px заввишки, фон accent)' if wide else 'лише іконка 40×40, підпис схований'}: {g['cart']['w']}×{g['cart']['h']} {g['bg']}", pill_ok)
        gap = g["chat"]["l"] - g["search"]["r"]
        chk(f"{w}px: пошук тягнеться до «Підтримка» без порожнього проміжку (відстань {gap} px ≤ 16)", 0 <= gap <= 16)
        bd0 = pg.evaluate("getComputedStyle(document.getElementById('cart-badge')).display")
        pg.evaluate("localStorage.setItem('pt_cart_v1', JSON.stringify({'1': {id:'1', name:'Тест', price:100, photo:'', qty:2}}))")
        pg.reload(wait_until="load")
        bd1 = pg.evaluate("[getComputedStyle(document.getElementById('cart-badge')).display, document.getElementById('cart-badge').textContent]")
        chk(f"{w}px: лічильник схований при порожньому ({bd0}) і видно при товарі {bd1}", bd0 == "none" and bd1[0] != "none" and bd1[1] == "2")
        hit = pg.evaluate("(()=>{const e=document.querySelector('.cartbtn'); const b=e.getBoundingClientRect(); const t=document.elementFromPoint(b.left+b.width/2,b.top+b.height/2); return e===t||e.contains(t)})()")
        pg.click(".cartbtn")
        pg.wait_for_timeout(200)
        chk(f"{w}px: кошик клікабельний (шапка його не перекриває), без помилок JS", hit and pg.evaluate("location.pathname").endswith("cart.html") and errs == [])
        ctx.close()
    br.close()
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
