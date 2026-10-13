#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_promo_ui.py — 13.10.2026 (власник, Консультант, SMM; бекенд Code-Agent #738): UI знижок у кошику. POST api/promo/quote {items, promo_code?, phone?} → {applied, code_status, message, discount_total, list_total, total, lines}.
PLUTUS5 = −5%, авто −10% при ≥2 різних товарах, НЕ складаються (береться більша); знижка не на всі рядки. Перевіряємо в реальному Chromium (мок бекенду): сервер — єдиний авторитет суми;
нема бекенду (404) — поля промокоду й знижок нема; окремий рядок «Знижка …», «застосована до N із M позицій»; код без урахування регістру; «Код не діє»; 400 promo_* на оформленні → причина + «Продовжити без коду»;
застаріла відповідь не показується; у /api/order іде promo_code."""
import http.server
import json
import math
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

PRICES = {"9601": 200, "9602": 300, "9610": 100}
NAMES = {"9601": "Іграшка А", "9602": "Іграшка Б", "9610": "Іграшка В"}
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


def quote(body):
    items = body["items"]
    code = (body.get("promo_code") or "").strip().lower()
    list_total = sum(PRICES[i["id"]] * i["qty"] for i in items)
    status, applied, rate, msg = "none", None, 0.0, ""
    if code:
        if code == "plutus5":
            status, applied, rate = "ok", "PLUTUS5", 0.05
        elif code == "used":
            status, msg = "used", "Цей код уже використано."
        else:
            status, msg = "unknown", "Код не діє."
    if len(items) >= 2 and rate < 0.10:
        rate, applied = 0.10, "TWO10"
    lines, disc = [], 0
    for i in items:
        lp = PRICES[i["id"]]
        up = math.ceil(lp * (1 - rate)) if (rate and not i["id"].endswith("0")) else lp
        disc += (lp - up) * i["qty"]
        lines.append({"id": i["id"], "qty": i["qty"], "list_price": lp, "unit_price": up})
    return {"applied": applied if disc else None, "code_status": status, "message": msg, "discount_total": disc, "list_total": list_total, "total": list_total - disc, "lines": lines}


def cartjs(ids_qty):
    return "localStorage.setItem('pt_cart_v1', JSON.stringify(%s))" % json.dumps({i: {"id": i, "name": NAMES[i], "price": PRICES[i], "photo": "", "qty": q} for i, q in ids_qty.items()}, ensure_ascii=False)


VIEW = "(()=>{const t=id=>{const e=document.getElementById(id); return e? e.textContent.trim():null}; const vis=id=>{const e=document.getElementById(id); return !!e && getComputedStyle(e).display!=='none'}; return {goods:t('sum-goods'), total:t('sum-total'), aside_total:t('aside-total'), disc_vis:vis('sum-discount-row'), disc_lbl:t('sum-discount-label'), disc:t('sum-discount'), aside_disc_vis:vis('aside-discount-row'), promo_vis:vis('promo-field'), msg:t('promo-msg'), strikes:document.querySelectorAll('#cart-body .ci-pr s').length}})()"

with sync_playwright() as pw:
    br = pw.chromium.launch()
    reqs = []
    orders = []

    def newpage(cart, backend=True, order_status=200, w=1280):
        ctx = br.new_context(viewport={"width": w, "height": 900}, reduced_motion="reduce")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("**/stock.json*", lambda r: r.abort())
        pg.add_init_script(cartjs(cart))
        reqs.clear()
        orders.clear()

        def q(route, request):
            body = request.post_data_json
            reqs.append(body)
            if not backend:
                route.fulfill(status=404, content_type="application/json", body='{"error":"невідомий ендпоінт"}')
            else:
                route.fulfill(status=200, content_type="application/json", body=json.dumps(quote(body), ensure_ascii=False))

        def o(route, request):
            body = request.post_data_json
            orders.append(body)
            if (body.get("promo_code") or "").lower() == "used":
                route.fulfill(status=400, content_type="application/json", body=json.dumps({"code": "promo_used", "error": "Цей код уже використано."}, ensure_ascii=False))
            else:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"order_id": "PT-TEST-1"}))

        pg.route("**/api/promo/quote", q)
        pg.route("**/api/order", o)
        return ctx, pg, errs

    # S0: бекенду нема (404) — нічого не змінюється, поля нема
    ctx, pg, errs = newpage({"9601": 1, "9602": 1}, backend=False)
    pg.goto(f"{BASE}/cart.html", wait_until="load")
    pg.wait_for_timeout(900)
    v = pg.evaluate(VIEW)
    chk(f"S0 нема бекенду знижок (404): поля промокоду й рядка знижки нема, суми за каталогом (500 ₴) {v}", not v["promo_vis"] and not v["disc_vis"] and v["total"] == "500 ₴" and v["goods"] == "500 ₴" and errs == [])
    ctx.close()

    # S1: один товар, без коду — поле є, знижки нема
    ctx, pg, errs = newpage({"9601": 1})
    pg.goto(f"{BASE}/cart.html", wait_until="load")
    pg.wait_for_timeout(900)
    v = pg.evaluate(VIEW)
    chk(f"S1 один товар: поле «Промокод» є (бекенд відповів), знижки нема, 200 ₴ {v}", v["promo_vis"] and not v["disc_vis"] and v["total"] == "200 ₴" and errs == [])

    # S4: код без урахування регістру
    pg.fill("#f-promo", "PlUtUs5")
    pg.click("#promo-apply")
    pg.wait_for_timeout(700)
    v = pg.evaluate(VIEW)
    chk(f"S4 код «PlUtUs5» (регістр довільний): рядок «Знижка −5% (PLUTUS5)» −10 ₴, разом 190 ₴, «застосована до 1 із 1 позицій» {v}", v["disc_vis"] and v["disc_lbl"] == "Знижка −5% (PLUTUS5)" and v["disc"] == "−10 ₴" and v["total"] == "190 ₴" and v["aside_total"] == "190 ₴" and v["goods"] == "200 ₴" and "Код застосовано" in v["msg"] and "до 1 із 1 позицій" in v["msg"])
    sent = reqs[-1]
    chk(f"у запиті quote: items [{{id,qty}}] і promo_code, без phone поки його не введено {sent}", sent["items"] == [{"id": "9601", "qty": 1}] and sent["promo_code"] == "PlUtUs5" and "phone" not in sent)
    # S5: невалідний код
    pg.fill("#f-promo", "ABCDEF")
    pg.click("#promo-apply")
    pg.wait_for_timeout(700)
    v = pg.evaluate(VIEW)
    chk(f"S5 невалідний код: «Код не діє.», знижки нема, 200 ₴ {v}", "Код не діє" in v["msg"] and not v["disc_vis"] and v["total"] == "200 ₴")
    # S9: телефон іде в quote, коли введений
    pg.fill("#f-phone", "0501112233")
    pg.dispatch_event("#f-phone", "change")
    pg.wait_for_timeout(700)
    chk(f"телефон іде в quote у форматі +380…: {reqs[-1].get('phone')}", reqs[-1].get("phone") == "+380501112233")
    ctx.close()

    # S2/S3/S6/S8: авто −10% при ≥2 різних товарах
    ctx, pg, errs = newpage({"9601": 1, "9602": 1})
    pg.goto(f"{BASE}/cart.html", wait_until="load")
    pg.wait_for_timeout(900)
    v = pg.evaluate(VIEW)
    chk(f"S2 два різні товари: авто «Знижка −10% (2 різні товари)» −50 ₴, разом 450 ₴ (і в бічній картці), ціни рядків перекреслені, «до 2 із 2 позицій» {v}",
        v["disc_vis"] and v["aside_disc_vis"] and v["disc_lbl"] == "Знижка −10% (2 різні товари)" and v["disc"] == "−50 ₴" and v["total"] == "450 ₴" and v["aside_total"] == "450 ₴" and v["goods"] == "500 ₴" and v["strikes"] == 2 and "до 2 із 2 позицій" in v["msg"])
    # S8: застаріла знижка не показується відразу після зміни кошика
    pg.click("#cart-body .cart-item[data-id='9601'] [data-act=inc]")
    stale = pg.evaluate(VIEW)
    chk(f"S8 після зміни кількості, до нової відповіді, старої знижки НЕ показуємо {stale}", not stale["disc_vis"] and stale["total"] == "700 ₴")
    pg.wait_for_timeout(700)
    v2 = pg.evaluate(VIEW)
    chk(f"…після нової відповіді сума перерахована (2×200+300=700, −10%: 360+270=630) {v2}", v2["disc_vis"] and v2["total"] == "630 ₴" and v2["disc"] == "−70 ₴")
    # S6: два товари + код → бере більша (мок: TWO10)
    pg.fill("#f-promo", "plutus5")
    pg.click("#promo-apply")
    pg.wait_for_timeout(700)
    v3 = pg.evaluate(VIEW)
    chk(f"S6 два товари + PLUTUS5: береться більша знижка (−10%), не сума {v3}", v3["disc_lbl"] == "Знижка −10% (2 різні товари)" and v3["total"] == "630 ₴")
    ctx.close()

    # S3: частина рядків без знижки
    ctx, pg, errs = newpage({"9601": 1, "9602": 1, "9610": 1})
    pg.goto(f"{BASE}/cart.html", wait_until="load")
    pg.wait_for_timeout(900)
    v = pg.evaluate(VIEW)
    chk(f"S3 один із трьох рядків без знижки (підлога): «до 2 із 3 позицій», разом 550 ₴ {v}", "до 2 із 3 позицій" in v["msg"] and v["total"] == "550 ₴" and v["strikes"] == 2)
    ctx.close()

    # S7: оформлення з кодом; 400 promo_used → причина + «Продовжити без коду»
    ctx, pg, errs = newpage({"9601": 1})
    pg.goto(f"{BASE}/cart.html", wait_until="load")
    pg.wait_for_timeout(800)
    pg.fill("#f-promo", "used")
    pg.click("#promo-apply")
    pg.wait_for_timeout(600)
    pg.evaluate("""(()=>{const set=(id,v)=>{const e=document.getElementById(id); if(e){ e.disabled=false; e.value=v; }};
        set('f-name','Іван Тестовий'); set('f-phone','+380501112233'); set('f-city','Київ'); set('f-warehouse','Відділення №1');
        const f=document.getElementById('checkout-form'); f.noValidate=true; f.requestSubmit();})()""")
    pg.wait_for_timeout(800)
    m = pg.evaluate("[document.getElementById('checkout-msg').textContent, !!document.querySelector('#checkout-msg button'), location.pathname.endsWith('cart.html'), document.getElementById('checkout-submit').disabled]")
    chk(f"S7 400 promo_used: замовлення не створено (лишаємось на кошику), причина й кнопка «Продовжити без коду», у /api/order був promo_code {m}", orders and orders[0].get("promo_code") == "used" and "уже використано" in m[0] and m[1] and m[2] and m[3] is False)
    pg.click("#checkout-msg button")
    pg.wait_for_timeout(700)
    v = pg.evaluate(VIEW)
    chk(f"«Продовжити без коду»: поле очищено, код не діє, знижки нема {v['total']}", pg.evaluate("document.getElementById('f-promo').value") == "" and not v["disc_vis"] and v["total"] == "200 ₴")
    n0 = len(orders)
    pg.evaluate("document.getElementById('checkout-form').requestSubmit()")
    pg.wait_for_timeout(900)
    chk(f"повторне оформлення БЕЗ коду: у /api/order нема promo_code, перехід на thanks.html {orders[n0:] and orders[n0].get('promo_code')} / {pg.url}", len(orders) == n0 + 1 and "promo_code" not in orders[n0] and pg.url.endswith("thanks.html") and errs == [])
    ctx.close()
    br.close()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
