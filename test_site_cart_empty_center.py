#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_cart_empty_center.py — 14.10.2026 (власник, скріншот: «не по центру»): порожній кошик на ПК стояв у лівій колонці двоколонкової сітки (1fr 380px; права колонка прихована), тож «Кошик порожній» і рядок про повернення були лівіше центру
сторінки. Тепер при порожньому кошику сітка — одна колонка (.cart-grid.is-empty), текст по центру. Непорожній кошик на ПК лишається в 2 колонки. Chromium."""
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


out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.write_cart([])
shutil.copytree(HERE / "site" / "assets", out / "assets")
H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"

from playwright.sync_api import sync_playwright  # noqa: E402

CENTERS = "(()=>{const c=s=>{const e=document.querySelector(s); if(!e) return null; const r=e.getBoundingClientRect(); return r.left+r.width/2}; const t=document.querySelector('.ret-cart'); return [c('#cart-body .empty'), document.documentElement.clientWidth/2, getComputedStyle(t).textAlign, getComputedStyle(document.querySelector('.cart-grid')).gridTemplateColumns.trim().split(/\s+/).length]})()"
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (1422, 1280, 960, 390):
        ctx = br.new_context(viewport={"width": w, "height": 800})
        pg = ctx.new_page()
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.goto(f"{base}/cart.html", wait_until="load")
        pg.wait_for_selector("#cart-body .empty")
        c, mid, ta, ncols = pg.evaluate(CENTERS)
        chk(f"{w}px: порожній кошик — центр блоку ({round(c)}) = центр вікна ({round(mid)}) ±3 px, одна колонка ({ncols}), рядок про повернення по центру ({ta})", abs(c - mid) <= 3 and ncols == 1 and ta == "center")
        if w >= 960:
            pg.evaluate("localStorage.setItem('pt_cart_v1', JSON.stringify({'1': {id: '1', name: 'Тест', price: 100, photo: '', qty: 1}}))")
            pg.reload(wait_until="load")
            pg.wait_for_selector("#aside-order", state="attached")
            ncols2 = pg.evaluate("getComputedStyle(document.querySelector('.cart-grid')).gridTemplateColumns.trim().split(/\s+/).length")
            has_empty = pg.evaluate("document.querySelector('.cart-grid').classList.contains('is-empty')")
            chk(f"{w}px: з товаром у кошику сітка лишається у 2 колонки ({ncols2}), без is-empty", ncols2 == 2 and not has_empty)
        ctx.close()
    br.close()
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
