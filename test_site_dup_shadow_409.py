#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_dup_shadow_409.py — 20.10.2026 (тестувальник 21 + Code-Agent PR 930, варіант b): сервер відповідає на замовлення з «тінню» (rt-дубль Toysi у наявності)
409 {code:"dup_shadow", error, lines:[{id, name, dup_of}]}: замовлення НЕ створено, інші позиції кошика не чіпаються. Фронт: прибирає ТІЛЬКИ названі позиції з кошика, показує текстом (не HTML)
повідомлення з посиланням «Перейти до товару →» на product-<dup_of>.html; dup_of порожній/дивний — без посилання (нічого не вигадуємо); сміття в lines — загальне повідомлення без винятку й без змін кошика;
кошик, що спорожнів, — повідомлення над порожнім станом (форма схована). Реальний Chromium, мок /api/order."""
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

out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.LIQPAY_LIVE = False
bs.write_cart([])
shutil.copytree(HERE / "site" / "assets", out / "assets")
os.chdir(out)


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"

XSS = "<img src=x onerror=window.__xss=1>Тінь"
SH1 = {"id": "rt-56350", "name": "Конструктор Місто", "price": 263, "photo": "", "qty": 1}
SH2 = {"id": "rt-27310", "name": XSS, "price": 150, "photo": "", "qty": 2}
OK1 = {"id": "9100", "name": "Звичайна іграшка", "price": 300, "photo": "", "qty": 1}


def cart_of(*items):
    return {i["id"]: i for i in items}


def fill_and_submit(pg):
    pg.evaluate("""(()=>{const set=(id,v)=>{const e=document.getElementById(id); if(e){ e.disabled=false; e.value=v; }};
        set('f-name','Іван Тестовий'); set('f-phone','+380501112233'); set('f-city','Київ'); set('f-warehouse','Відділення №1');
        const f=document.getElementById('checkout-form'); f.noValidate=true; f.requestSubmit();})()""")


with sync_playwright() as pw:
    br = pw.chromium.launch()
    cases = (
        ("з близнюком, змішаний кошик", cart_of(SH1, OK1), {"code": "dup_shadow", "error": "x", "lines": [{"id": "rt-56350", "name": "Конструктор Місто", "dup_of": "274643"}]}),
        ("dup_of порожній (мапа не знайшла)", cart_of(SH1, OK1), {"code": "dup_shadow", "error": "x", "lines": [{"id": "rt-56350", "name": "Конструктор Місто", "dup_of": ""}]}),
        ("dup_of дивний (не цифри)", cart_of(SH1, OK1), {"code": "dup_shadow", "error": "x", "lines": [{"id": "rt-56350", "name": "Конструктор Місто", "dup_of": "../evil\"><b>"}]}),
        ("кошик лише з тінями (спорожнів), назва з HTML", cart_of(SH1, SH2), {"code": "dup_shadow", "error": "x", "lines": [{"id": "rt-56350", "name": "Конструктор Місто", "dup_of": "274643"}, {"id": "rt-27310", "name": XSS, "dup_of": "223633"}]}),
        ("сміття в lines", cart_of(SH1, OK1), {"code": "dup_shadow", "error": "x", "lines": [None, 5, {}]}),
    )
    for label, cart, body in cases:
        ctx = br.new_context(viewport={"width": 1280, "height": 900})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("**/stock.json*", lambda r: r.fulfill(status=404, body=""))
        calls = []
        pg.route("**/api/order", lambda r, _rq, b=body: (calls.append(1), r.fulfill(status=409, content_type="application/json", body=json.dumps(b, ensure_ascii=False))))
        pg.add_init_script("window.PT_ALLOW_MIXED_PARCELS=true")   # тест перевіряє обробку 409 від сервера (мок); змішаний кошик тут лише фікстура — на живому сайті його блокує cart (test_site_mixed_cart_block)
        pg.add_init_script(f"if(!localStorage.getItem('pt_cart_v1')) localStorage.setItem('pt_cart_v1', JSON.stringify({json.dumps(cart, ensure_ascii=False)}))")
        pg.goto(f"{BASE}/cart.html", wait_until="load")
        pg.wait_for_timeout(500)
        fill_and_submit(pg)
        pg.wait_for_timeout(400)
        if pg.evaluate("!!document.getElementById('parcel-dialog')"):   # змішаний кошик (rt- + Toysi): спершу вікно «кілька посилок» (власник 21.10) — покупець підтверджує
            pg.click("#pk-ok")
        pg.wait_for_timeout(800)
        st = pg.evaluate("""(()=>{const m=document.getElementById('checkout-msg'), n=document.getElementById('dup-shadow-note'), host=(n||m);
            const vis=e=>!!e && e.offsetParent!==null;
            return {cart:JSON.parse(localStorage.getItem('pt_cart_v1')||'{}'), text:host?host.textContent:'', hostVisible:vis(host), links:Array.from((host||document).querySelectorAll('a')).map(a=>a.getAttribute('href')),
                    imgs:host?host.querySelectorAll('img').length:0, xss:!!window.__xss, onCart:location.pathname.endsWith('cart.html'), btnDisabled:document.getElementById('checkout-submit').disabled}})()""")
        ids = set(st["cart"])
        chk(f"[{label}] замовлення не створено (лишаємось на cart.html, один запит), кнопка «Оформити» знову активна", len(calls) == 1 and st["onCart"] and st["btnDisabled"] is False)
        if label.startswith("сміття"):
            chk(f"[{label}] загальне повідомлення, кошик НЕ змінено", "вже продаються на іншій сторінці" in st["text"] and ids == set(cart))
        else:
            chk(f"[{label}] названі позиції прибрано з кошика, решта на місці ({sorted(ids)})", not (ids & {"rt-56350", "rt-27310"}) and ({"9100"} <= ids if "9100" in cart else True))
            chk(f"[{label}] повідомлення видно покупцю і називає товар текстом, не HTML", st["hostVisible"] and "уже продається на іншій сторінці" in st["text"] and st["imgs"] == 0 and st["xss"] is False)
            if label.startswith("з близнюком"):
                chk(f"[{label}] посилання на близнюка product-274643.html ({st['links']})", st["links"] == ["product-274643.html"])
            elif label.startswith("кошик лише"):
                chk(f"[{label}] два посилання на близнюків, назва з HTML показана як текст ({st['links']})", st["links"] == ["product-274643.html", "product-223633.html"] and "<img" in st["text"])
            else:
                chk(f"[{label}] посилання нема ({st['links']})", st["links"] == [])
        chk(f"[{label}] без помилок JS", errs == [])
        ctx.close()
    br.close()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
