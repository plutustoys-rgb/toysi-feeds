#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_parcel_dialog.py — 21.10.2026 (власник: «варіант Б… прибери орієнтовну вартість всюди, вкажи за тарифами оператора… показати які товари з якого складу»).
(1) Орієнтовної суми доставки НЕМАЄ ніде (кошик, колонка, картка товару, сторінка «Доставка», app.js): «орієнтовно», «70–90», «140–180» відсутні, є «за тарифами оператора».
(2) Кошик із товарами з різних складів (Toysi / RoyalToys rt- / ZooBaza zb-): при «Оформити» спершу вікно «Замовлення прийде кількома посилками» — товари по посилках (різний фон), без суми; «Повернутися до кошика» / Esc / клік повз вікно
    — запит НЕ йде; «Так, розумію» — рівно один POST /api/order; наступна відправка питає знову. Один склад — вікна нема, запит йде одразу. Назви — текстом, не HTML. Реальний Chromium, мок /api/order."""
import http.server
import json
import os
import re
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

BAD = ("орієнтовно", "70–90", "140–180", "90–135", "94–114")
js = (HERE / "site" / "assets" / "app.js").read_text(encoding="utf-8")
chk("app.js: орієнтовної суми доставки нема (жодного «орієнтовно», «70–90», «140–180»)", not any(w in js for w in BAD) and "DELIV_TARIFF" not in js)
chk("константи: DELIVERY_COST = «за тарифами оператора», у DELIVERY_NOTE/DELIVERY_NOTE_PREPAID/сторінці «Доставка» нема орієнтовної суми, комісія накладеного (20 ₴ + 2%) лишилась",
    bs.DELIVERY_COST == "за тарифами оператора" and not any(w in t for t in (bs.DELIVERY_NOTE, bs.DELIVERY_NOTE_PREPAID, bs._DELIVERY) for w in BAD)
    and "20 ₴ + 2%" in bs.DELIVERY_NOTE and "за тарифами оператора" in bs._DELIVERY and "кілька посилок" in bs._DELIVERY)

out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.LIQPAY_LIVE = True
bs.write_cart([])
prod = {"id": "9100", "name": "Іграшка", "price": 300, "photo": "", "stock": 5, "category": "Іграшки", "desc": "", "contribution": 100, "rt": 0, "noindex": False, "dup_of": ""}
bs.write_product(prod, related=None, cat_slug={}, similar=[])
ph = (out / "product-9100.html").read_text(encoding="utf-8")
ct = (out / "cart.html").read_text(encoding="utf-8")
chk("картка товару і кошик (розмітка): орієнтовної суми нема, є «за тарифами оператора»", not any(w in ph or w in ct for w in BAD) and "за тарифами оператора" in ph and "за тарифами оператора" in ct)
shutil.copytree(HERE / "site" / "assets", out / "assets")
os.chdir(out)


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"

XSS = "<img src=x onerror=window.__xss=1>Лежак"
T1 = {"id": "9100", "name": "Картина «Котик»", "price": 468, "photo": "", "qty": 1}
T2 = {"id": "9101", "name": "Набір для гри", "price": 61, "photo": "", "qty": 2}
R1 = {"id": "rt-45560", "name": "Дидактична гра", "price": 98, "photo": "", "qty": 1}
Z1 = {"id": "zb-305457", "name": XSS, "price": 690, "photo": "", "qty": 1}


def cart_of(*its):
    return {i["id"]: i for i in its}


def fill_and_submit(pg):
    pg.evaluate("""(()=>{const set=(id,v)=>{const e=document.getElementById(id); if(e){ e.disabled=false; e.value=v; }};
        set('f-name','Іван Тестовий'); set('f-phone','+380501112233'); set('f-city','Київ'); set('f-warehouse','Відділення №1');
        const f=document.getElementById('checkout-form'); f.noValidate=true; f.requestSubmit();})()""")


DLG = """(()=>{const d=document.getElementById('parcel-dialog'); if(!d) return null;
  return {groups:Array.from(d.querySelectorAll('.pk-grp')).map(g=>({cls:g.className, title:g.querySelector('b').textContent, items:Array.from(g.querySelectorAll('li')).map(li=>li.textContent)})),
          text:d.textContent, bg:Array.from(d.querySelectorAll('.pk-grp')).map(g=>getComputedStyle(g).backgroundColor), imgs:d.querySelectorAll('img').length, xss:!!window.__xss}})()"""

with sync_playwright() as pw:
    br = pw.chromium.launch()

    def newpage(cart):
        ctx = br.new_context(viewport={"width": 390, "height": 860})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("**/stock.json*", lambda r: r.fulfill(status=404, body=""))
        calls = []
        pg.route("**/api/order", lambda r, _rq: (calls.append(1), r.fulfill(status=400, content_type="application/json", body=json.dumps({"error": "тест"}))))
        pg.add_init_script(f"if(!localStorage.getItem('pt_cart_v1')) localStorage.setItem('pt_cart_v1', JSON.stringify({json.dumps(cart, ensure_ascii=False)}))")
        pg.goto(f"{BASE}/cart.html", wait_until="load")
        pg.wait_for_timeout(500)
        return ctx, pg, errs, calls

    # (1) кошик: рядок доставки без суми
    ctx, pg, errs, calls = newpage(cart_of(T1, T2))
    r = pg.evaluate("['sum-delivery','aside-delivery','delivery-note-sum','delivery-note-aside'].map(i=>{const e=document.getElementById(i); return e?e.textContent:''})")
    chk(f"один склад: рядок «Доставка» = «за тарифами оператора», суми й «орієнтовно» нема ({r[0]!r} / {r[1]!r})", r[0] == "за тарифами оператора" and r[1] == "за тарифами оператора" and not any(w in x for x in r for w in BAD))
    # один склад — вікна нема, запит іде одразу
    fill_and_submit(pg)
    pg.wait_for_timeout(600)
    chk("один склад: вікна підтвердження НЕМАЄ, запит /api/order пішов одразу", pg.evaluate("!document.getElementById('parcel-dialog')") and len(calls) == 1)
    ctx.close()

    # (2) змішаний: Toysi + rt
    ctx, pg, errs, calls = newpage(cart_of(T1, T2, R1))
    r = pg.evaluate("['sum-delivery','aside-delivery'].map(i=>document.getElementById(i).textContent)")
    chk(f"дві посилки: рядок «Доставка» = «за тарифами оператора (за кожну посилку)», суми нема ({r[0]!r})", all(x == "за тарифами оператора (за кожну посилку)" for x in r))
    fill_and_submit(pg)
    pg.wait_for_timeout(500)
    d = pg.evaluate(DLG)
    chk("змішаний кошик: при «Оформити» з'являється вікно, запит ще НЕ пішов", d is not None and len(calls) == 0)
    if d:
        g = d["groups"]
        chk(f"дві посилки: Посилка 1 = Toysi-товари (2 позиції з кількістю), Посилка 2 = партнерський ({[x['items'] for x in g]})", len(g) == 2 and "Посилка 1" in g[0]["title"] and "Посилка 2" in g[1]["title"]
            and g[0]["items"] == ["Картина «Котик»1 шт", "Набір для гри2 шт"] and g[1]["items"] == ["Дидактична гра1 шт"])
        chk(f"різний фон посилок (кожна своє тло): {d['bg']}", len(set(d["bg"])) == 2 and "pk-1" in g[0]["cls"] and "pk-2" in g[1]["cls"])
        chk("у вікні нема суми/цифр вартості («₴», «70», «140») і є «за тарифом оператора»", "₴" not in d["text"] and "70" not in d["text"] and "140" not in d["text"] and "за тарифом оператора" in d["text"] and "кількома посилками" in d["text"])
    # «Повернутися до кошика» — нічого не відправлено
    pg.click("#pk-back")
    pg.wait_for_timeout(200)
    chk("«Повернутися до кошика»: вікно закрито, запит не пішов", pg.evaluate("!document.getElementById('parcel-dialog')") and len(calls) == 0)
    # Esc
    fill_and_submit(pg)
    pg.wait_for_timeout(300)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(200)
    chk("Esc: вікно закрито, запит не пішов", pg.evaluate("!document.getElementById('parcel-dialog')") and len(calls) == 0)
    # клік повз вікно
    fill_and_submit(pg)
    pg.wait_for_timeout(300)
    pg.mouse.click(5, 5)
    pg.wait_for_timeout(200)
    chk("клік повз вікно: закрито, запит не пішов", pg.evaluate("!document.getElementById('parcel-dialog')") and len(calls) == 0)
    # підтвердження → рівно один POST
    fill_and_submit(pg)
    pg.wait_for_timeout(300)
    pg.click("#pk-ok")
    pg.wait_for_timeout(700)
    chk("«Так, розумію»: вікно закрито, рівно ОДИН POST /api/order", pg.evaluate("!document.getElementById('parcel-dialog')") and len(calls) == 1)
    # наступна відправка питає знову (підтвердження не «залипає»)
    fill_and_submit(pg)
    pg.wait_for_timeout(400)
    chk("наступна відправка питає знову (підтвердження не залипає), другий POST ще не пішов", pg.evaluate("!!document.getElementById('parcel-dialog')") and len(calls) == 1)
    chk("без помилок JS", errs == [])
    ctx.close()

    # (3) три посилки: Toysi + rt + zb, назва з HTML — текстом
    ctx, pg, errs, calls = newpage(cart_of(T1, R1, Z1))
    fill_and_submit(pg)
    pg.wait_for_timeout(500)
    d = pg.evaluate(DLG)
    chk(f"три посилки (Toysi / партнер / ZooBaza): три групи з трьома різними тлами", d is not None and len(d["groups"]) == 3 and len(set(d["bg"])) == 3 and "pk-3" in d["groups"][2]["cls"])
    chk("назва з HTML показана текстом (нема <img>, не виконується)", d is not None and d["imgs"] == 0 and d["xss"] is False and "<img" in d["groups"][2]["items"][0])
    chk("без помилок JS", errs == [])
    ctx.close()
    br.close()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
