#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_mail_copy_avatar.py — 15.10.2026 (власник): (1) «коли на кнопку пошти натискаєш — нічого не відбувається» (mailto без поштового клієнта браузер мовчки ігнорує) → клік по будь-якому mailto копіює адресу в буфер і показує тост
«Адресу … скопійовано» (mailto лишається, де клієнт є); (2) «на кнопці підтримка розмісти лого» → замість іконки-бульбашки в кнопці «Підтримка» кругле лого (Плутус у навушниках, support_avatar.webp 28×28). Chromium."""
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
chk("«Підтримка»: у кнопці кругле лого (img.chat-ava 28×28 support_avatar.webp?v=хеш), без старої іконки-бульбашки; текст і aria-label лишились",
    'class="chat-ava"' in h and 'width="28" height="28"' in h and "assets/support_avatar.webp?v=" in h and 'd="M21 12a8 8 0 0 1-11.6 7.1' not in h and '<span class="chat-lbl">Підтримка</span>' in h and 'aria-label="Підтримка: написати нам"' in h)
av = HERE / "site/assets/support_avatar.webp"
chk(f"файл лого існує й легкий ({av.stat().st_size} Б ≤ 12 КБ)", av.exists() and av.stat().st_size <= 12 * 1024)
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.write_cart([])
shutil.copytree(HERE / "site" / "assets", out / "assets")
H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None


class _Srv(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


srv = _Srv(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (1280, 390):
        ctx = br.new_context(viewport={"width": w, "height": 800})
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.goto(f"{base}/cart.html", wait_until="load")
        pg.wait_for_timeout(300)
        # навігацію mailto у headless нічим не відкрити — гасимо її, щоб міряти лише наш обробник
        pg.evaluate("document.addEventListener('click', e => { const a = e.target.closest && e.target.closest('a[href^=\"mailto:\"]'); if (a) e.preventDefault(); }, true)")
        g = pg.evaluate("(()=>{const i=document.querySelector('.chat-ava'); const b=i.getBoundingClientRect(); const bt=document.getElementById('chat-toggle').getBoundingClientRect(); return {w:Math.round(b.width), h:Math.round(b.height), ok:i.complete && i.naturalWidth>0, br:getComputedStyle(i).borderRadius, inside:b.left>=bt.left && b.right<=bt.right && b.top>=bt.top && b.bottom<=bt.bottom, btn:[Math.round(bt.width),Math.round(bt.height)]}})()")
        chk(f"{w}px: лого в кнопці «Підтримка» завантажилось, 28×28, кругле, усередині кнопки {g}", g["ok"] and g["w"] == 28 and g["h"] == 28 and g["br"] in ("50%", "14px") and g["inside"])
        if w >= 900:
            pg.click('.hsoc-a[data-hsoc="email"]')
            pg.wait_for_timeout(300)
            t = pg.evaluate("(()=>{const t=document.querySelector('.pt-toast'); return t && !t.hidden ? t.textContent : null})()")
            clip = pg.evaluate("navigator.clipboard.readText()")
            chk(f"{w}px: клік по значку пошти в шапці: адресу скопійовано в буфер ({clip!r}) і показано тост «{t}»", clip == "plutustoys@gmail.com" and t == "Адресу plutustoys@gmail.com скопійовано")
        pg.click('.foot a[href^="mailto:"]')
        pg.wait_for_timeout(300)
        t2 = pg.evaluate("(()=>{const t=document.querySelector('.pt-toast'); return t && !t.hidden ? t.textContent : null})()")
        chk(f"{w}px: клік по пошті в підвалі — те саме (тост «{t2}»)", t2 == "Адресу plutustoys@gmail.com скопійовано")
        pg.wait_for_timeout(3800)
        chk(f"{w}px: тост сам зникає через ~3,5 с", pg.evaluate("(()=>{const t=document.querySelector('.pt-toast'); return !t || t.hidden})()"))
        chk(f"{w}px: href=mailto: лишився (де клієнт є — відкриється)", pg.evaluate("document.querySelector('.foot a[href^=\"mailto:\"]').getAttribute('href')") == "mailto:plutustoys@gmail.com" and errs == [])
        ctx.close()
    br.close()
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
