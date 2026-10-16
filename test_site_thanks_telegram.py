#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_thanks_telegram.py — 16.10.2026 (власник через Code): на «Дякуємо за замовлення» кнопка «Отримувати статус у Telegram» → https://t.me/plutustoys_support_bot?start=<номер замовлення>
(номер той самий, що бачить клієнт; payload Telegram [A-Za-z0-9_-] ≤ 64). Кнопка з'являється лише коли номер відомий і підходить; без номера — прихована. Футер: у верхньому рядку «Усі категорії» нема (власник 16.10). Chromium."""
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


out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.write_thanks()
h = (out / "thanks.html").read_text(encoding="utf-8")
chk("розмітка: прихований <p id=thanks-tg-wrap> із кнопкою «Отримувати статус у Telegram» (target=_blank, rel=noopener), базове посилання — бот без start", '<p class="thanks-tg-wrap" id="thanks-tg-wrap" hidden>' in h and 'id="thanks-tg" href="https://t.me/plutustoys_support_bot"' in h and "Отримувати статус у Telegram" in h and 'rel="noopener"' in h)
nav = re.search(r'<nav class="fnav">(.*?)</nav>', h, re.S).group(1)
chk("футер: у верхньому рядку нема «Усі категорії» (власник 16.10), решта пунктів на місці", "Усі категорії" not in nav and all(x in nav for x in ("Про нас", "Доставка", "Повернення", "Контакти", "Публічна оферта", "Політика конфіденційності")))
shutil.copytree(HERE / "site" / "assets", out / "assets", dirs_exist_ok=True)
H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
from playwright.sync_api import sync_playwright  # noqa: E402

GET = "(()=>{const a=document.getElementById('thanks-tg'), w=document.getElementById('thanks-tg-wrap'); return [a.getAttribute('href'), w.hidden, getComputedStyle(w).display]})()"
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for order, ok in (("PT-20261016-153045-a1b2c3", True), ("", False), ("PT 2026/10 небезпечний?x=1&y=2", False), ("A" * 65, False)):
        pg = br.new_page(viewport={"width": 390, "height": 800})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.goto(f"{base}/thanks.html", wait_until="load")
        pg.evaluate("o => { sessionStorage.clear(); if (o) sessionStorage.setItem('pt_last_order', o); }", order)
        pg.reload(wait_until="load")
        pg.wait_for_timeout(300)
        href, hidden, disp = pg.evaluate(GET)
        if ok:
            chk(f"номер {order}: кнопка видима, href = бот?start=<номер> ({href})", href == f"https://t.me/plutustoys_support_bot?start={order}" and hidden is False and disp != "none")
        else:
            chk(f"номер {order[:30]!r}: кнопка прихована, без start у href ({href})", hidden is True and disp == "none" and "start=" not in href)
        chk("без помилок JS", errs == [])
        pg.close()
    br.close()
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
