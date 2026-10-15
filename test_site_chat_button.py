#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_chat_button.py — 14.10.2026 (власник, варіант А): кнопка «Написати» у ПРАВОМУ ВЕРХНЬОМУ куті шапки → меню месенджерів: Viber (номер загального телефону), Instagram; Telegram — лише коли є бот (CHAT_TELEGRAM_URL).
Chromium 1280/390/320: кнопка справа від кошика, меню відкривається/закривається (клік, повторний клік, клік поза меню, Escape), aria-expanded, посилання в новій вкладці з noopener, без горизонтального скролу; на вузьких екранах лише іконка."""
import os
import sys
import tempfile
import shutil
import threading
import http.server
import socketserver
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
os.environ["SITE_PICK_DATE"] = "2026-03-05"
bs.write_cart([])
_cart = (out / "cart.html")
bs.write_cart([])
h = _cart.read_text(encoding="utf-8")
chk("меню: Viber (за номером) і Telegram (бот t.me/plutustoys_support_bot), БЕЗ Instagram (власник 14.10); посилання target=_blank rel=noopener", 'data-chat="viber" href="viber://chat?number=%2B380730150815" target="_blank" rel="noopener"' in h and 'data-chat="telegram" href="https://t.me/plutustoys_support_bot" target="_blank" rel="noopener"' in h and 'data-chat="instagram"' not in h and "ig.me" not in h)
bs.CHAT_TELEGRAM_URL = ""
bs.write_cart([])
chk("кнопка в шапці підписана «Підтримка» (власник 14.10), не «Написати»", '<span class="chat-lbl">Підтримка</span>' in h and '<span class="chat-lbl">Написати</span>' not in h)
chk("порожній CHAT_TELEGRAM_URL — пункт Telegram не показується", 'data-chat="telegram"' not in _cart.read_text(encoding="utf-8"))
bs.CHAT_TELEGRAM_URL = "https://t.me/plutustoys_support_bot"
bs.write_cart([])
shutil.copytree(HERE / "site" / "assets", out / "assets")
os.chdir(out)


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
GEO = "(()=>{const b=document.getElementById('chat-toggle').getBoundingClientRect(); const c=document.querySelector('.cartbtn').getBoundingClientRect(); const h=document.querySelector('header.top').getBoundingClientRect(); const m=document.getElementById('chat-menu'); return {right:Math.round(b.right), vw:innerWidth, cartLeft:Math.round(c.left), cartRight:Math.round(c.right), top:Math.round(b.top), headerRight:Math.round(h.right), headerBottom:Math.round(h.bottom), menuDisplay:getComputedStyle(m).display, exp:document.getElementById('chat-toggle').getAttribute('aria-expanded'), lblDisplay:getComputedStyle(document.querySelector('.chat-lbl')).display, sw:document.documentElement.scrollWidth<=innerWidth, w:Math.round(b.width)}})()"
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (1280, 390, 320):
        ctx = br.new_context(viewport={"width": w, "height": 800}, reduced_motion="reduce")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("**/stock.json*", lambda r: r.abort())
        pg.goto(f"{BASE}/cart.html", wait_until="load")
        pg.wait_for_timeout(500)
        g = pg.evaluate(GEO)
        chk(f"{w}px: кнопка «Підтримка» — у шапці в одному рядку з кошиком, ЛІВІШЕ нього (порядок власника 15.10: пошук → підтримка → ІГ/ФБ/пошта → кошик крайній праворуч), меню закрите, aria-expanded=false, без горизонтального скролу {g}",
            g["right"] <= g["cartLeft"] and g["headerRight"] - g["cartRight"] <= 24 and g["top"] < g["headerBottom"] and g["menuDisplay"] == "none" and g["exp"] == "false" and g["sw"])
        chk(f"{w}px: підпис «Підтримка» видно на широких, на ≤560 px лише іконка (aria-label лишається) {g['lblDisplay']}/{g['w']}px", (g["lblDisplay"] != "none") == (w > 560))
        pg.click("#chat-toggle")
        pg.wait_for_timeout(150)
        o = pg.evaluate("(()=>{const m=document.getElementById('chat-menu'); const r=m.getBoundingClientRect(); return [getComputedStyle(m).display, document.getElementById('chat-toggle').getAttribute('aria-expanded'), [...m.querySelectorAll('a')].map(a=>a.dataset.chat), r.right<=innerWidth+1, r.left>=0]})()")
        chk(f"{w}px: клік відкриває меню (Viber, Telegram), у межах екрана {o}", o[0] != "none" and o[1] == "true" and o[2] == ["viber", "telegram"] and o[3] and o[4])
        pg.click("#chat-toggle")
        pg.wait_for_timeout(100)
        chk(f"{w}px: повторний клік закриває", pg.evaluate("getComputedStyle(document.getElementById('chat-menu')).display") == "none")
        pg.click("#chat-toggle")
        pg.mouse.click(5, 600)
        pg.wait_for_timeout(100)
        chk(f"{w}px: клік поза меню закриває", pg.evaluate("getComputedStyle(document.getElementById('chat-menu')).display") == "none")
        pg.click("#chat-toggle")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(100)
        chk(f"{w}px: Escape закриває й повертає фокус на кнопку", pg.evaluate("getComputedStyle(document.getElementById('chat-menu')).display") == "none" and pg.evaluate("document.activeElement.id") == "chat-toggle")
        chk(f"{w}px: без помилок JS", errs == [])
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
