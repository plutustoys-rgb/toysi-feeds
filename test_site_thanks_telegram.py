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
chk("розмітка: прихований блок #thanks-tg-wrap із кнопкою «Старт у Telegram» (target=_blank, rel=noopener), базове посилання — бот без start, і фраза «Натисніть «Старт» у боті — і ми напишемо вам, коли замовлення відправлять»", '<div class="thanks-tg" id="thanks-tg-wrap" hidden>' in h and 'id="thanks-tg" href="https://t.me/plutustoys_support_bot"' in h and ">Старт у Telegram</a>" in h and 'rel="noopener"' in h and "Натисніть «Старт» у боті — і ми напишемо вам, коли замовлення відправлять." in h)
bs.write_cart()
ch = (out / "cart.html").read_text(encoding="utf-8")
chk("чекаут: галочка «Хочу отримувати статус замовлення в Telegram» за замовчуванням ввімкнена (checked), одна на сторінці, не в блоці .field (інакше width:100%)", ch.count('id="f-tg"') == 1 and '<input type="checkbox" id="f-tg" name="tg" checked>' in ch and "Хочу отримувати статус замовлення в Telegram" in ch)
nav = re.search(r'<nav class="fnav">(.*?)</nav>', h, re.S).group(1)
chk("футер: верхній рядок починається з «Усі категорії товарів» (власник 19.10, перед «Про нас»), решта пунктів на місці", nav.startswith('<a href="categories.html">Усі категорії товарів</a>') and all(x in nav for x in ("Про нас", "Доставка", "Повернення", "Контакти", "Публічна оферта", "Політика конфіденційності")))
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
    # ?o= (повернення з LiqPay в іншу вкладку: sessionStorage порожній) — лише точний формат PT-YYYYMMDD-HHMMSS-xxxxxx; номер видно на сторінці
    OID = "document.getElementById('thanks-oid').textContent"
    good = "PT-20261020-101530-0a1b2c"
    for qs, ok, label in ((f"?o={good}", True, "валідний ?o="), ("?o=PT-20261020-101530-ZZZZZZ", False, "?o= з не-hex"), ("?o=PT-1", False, "?o= короткий"), ("?o=%3Cscript%3E", False, "?o= з розміткою"),
                          (f"?x=1&o={good}", True, "?o= не першим параметром"), (f"?o={good}A", False, "?o= з хвостом")):
        pg = br.new_page(viewport={"width": 390, "height": 800})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.goto(f"{base}/thanks.html{qs}", wait_until="load")
        pg.evaluate("() => { sessionStorage.clear(); localStorage.clear(); }")
        pg.reload(wait_until="load")
        pg.wait_for_timeout(300)
        href, hidden, disp = pg.evaluate(GET)
        oid = pg.evaluate(OID)
        if ok:
            chk(f"{label}: номер показано ({oid}), кнопка видима, href = бот?start=<номер>", oid == good and href == f"https://t.me/plutustoys_support_bot?start={good}" and hidden is False and disp != "none")
        else:
            chk(f"{label}: ігнорується — номера нема, кнопка прихована", oid == "" and hidden is True and disp == "none" and "start=" not in href)
        chk(f"{label}: без помилок JS", errs == [])
        pg.close()
    # sessionStorage має пріоритет над ?o=
    pg = br.new_page(viewport={"width": 390, "height": 800})
    pg.route("**/googletagmanager.com/**", lambda r: r.abort())
    pg.goto(f"{base}/thanks.html?o={good}", wait_until="load")
    pg.evaluate("() => { sessionStorage.clear(); sessionStorage.setItem('pt_last_order', 'PT-20261020-090000-ffffff'); }")
    pg.reload(wait_until="load")
    pg.wait_for_timeout(300)
    chk("sessionStorage-номер має пріоритет над ?o=", pg.evaluate(OID) == "PT-20261020-090000-ffffff" and pg.evaluate(GET)[0].endswith("start=PT-20261020-090000-ffffff"))
    pg.close()
    # галочка на чекауті: «увімкнена»/нема вибору → помітний блок; «вимкнена» → той самий блок, але is-calm (тихіше), кнопка лишається видимою
    CLS = "(()=>{const w=document.getElementById('thanks-tg-wrap'); const b=getComputedStyle(document.getElementById('thanks-tg')); return [w.className, w.hidden, document.getElementById('thanks-tg-hint').textContent, parseFloat(getComputedStyle(w).borderTopWidth), b.fontSize]})()"
    res = {}
    for opt in (None, "1", "0"):
        pg = br.new_page(viewport={"width": 390, "height": 800})
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.goto(f"{base}/thanks.html", wait_until="load")
        pg.evaluate("(o) => { sessionStorage.clear(); localStorage.clear(); sessionStorage.setItem('pt_last_order', 'PT-20261020-101530-0a1b2c'); if (o) sessionStorage.setItem('pt_tg_opt', o); }", opt)
        pg.reload(wait_until="load")
        pg.wait_for_timeout(300)
        res[opt] = pg.evaluate(CLS)
        pg.close()
    chk(f"галочка ввімкнена/вибору нема: блок помітний (рамка), без is-calm, фраза «Натисніть «Старт»…» ({res[None][0]!r}, {res['1'][0]!r})", all(res[k][0] == "thanks-tg" and res[k][1] is False and res[k][3] > 0 and res[k][2].startswith("Натисніть «Старт»") for k in (None, "1")))
    chk(f"галочка вимкнена: кнопка ЛИШАЄТЬСЯ видимою, але тихіше (is-calm, без рамки, інша фраза «Передумали?…») ({res['0'][0]!r})", res["0"][0] == "thanks-tg is-calm" and res["0"][1] is False and res["0"][3] == 0 and res["0"][2].startswith("Передумали?"))
    # localStorage-резерв: sessionStorage порожній (інша вкладка після LiqPay), вибір «вимкнено» лежить у localStorage
    pg = br.new_page(viewport={"width": 390, "height": 800})
    pg.route("**/googletagmanager.com/**", lambda r: r.abort())
    pg.goto(f"{base}/thanks.html?o={good}", wait_until="load")
    pg.evaluate("() => { sessionStorage.clear(); localStorage.setItem('pt_tg_opt', '0'); }")
    pg.reload(wait_until="load")
    pg.wait_for_timeout(300)
    chk("інша вкладка (sessionStorage порожній): вибір береться з localStorage → is-calm", pg.evaluate(CLS)[0] == "thanks-tg is-calm")
    pg.close()
    br.close()
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
