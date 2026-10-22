#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_mixed_cart_block.py — 22.10.2026 (Консультант: знахідка Shopify-code — сервер відхиляє змішаний кошик, а вікно «кількома посилками» пускало покупця до кінця оформлення).
Інваріанти кошика (cart.html, реальний браузер):
(1) кошик із товарами різних складів (Toysi / rt- / zb-) блокується ВІДРАЗУ: блок «не можна оформити одним замовленням» з кнопкою на кожну групу, «Оформити» вимкнена, вікна посилок немає, запит /api/order не йде;
(2) один склад — без змін: блоку нема, кнопка активна, запит іде;
(3) «Спершу: …» лишає в кошику одну групу, решту переносить у «на потім» (localStorage pt_cart_later_v1); кошик знову оформлюється;
(4) «на потім»: показується і в порожньому кошику; «Повернути до кошика» / «Оформити їх замість поточних» (обмін) / «Прибрати»; назви товарів екрануються;
(5) прапорець window.PT_ALLOW_MIXED_PARCELS=true повертає стару поведінку (вікно власника 21.10) — код вікна не видалено;
(6) текст про «окремі посилки» в нотатці оплати не обіцяє змішаного кошика."""
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
bs.LIQPAY_LIVE = True
bs.write_cart([])
ct = (out / "cart.html").read_text(encoding="utf-8")
chk("cart.html: є контейнери later-box і mix-block (приховані за замовчуванням)", 'id="later-box" hidden' in ct and 'id="mix-block" role="alert" hidden' in ct)
shutil.copytree(HERE / "site" / "assets", out / "assets")
os.chdir(out)


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"

T1 = {"id": "9100", "name": "Картина «Котик»", "price": 468, "photo": "", "qty": 1}
T2 = {"id": "9101", "name": "Набір для гри", "price": 61, "photo": "", "qty": 2}
R1 = {"id": "rt-45560", "name": "Дидактична гра", "price": 98, "photo": "", "qty": 1}
XSS = "<img src=x onerror=window.__xss=1>Лежак"
Z1 = {"id": "zb-305457", "name": XSS, "price": 690, "photo": "", "qty": 1}


def cart_of(*its):
    return {i["id"]: i for i in its}


def fill_and_submit(pg):
    pg.evaluate("""(()=>{const set=(id,v)=>{const e=document.getElementById(id); if(e){ e.disabled=false; e.value=v; }};
        set('f-name','Іван Тестовий'); set('f-phone','+380501112233'); set('f-city','Київ'); set('f-warehouse','Відділення №1');
        const f=document.getElementById('checkout-form'); f.noValidate=true; f.requestSubmit();})()""")


STATE = """(()=>{const g=k=>{try{return JSON.parse(localStorage.getItem(k)||'{}')}catch(e){return {}}};
  const mb=document.getElementById('mix-block'), lb=document.getElementById('later-box'), sb=document.getElementById('checkout-submit');
  return {cart:Object.keys(g('pt_cart_v1')).sort(), later:Object.keys(g('pt_cart_later_v1')).sort(),
          mixHidden:mb.hidden, mixText:mb.textContent, mixBtns:Array.from(mb.querySelectorAll('[data-mix-keep]')).map(b=>b.getAttribute('data-mix-keep')+'|'+b.textContent),
          laterHidden:lb.hidden, laterText:lb.textContent, laterImgs:lb.querySelectorAll('img').length,
          submitDisabled:sb.disabled, dialog:!!document.getElementById('parcel-dialog'), xss:!!window.__xss,
          msg:(document.getElementById('checkout-msg')||{}).textContent||'', payNote:(document.getElementById('rt-pay-note')||{}).textContent||''}})()"""

with sync_playwright() as pw:
    br = pw.chromium.launch()

    def newpage(cart, later=None, allow_mixed=False):
        ctx = br.new_context(viewport={"width": 390, "height": 860})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("**/stock.json*", lambda r: r.fulfill(status=404, body=""))
        calls = []
        beacons = []
        pg.route("**/api/basket_blocked", lambda r, _rq: (beacons.append(_rq.post_data), r.fulfill(status=200, content_type="application/json", body='{"ok": true}')))
        pg.beacons = beacons
        pg.route("**/api/order", lambda r, _rq: (calls.append(1), r.fulfill(status=400, content_type="application/json", body=json.dumps({"error": "тест"}))))
        if allow_mixed:
            pg.add_init_script("window.PT_ALLOW_MIXED_PARCELS=true")
        pg.add_init_script(f"if(!sessionStorage.getItem('seeded')){{ sessionStorage.setItem('seeded','1'); localStorage.setItem('pt_cart_v1', JSON.stringify({json.dumps(cart, ensure_ascii=False)}));"
                           + (f" localStorage.setItem('pt_cart_later_v1', JSON.stringify({json.dumps(later, ensure_ascii=False)}));" if later else "") + " }")
        pg.goto(f"{BASE}/cart.html", wait_until="load")
        pg.wait_for_timeout(500)
        return ctx, pg, errs, calls

    # (1) змішаний Toysi + rt
    ctx, pg, errs, calls = newpage(cart_of(T1, T2, R1))
    s = pg.evaluate(STATE)
    chk(f"змішаний кошик: блок видно, кнопки на обидві групи ({s['mixBtns']})", not s["mixHidden"] and len(s["mixBtns"]) == 2 and "різних складів" in s["mixText"] and "окремими замовленнями" in s["mixText"])
    chk("кнопки названі по групах з кількістю штук (Toysi: 3 шт, партнер: 1 шт)", any("основні товари (3 шт)" in b for b in s["mixBtns"]) and any("товари партнера (1 шт)" in b for b in s["mixBtns"]))
    chk("«Оформити замовлення» вимкнена", s["submitDisabled"] is True)
    chk("нотатка оплати НЕ обіцяє «окремими посилками» (змішаного кошика не приймаємо)", "окремими посилками" not in s["payNote"] and "лише за передоплатою" in s["payNote"])
    fill_and_submit(pg)
    pg.wait_for_timeout(500)
    s = pg.evaluate(STATE)
    chk("відправка програмно (requestSubmit): вікна посилок НЕМАЄ, запит /api/order не пішов, показано причину", not s["dialog"] and len(calls) == 0 and "різних складів" in s["msg"] and s["submitDisabled"] is True)
    chk("без помилок JS", errs == [])
    b = [json.loads(x) for x in pg.beacons]
    chk(f"облік: заблокований змішаний кошик повідомлено серверу рівно ОДИН раз, лише id і к-сть, без персональних даних ({b})",
        len(b) == 1 and set(b[0]) == {"items"} and sorted((i["id"], i["qty"]) for i in b[0]["items"]) == [("9100", 1), ("9101", 2), ("rt-45560", 1)] and all(set(i) == {"id", "qty"} for i in b[0]["items"]))
    # (3) «Спершу: основні товари» → у кошику лишається Toysi, rt іде «на потім»
    pg.click("[data-mix-keep='t']")
    pg.wait_for_timeout(300)
    s = pg.evaluate(STATE)
    chk(f"«Спершу: основні товари»: у кошику лише Toysi ({s['cart']}), rt-товар у «на потім» ({s['later']})", s["cart"] == ["9100", "9101"] and s["later"] == ["rt-45560"])
    chk("після вибору: блок схований, «Оформити» активна, видно «Збережено на потім: 1 шт» і назву", s["mixHidden"] and not s["submitDisabled"] and not s["laterHidden"] and "Збережено на потім: 1 шт" in s["laterText"] and "Дидактична гра" in s["laterText"])
    fill_and_submit(pg)
    pg.wait_for_timeout(500)
    chk("кошик з однієї групи оформлюється: запит /api/order пішов рівно раз", len(calls) == 1)
    # (4) обмін: «Оформити їх замість поточних»
    pg.click("[data-later-swap]")
    pg.wait_for_timeout(300)
    s = pg.evaluate(STATE)
    chk(f"обмін: у кошику rt-товар, Toysi пішов у «на потім» (кошик {s['cart']}, later {s['later']})", s["cart"] == ["rt-45560"] and s["later"] == ["9100", "9101"])
    chk("після обміну кошик знову з однієї групи — без блоку", s["mixHidden"] and not s["submitDisabled"])
    ctx.close()

    # (2) один склад — без змін
    ctx, pg, errs, calls = newpage(cart_of(T1, T2))
    s = pg.evaluate(STATE)
    chk("один склад: блоку і «на потім» нема, «Оформити» активна, облік блокування НЕ шлеться", s["mixHidden"] and s["laterHidden"] and not s["submitDisabled"] and pg.beacons == [])
    fill_and_submit(pg)
    pg.wait_for_timeout(500)
    chk("один склад: вікна нема, запит пішов одразу", not pg.evaluate(STATE)["dialog"] and len(calls) == 1)
    ctx.close()

    # (3б) три групи: лишаємо партнера → дві інші разом у «на потім»; назва з HTML екранується
    ctx, pg, errs, calls = newpage(cart_of(T1, R1, Z1))
    s = pg.evaluate(STATE)
    chk(f"три групи: три кнопки ({len(s['mixBtns'])}), блок видно", len(s["mixBtns"]) == 3 and not s["mixHidden"])
    pg.click("[data-mix-keep='r']")
    pg.wait_for_timeout(300)
    s = pg.evaluate(STATE)
    chk(f"лишили партнера: кошик {s['cart']}, «на потім» — Toysi + zb ({s['later']})", s["cart"] == ["rt-45560"] and s["later"] == ["9100", "zb-305457"])
    chk("назва з HTML у «на потім» показана текстом (нема <img>, XSS не виконався)", s["laterImgs"] == 0 and s["xss"] is False and "<img" in s["laterText"])
    chk("без помилок JS", errs == [])
    pg.click("[data-later-drop]")
    pg.wait_for_timeout(300)
    s = pg.evaluate(STATE)
    chk("«Прибрати»: «на потім» порожнє, блок схований", s["later"] == [] and s["laterHidden"])
    ctx.close()

    # (4б) порожній кошик + «на потім»: видно, «Повернути до кошика»
    ctx, pg, errs, calls = newpage({}, later=cart_of(T1, R1))
    s = pg.evaluate(STATE)
    chk("порожній кошик: «на потім» видно (кнопка «Повернути до кошика»)", not s["laterHidden"] and "Повернути до кошика" in s["laterText"] and "Збережено на потім: 2 шт" in s["laterText"])
    pg.click("[data-later-swap]")
    pg.wait_for_timeout(300)
    s = pg.evaluate(STATE)
    chk(f"«Повернути до кошика»: товари в кошику ({s['cart']}), «на потім» порожнє; кошик змішаний — блок знову, вибір за покупцем", s["cart"] == ["9100", "rt-45560"] and s["later"] == [] and not s["mixHidden"] and s["submitDisabled"])
    ctx.close()

    # (5) прапорець зворотної сумісності
    ctx, pg, errs, calls = newpage(cart_of(T1, R1), allow_mixed=True)
    s = pg.evaluate(STATE)
    chk("PT_ALLOW_MIXED_PARCELS=true: блоку нема, кнопка активна (стара поведінка), облік блокування не шлеться", s["mixHidden"] and not s["submitDisabled"] and pg.beacons == [])
    fill_and_submit(pg)
    pg.wait_for_timeout(500)
    s = pg.evaluate(STATE)
    chk("PT_ALLOW_MIXED_PARCELS=true: з'являється вікно посилок, запит ще не пішов; нотатка оплати знову згадує окремі посилки", s["dialog"] and len(calls) == 0 and "окремими посилками" in s["payNote"])
    ctx.close()
    br.close()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
