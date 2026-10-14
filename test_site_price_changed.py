#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_price_changed.py — 14.10.2026 (Code-Agent #759): клієнт шле в POST /api/order ОПЦІЙНЕ поле expected_total (ціле грн) = сума, яку показав кошик у момент сабміту. Якщо index.json змінився між «У кошик» і оплатою,
сервер відповідає 409 {code:"price_changed", expected_total, total, lines:[{id,name,qty,price}]} БЕЗ створення замовлення: показуємо «Ціни змінились» з було/стало, оновлюємо ціни кошика, нова сума, покупець підтверджує ще раз.
Chromium + мок /api/order."""
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
CART = {"9701": {"id": "9701", "name": "Іграшка А", "price": 200, "photo": "", "qty": 1}, "9702": {"id": "9702", "name": "Іграшка Б", "price": 300, "photo": "", "qty": 2}}
SUBMIT = """(()=>{const set=(id,v)=>{const e=document.getElementById(id); if(e){ e.disabled=false; e.value=v; }};
    set('f-name','Іван Тестовий'); set('f-phone','+380501112233'); set('f-city','Київ'); set('f-warehouse','Відділення №1');
    const f=document.getElementById('checkout-form'); f.noValidate=true; f.requestSubmit();})()"""
VIEW = "(()=>{const t=id=>{const e=document.getElementById(id); return e? e.textContent.trim():null}; return {total:t('sum-total'), aside:t('aside-total'), msg:t('checkout-msg'), dis:document.getElementById('checkout-submit').disabled, path:location.pathname, cart:JSON.parse(localStorage.getItem('pt_cart_v1')||'{}')}})()"

with sync_playwright() as pw:
    br = pw.chromium.launch()

    def newpage(responses):
        ctx = br.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
        pg = ctx.new_page()
        errs, orders = [], []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("**/stock.json*", lambda r: r.abort())
        pg.add_init_script("if(!sessionStorage.getItem('seeded')){ localStorage.setItem('pt_cart_v1', JSON.stringify(%s)); sessionStorage.setItem('seeded','1'); }" % json.dumps(CART, ensure_ascii=False))
        seq = list(responses)

        def o(route, request):
            orders.append(request.post_data_json)
            status, body = seq.pop(0) if len(seq) > 1 else seq[0]
            route.fulfill(status=status, content_type="application/json", body=json.dumps(body, ensure_ascii=False))

        pg.route("**/api/order", o)
        return ctx, pg, errs, orders

    # 1. звичайний успіх: expected_total — ціле = показана сума; сума 200 + 2×300 = 800
    ctx, pg, errs, orders = newpage([(200, {"order_id": "PT-T-1"})])
    pg.goto(f"{BASE}/cart.html", wait_until="load")
    pg.wait_for_timeout(700)
    shown = pg.evaluate("document.getElementById('sum-total').textContent.trim()")
    pg.evaluate(SUBMIT)
    pg.wait_for_url("**/thanks.html", timeout=5000)
    et = orders[0].get("expected_total")
    chk(f"payload: expected_total — ЦІЛЕ число (не рядок, без копійок/пробілів) = сума з кошика ({shown}) → {et!r}", shown == "800 ₴" and et == 800 and isinstance(et, int) and not isinstance(et, bool) and errs == [])
    ctx.close()

    # 2–3. 409 price_changed → повідомлення, оновлені ціни, нова сума, повторне підтвердження
    NEW = {"code": "price_changed", "error": "Ціни змінились.", "expected_total": 800, "total": 810, "lines": [{"id": "9701", "name": "Іграшка А", "qty": 1, "price": 210}, {"id": "9702", "name": "Іграшка Б", "qty": 2, "price": 300}]}
    ctx, pg, errs, orders = newpage([(409, NEW), (200, {"order_id": "PT-T-2"})])
    pg.goto(f"{BASE}/cart.html", wait_until="load")
    pg.wait_for_timeout(700)
    pg.evaluate(SUBMIT)
    pg.wait_for_timeout(900)
    v = pg.evaluate(VIEW)
    chk(f"409 price_changed: замовлення не створено (лишаємось на кошику), повідомлення «Ціни змінились» з було/стало для зміненого товару й новою сумою, кнопка активна {v['msg']}",
        v["path"].endswith("cart.html") and "Ціни змінились" in v["msg"] and "Іграшка А — було 200 ₴, тепер 210 ₴" in v["msg"] and "Нова сума: 810 ₴" in v["msg"] and "Іграшка Б" not in v["msg"] and v["dis"] is False)
    chk(f"ціни кошика оновлено (А → 210, Б без змін), підсумок у кошику й бічній картці — 810 ₴ {v['total']} / {v['aside']}", v["cart"]["9701"]["price"] == 210 and v["cart"]["9702"]["price"] == 300 and v["total"] == "810 ₴" and v["aside"] == "810 ₴")
    pg.evaluate(SUBMIT)
    pg.wait_for_url("**/thanks.html", timeout=5000)
    chk(f"повторне підтвердження: expected_total = НОВА сума ({orders[1].get('expected_total')}), замовлення пройшло", orders[0].get("expected_total") == 800 and orders[1].get("expected_total") == 810 and errs == [])
    ctx.close()

    # 4. зіпсована відповідь price_changed — без винятку, кошик не псується
    for label, body in (("lines null", {"code": "price_changed", "lines": None, "total": 810}), ("lines сміття", {"code": "price_changed", "lines": [None, 5, {"id": "9701", "price": "abc"}, {"id": "9701", "price": -3}, {"id": "9999", "price": 1}], "total": "x"}), ("без полів", {"code": "price_changed"})):
        ctx, pg, errs, orders = newpage([(409, body)])
        pg.goto(f"{BASE}/cart.html", wait_until="load")
        pg.wait_for_timeout(600)
        pg.evaluate(SUBMIT)
        pg.wait_for_timeout(800)
        v = pg.evaluate(VIEW)
        chk(f"зіпсована відповідь price_changed ({label}): без винятку, ціни кошика не зіпсовано (А=200, Б=300), повідомлення є, кнопка активна", errs == [] and v["cart"]["9701"]["price"] == 200 and v["cart"]["9702"]["price"] == 300 and "Ціни змінились" in v["msg"] and v["dis"] is False)
        ctx.close()
    br.close()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
