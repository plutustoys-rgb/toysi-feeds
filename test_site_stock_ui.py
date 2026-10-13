#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_stock_ui.py — 13.10.2026 (власник: відставання наявності сайту від Toysi — «критично»). /stock.json від Code-Agent (кожні 15 хв): s[id]==0 — точно нема; s[id]>0 — верхня межа; id нема — невідомо;
знімок старший за 45 хв (Date − Last-Modified) — «невідомо». Перевіряємо в реальному Chromium (мок /stock.json і /api/order): картка товару ховає «У кошик» лише при свіжому нулі; кошик показує «Щойно закінчилось» і
чинить ЛИШЕ названі рядки; 409 {code:stock_changed} на оформленні — замовлення не створено, повідомлення, «Прибрати / зменшити», решта кошика не чіпається; інший 409 (без code) — звичайна помилка без кнопки."""
import http.server
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
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
prods = [{"id": str(9100 + i), "name": f"Іграшка «Лінія{i}»", "category": f"Кат{i % 3}", "price": 200 + i, "contribution": 120, "stock": 5, "photo": IMG, "desc": ""} for i in range(12)]
cats = {}
for p in prods:
    cats.setdefault(p["category"], []).append(p)
cl = sorted(cats, key=lambda c: -len(cats[c]))
cs = {c: f"c{i}" for i, c in enumerate(cl)}
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.LIQPAY_LIVE = False
os.environ["SITE_PICK_DATE"] = "2026-03-05"
bs.write_product(prods[0], related=None, cat_slug=cs, similar=[])
bs.write_home(prods, cats, cl, cs)
bs.write_cart([])
shutil.copytree(HERE / "site" / "assets", out / "assets")
os.chdir(out)


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
PID = prods[0]["id"]
PURL = f"{BASE}/product-{PID}.html"


def stock_route(snapshot, age_min=1, status=200, nohdr=False):
    now = datetime.now(timezone.utc)

    def handler(route):
        if status != 200:
            route.fulfill(status=status, body="nope")
            return
        hdr = {"Cache-Control": "no-cache"} if nohdr else {"Date": format_datetime(now, usegmt=True), "Last-Modified": format_datetime(now - timedelta(minutes=age_min), usegmt=True), "Cache-Control": "no-cache"}
        route.fulfill(status=200, content_type="application/json", headers=hdr,
                      body=json.dumps({"ts": "2026-10-13T17:17:02", "n": len(snapshot), "s": snapshot}))
    return handler


with sync_playwright() as pw:
    br = pw.chromium.launch()

    def newpage(snapshot=None, age_min=1, status=200, cart=None, w=1280, nohdr=False):
        ctx = br.new_context(viewport={"width": w, "height": 900}, reduced_motion="reduce")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("**/stock.json*", stock_route(snapshot or {}, age_min, status, nohdr))
        if cart is not None:
            pg.add_init_script("localStorage.setItem('pt_cart_v1', JSON.stringify(%s))" % json.dumps(cart, ensure_ascii=False))
        return ctx, pg, errs

    BTN = "(()=>{const b=document.querySelector('.btn[data-add]'); const s=getComputedStyle(b); return [s.display, b.disabled, !!document.querySelector('.stock-now'), (document.querySelector('.avail')||{}).textContent]})()"
    for label, snap, age, st, want_hidden in (("свіжий нуль", {PID: 0}, 1, 200, True), ("є залишок 5", {PID: 5}, 1, 200, False), ("знімок старий (2 год) із нулем — «невідомо»", {PID: 0}, 120, 200, False),
                                              ("id у знімку нема — «невідомо»", {"1": 3}, 1, 200, False), ("stock.json 404", {}, 1, 404, False)):
        ctx, pg, errs = newpage(snap, age, st)
        pg.goto(PURL, wait_until="load")
        pg.wait_for_timeout(500)
        r = pg.evaluate(BTN)
        chk(f"картка товару, {label}: «У кошик» {'прихована (computed display none), блок «Щойно закінчився», статус «Немає в наявності»' if want_hidden else 'видима, блоку нема'} {r}",
            ((r[0] == "none" and r[1] is True and r[2] is True and r[3] == "Немає в наявності") if want_hidden else (r[0] != "none" and r[1] is False and r[2] is False)) and errs == [])
        ctx.close()

    # без заголовків Date/Last-Modified свіжість невідома — кнопку НЕ ховаємо (аудит: new Date(null)=0 давав «свіжий»)
    ctx, pg, errs = newpage({PID: 0}, nohdr=True)
    pg.goto(PURL, wait_until="load")
    pg.wait_for_timeout(500)
    r = pg.evaluate(BTN)
    chk(f"stock.json без Date/Last-Modified (свіжість невідома): «У кошик» видима {r}", r[0] != "none" and r[1] is False and r[2] is False and errs == [])
    ctx.close()

    # картки на головній
    ctx, pg, errs = newpage({p["id"]: 0 for p in prods})
    pg.goto(f"{BASE}/index.html", wait_until="load")
    pg.wait_for_timeout(900)
    r = pg.evaluate("(()=>{const all=[...document.querySelectorAll('a.card[href^=\"product-\"]')]; return [all.length, all.filter(a=>a.hasAttribute('data-clone')).length, all.filter(a=>{const v=a.querySelector('.av'); return v && v.textContent.trim()==='У наявності'}).length, all.filter(a=>{const v=a.querySelector('.av'); return v && v.textContent.trim()==='Немає'}).length]})()")
    chk(f"головна: УСІ картки зі свіжим нулем (включно з клонами каруселі) показують «Немає», жодної «У наявності» {r}", r[0] > 10 and r[1] > 0 and r[2] == 0 and r[3] == r[0] and errs == [])
    ctx.close()

    # кошик
    A, B, C = prods[1], prods[2], prods[3]
    cart = {x["id"]: {"id": x["id"], "name": x["name"], "price": x["price"], "photo": "", "qty": q} for x, q in ((A, 1), (B, 3), (C, 2))}
    ctx, pg, errs = newpage({A["id"]: 0, B["id"]: 2, C["id"]: 10}, cart=cart)
    pg.goto(f"{BASE}/cart.html", wait_until="load")
    pg.wait_for_timeout(600)
    r = pg.evaluate("[(document.getElementById('cart-stock-note')||{}).textContent, [...document.querySelectorAll('.ci-stock')].map(e=>e.textContent)]")
    chk(f"кошик: попередження «Щойно закінчилось» називає A (немає) і B (доступно 2), C не чіпає; підписи біля рядків {r}", r[0] and A["name"] in r[0] and B["name"] in r[0] and C["name"] not in r[0] and sorted(r[1]) == ["Доступно не більше 2 шт.", "Щойно закінчився"])
    pg.click("[data-act=stock-fix]")
    pg.wait_for_timeout(500)
    ls = pg.evaluate("JSON.parse(localStorage.getItem('pt_cart_v1'))")
    chk(f"«Прибрати / зменшити»: A прибрано, B зменшено до 2, C лишилась (qty 2); сповіщення зникло {({k: v['qty'] for k, v in ls.items()})}", A["id"] not in ls and ls[B["id"]]["qty"] == 2 and ls[C["id"]]["qty"] == 2 and pg.evaluate("!document.getElementById('cart-stock-note')") and errs == [])
    ctx.close()

    # кошик з одним рядком: після «Прибрати / зменшити» кошик порожній — попередження зникає (аудит); назва з HTML не виконується
    XSS = "<img src=x onerror=window.__xss=1>Іграшка"
    one = {A["id"]: {"id": A["id"], "name": XSS, "price": A["price"], "photo": "", "qty": 1}}
    ctx, pg, errs = newpage({A["id"]: 0}, cart=one)
    pg.goto(f"{BASE}/cart.html", wait_until="load")
    pg.wait_for_timeout(600)
    note = pg.evaluate("[(document.getElementById('cart-stock-note')||{}).textContent, !!window.__xss, document.querySelectorAll('#cart-stock-note img, .ci-stock img').length]")
    chk(f"назва з HTML-ін'єкцією показується текстом, не виконується {note}", note[0] and "<img" in note[0] and note[1] is False and note[2] == 0)
    pg.click("[data-act=stock-fix]")
    pg.wait_for_timeout(500)
    chk("кошик спорожнів: попередження «Щойно закінчилось» зникло, localStorage порожній", pg.evaluate("!document.getElementById('cart-stock-note')") and pg.evaluate("JSON.parse(localStorage.getItem('pt_cart_v1')||'{}')") == {} and errs == [])
    ctx.close()

    # оформлення: 409 stock_changed
    def fill_and_submit(pg):
        pg.evaluate("""(()=>{const set=(id,v)=>{const e=document.getElementById(id); if(e){ e.disabled=false; e.value=v; }};
            set('f-name','Іван Тестовий'); set('f-phone','+380501112233'); set('f-city','Київ'); set('f-warehouse','Відділення №1');
            const f=document.getElementById('checkout-form'); f.noValidate=true; f.requestSubmit();})()""")

    for kind in ("stock", "other409", "junk409"):
        ctx, pg, errs = newpage({}, cart=cart)
        calls = []
        if kind == "stock":
            body = {"code": "stock_changed", "error": "Деяких товарів уже немає в такій кількості.", "lines": [{"id": A["id"], "name": A["name"], "requested": 1, "available": 0}, {"id": B["id"], "name": B["name"], "requested": 3, "available": 2}]}
        elif kind == "junk409":
            body = {"code": "stock_changed", "error": "x", "lines": [None, 5, {}]}
        else:
            body = {"error": "Замовлення вже існує."}
        pg.route("**/api/order", lambda r, _rq, b=body: (calls.append(1), r.fulfill(status=409, content_type="application/json", body=json.dumps(b, ensure_ascii=False))))
        pg.goto(f"{BASE}/cart.html", wait_until="load")
        pg.wait_for_timeout(500)
        fill_and_submit(pg)
        pg.wait_for_timeout(700)
        msg = pg.evaluate("[(document.getElementById('checkout-msg')||{}).textContent, !!document.querySelector('#checkout-msg button'), location.pathname.endsWith('cart.html'), document.getElementById('checkout-submit').disabled]")
        if kind == "stock":
            chk(f"оформлення 409 stock_changed: замовлення не створено (лишаємось на cart.html), повідомлення з назвами й кількостями, кнопка «Прибрати / зменшити», «Оформити» знову активна {msg}",
                len(calls) == 1 and A["name"] in msg[0] and "доступно 2" in msg[0] and "Замовлення не створено" in msg[0] and msg[1] and msg[2] and msg[3] is False)
            pg.click("#checkout-msg button")
            pg.wait_for_timeout(400)
            ls = pg.evaluate("JSON.parse(localStorage.getItem('pt_cart_v1'))")
            m2 = pg.evaluate("document.getElementById('checkout-msg').textContent")
            chk(f"кнопка: лише A прибрано, B → 2, C без змін; повідомлення «Кошик оновлено…» {m2}", A["id"] not in ls and ls[B["id"]]["qty"] == 2 and ls[C["id"]]["qty"] == 2 and "Кошик оновлено" in m2)
        elif kind == "junk409":
            chk(f"409 stock_changed зі сміттям у lines: загальне повідомлення без винятку й без марної кнопки {msg}", "уже немає" in msg[0] and msg[1] is False and msg[2] and msg[3] is False)
        else:
            chk(f"інший 409 (без code): звичайна помилка без кнопки {msg}", "Замовлення вже існує." in msg[0] and msg[1] is False and msg[2])
        chk("без помилок JS", errs == [])
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
