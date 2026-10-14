#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_header_socials.py — 14.10.2026 (власник, скріншот шапки: «це поле пустує — додай значок інстаграма, фейсбука та пошту»): у шапці між кошиком і кнопкою «Підтримка» — три круглі значки (Instagram, Facebook, пошта).
Посилання ті самі, що в підвалі (utm_medium=header), пошта mailto, target=_blank rel=noopener; на ≥900 px показані в одному рядку з кошиком і кнопкою (між ними), на вужчих приховані; без горизонтального скролу. Chromium."""
import functools
import http.server
import os
import re
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
hs = re.search(r'<div class="hsoc">(.*?)</div>', h, re.S).group(1)
hrefs = re.findall(r'<a class="hsoc-a" href="([^"]+)"', hs)
chk("шапка: рівно 3 значки — Instagram, Facebook, пошта (aria-label, без тексту-плейсхолдера)", len(hrefs) == 3 and 'aria-label="Instagram"' in hs and 'aria-label="Facebook"' in hs and "mailto:plutustoys@gmail.com" in hs)
chk("Instagram/Facebook — ті самі адреси, що в SOCIAL_URLS, з utm_medium=header (не footer); у Facebook один «?»", hrefs[0].startswith(bs.SOCIAL_URLS[0]) and hrefs[1].startswith(bs.SOCIAL_URLS[1]) and all("utm_source=site&utm_medium=header&utm_campaign=social_link" in x for x in hrefs[:2]) and hrefs[1].count("?") == 1 and "utm_medium=footer" not in hs)
chk("зовнішні посилання відкриваються в новій вкладці з rel=noopener", hs.count('target="_blank" rel="noopener"') == 2)
chk("порядок у шапці: кошик → значки → кнопка «Підтримка»", h.index('class="cartbtn"') < h.index('class="hsoc"') < h.index('class="chatwrap"'))

out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.write_cart([])
shutil.copytree(HERE / "site" / "assets", out / "assets")
H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None
class _Srv(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):   # Chromium рве з'єднання при route.abort/закритті — це не помилка тесту (і не має давати «Traceback» у виводі, на який реагує гейт)
        pass


srv = _Srv(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"

from playwright.sync_api import sync_playwright  # noqa: E402

GEO = "(()=>{const r=s=>{const e=document.querySelector(s); const b=e.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.right), Math.round(b.top), Math.round(b.bottom), getComputedStyle(e).display]}; const as=[...document.querySelectorAll('.hsoc-a')].map(e=>{const b=e.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.right), Math.round(b.width), Math.round(b.height)]}); return {cart:r('.cartbtn'), soc:r('.hsoc'), chat:r('.chatbtn'), as:as, sw:document.documentElement.scrollWidth, cw:document.documentElement.clientWidth}})()"
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (1741, 1280, 900, 768, 390, 320):
        ctx = br.new_context(viewport={"width": w, "height": 800})
        pg = ctx.new_page()
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.goto(f"{base}/cart.html", wait_until="load")
        g = pg.evaluate(GEO)
        if w >= 900:
            ok = (g["soc"][4] == "flex" and len(g["as"]) == 3 and all(a[2] == 40 and a[3] == 40 for a in g["as"])
                  and g["cart"][1] <= g["soc"][0] and g["soc"][1] <= g["chat"][0]
                  and abs(g["soc"][2] - g["chat"][2]) <= 8 and g["sw"] <= g["cw"])
            chk(f"{w}px: значки видно, 40×40, в одному рядку між кошиком ({g['cart'][1]}) і кнопкою «Підтримка» ({g['chat'][0]}): {g['soc']}", ok)
        else:
            chk(f"{w}px: значки приховані (місце під шапку; вони є в підвалі), без горизонтального скролу ({g['sw']}/{g['cw']})", g["soc"][4] == "none" and g["sw"] <= g["cw"])
        ctx.close()
    br.close()
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
