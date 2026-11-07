#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dropt_cabinet.py — кабінет Dropt (07.11.2026). Офлайн: підроблений HTTP відтворює форму відповідей, ЗНЯТИХ З ЖИВОГО кабінету (cabinet-сторінка, модалка checkout/modal як JSON {"html"}, сторінка товару з current_params).
Інваріанти: (1) парсери (csrf, баланс, внутрішній id товару, рядки кошика, data-action); (2) ensure_session: протухла сесія → OrderError з інструкцією; (3) preflight лише читає (жодного POST), ловить: артикул не збігся, нема в наявності,
ціна зросла, мало балансу; (4) order_flow(submit=False) нічого не змінює; (5) РЕАЛЬНЕ оформлення закрите, доки DROPT_CABINET_VERIFIED≠1; (6) повний ланцюг: кошик без сторонніх → PDF → confirm з очікуваними полями; (7) сторонні рядки в кошику / кількість не та → відмова
БЕЗ POST; (8) помилка сервера (error / redirect_deposit) = «напевно не оформлено»; таймаут/незрозуміла відповідь = «невідомо»; (9) URL лише dropt.in.ua і без logout; (10) load_cookies."""
import json
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import dropt_cabinet as dc  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


CABINET = '<html><head><meta name="csrf-token" content="TOK123"></head><body><a href="https://dropt.in.ua/index.php?route=account/logout">Вийти</a><div>БАЛАНС ›\n₴1 250.50\nНА ХОЛДІ</div></body></html>'
CABINET_GUEST = "<html><head></head><body><a href='/login'>Увійти</a></body></html>"


def product_page(pid="19357", sku="59298", price="502.2", avail="http://schema.org/InStock"):
    return (f'<html><script type="application/ld+json">{{"@type":"Product","sku":"{sku}","offers":{{"@type":"Offer","price":"{price}","availability":"{avail}"}}}}</script>'
            f'<form><input type="hidden" name="current_params" value="product_id={pid}" /></form></html>')


def modal(rows=None, balance="1250.50", action="https://dropt.in.ua/index.php?route=checkout/dropshipper_checkout/confirm"):
    rows = rows if rows is not None else {"19357": 1}
    trs = "".join(f'<tr data-product-id="{k}"><td><input class="form-control quantity-input" data-product-id="{k}" value="{v}"></td></tr>' for k, v in rows.items())
    return json.dumps({"html": f'<form id="modal-checkout-form" data-action="{action}"><table>{trs}</table><label>Оплатити з балансу (доступно: {balance} грн)</label></form>'})


class FakeHttp:
    def __init__(self, cabinet=CABINET, rows=None, prod=None, confirm=None, upload=None, balance="1250.50", add_ok=True, post_exc=None):
        self.cabinet, self.rows, self.balance = cabinet, dict(rows or {}), balance
        self.prod = prod or {"https://dropt.in.ua/ua/tovar-a": product_page()}
        self.confirm = confirm or json.dumps({"success": True, "redirect": "/ua/cabinet?tab=orders", "ga4": {"transaction_id": "D-5551"}})
        self.upload = upload or json.dumps({"success": True, "filename": "ttn_abc.pdf", "ttn_number": "20451500000001"})
        self.add_ok, self.post_exc = add_ok, post_exc
        self.posts, self.gets = [], []

    def get(self, url, **kw):
        self.gets.append(url)
        if url.endswith("/ua/cabinet"):
            return 200, self.cabinet
        if "tab=orders" in url:
            return 200, "<table><tr><td>#7788</td><td>20451500000001</td></tr></table>"
        if "checkout/modal" in url:
            return 200, modal(self.rows, self.balance)
        if url in self.prod:
            return 200, self.prod[url]
        return 404, ""

    def post(self, url, data=None, files=None, headers=None):
        self.posts.append((url, dict(data or {}), bool(files)))
        if self.post_exc and "dropshipper_checkout/confirm" in url:
            raise self.post_exc
        if "cart/add" in url:
            if self.add_ok:
                self.rows[str(data["product_id"])] = int(data["quantity"])
            return 200, json.dumps({"success": "ok"} if self.add_ok else {"error": "x"})
        if "uploadTTN" in url:
            return 200, self.upload
        if "dropshipper_checkout/confirm" in url:
            return 200, self.confirm
        return 404, ""


tmp = Path(tempfile.mkdtemp())
pdf = tmp / "ttn.pdf"
pdf.write_bytes(b"%PDF-1.4 test")
ORDER = {"items": [{"vendor_code": "59298", "qty": 1, "cost": 500.0, "sale_price": 790, "source_url": "https://dropt.in.ua/ua/tovar-a"}], "ttn_number": "20451500000001", "marking_pdf": str(pdf)}

# (1) парсери
chk("parse_csrf / parse_balance (пробіли у тисячах) / parse_modal_balance", dc.parse_csrf(CABINET) == "TOK123" and dc.parse_balance(CABINET) == 1250.50 and dc.parse_modal_balance("доступно: 0.00 грн") == 0.0 and dc.parse_modal_balance("нема") is None)
p = dc.parse_product(product_page())
chk("parse_product: внутрішній id ≠ артикул, ціна, наявність", p == {"internal_id": "19357", "sku": "59298", "price": 502.2, "in_stock": True})
chk("parse_product: OutOfStock → in_stock False", dc.parse_product(product_page(avail="http://schema.org/OutOfStock"))["in_stock"] is False)
chk("parse_cart_rows / parse_confirm_url", dc.parse_cart_rows(json.loads(modal({"1": 2, "7": 1}))["html"]) == {"1": 2, "7": 1} and dc.parse_confirm_url(json.loads(modal())["html"]).endswith("dropshipper_checkout/confirm"))

# (2) сесія
h = FakeHttp()
chk("ensure_session: жива сесія → csrf і баланс", dc.ensure_session(h) == {"logged_in": True, "csrf": "TOK123", "balance": 1250.50})
try:
    dc.ensure_session(FakeHttp(cabinet=CABINET_GUEST))
    ok = False
except dc.OrderError as e:
    ok = "--login" in str(e)
chk("ensure_session: протухла сесія → OrderError з інструкцією --login", ok)

# (3) preflight лише читає
h = FakeHttp()
r = dc.preflight(ORDER, h)
chk("preflight: готово, внутрішній id знайдено, сума закупівлі, баланс; жодного POST", r["ready"] and r["ids"] == {"59298": "19357"} and r["total"] == 500.0 and r["balance"] == 1250.50 and not h.posts)
chk("preflight: артикул на сторінці ≠ замовленому → проблема", not dc.preflight(ORDER, FakeHttp(prod={"https://dropt.in.ua/ua/tovar-a": product_page(sku="11111")}))["ready"])
chk("preflight: нема в наявності → проблема", any("нема в наявності" in x for x in dc.preflight(ORDER, FakeHttp(prod={"https://dropt.in.ua/ua/tovar-a": product_page(avail="OutOfStock")}))["problems"]))
chk("preflight: ціна «Дроп» зросла (700 ≫ 500) → проблема; ±2 % і 1 грн — ні", any("зросла" in x for x in dc.preflight(ORDER, FakeHttp(prod={"https://dropt.in.ua/ua/tovar-a": product_page(price="700")}))["problems"])
    and dc.preflight(ORDER, FakeHttp(prod={"https://dropt.in.ua/ua/tovar-a": product_page(price="506")}))["ready"])
lb = dc.preflight(ORDER, FakeHttp(cabinet=CABINET.replace("1 250.50", "100.00")))
chk("preflight: мало балансу → low_balance, ready False", lb["low_balance"] and not lb["ready"])
bad_order = dict(ORDER, items=[dict(ORDER["items"][0], source_url="https://evil.example/x")])
chk("preflight: source_url не з dropt.in.ua → проблема", not dc.preflight(bad_order, FakeHttp())["ready"])
for bad in ([], [dict(ORDER["items"][0], qty=0)], [dict(ORDER["items"][0], cost=0)], [dict(ORDER["items"][0], vendor_code="")]):
    try:
        dc.preflight(dict(ORDER, items=bad), FakeHttp())
        ok = False
    except dc.OrderError:
        ok = True
    if not ok:
        break
chk("preflight: порожнє замовлення / qty 0 / cost 0 / без артикулу → OrderError", ok)

# (4) submit=False
h = FakeHttp()
r = dc.order_flow(ORDER, submit=False, http=h)
chk("order_flow(submit=False): лише читання, нічого не змінює", not h.posts and r["submitted"] is False and r["ready"] is True)

# (5) закрито, доки не підтверджено
os.environ.pop("DROPT_CABINET_VERIFIED", None)
chk("final_implemented: за замовчуванням False", dc.final_implemented() is False)
h = FakeHttp()
try:
    dc.order_flow(ORDER, submit=True, http=h)
    ok = False
except dc.OrderError as e:
    ok = "DROPT_CABINET_VERIFIED" in str(e) and not h.posts
chk("order_flow(submit=True) без DROPT_CABINET_VERIFIED=1 → OrderError, жодного POST", ok)

# (6) повний ланцюг
os.environ["DROPT_CABINET_VERIFIED"] = "1"
h = FakeHttp()
r = dc.order_flow(ORDER, submit=True, http=h)
urls = [u.split("route=")[-1] for u, _d, _f in h.posts]
chk("повний ланцюг: cart/add → uploadTTN → confirm; submitted, номер замовлення з ga4.transaction_id", urls == ["checkout/cart/add", "account/dropshipper/uploadTTN", "checkout/dropshipper_checkout/confirm"] and r["submitted"] and r["dropt_order_ref"] == "D-5551" and r["problems"] == [])
conf = h.posts[-1][1]
chk("confirm: external, наша ТТН, файл з відповіді, sale_price[внутр. id], pay_from_balance, csrf", conf["delivery_type"] == "external" and conf["ttn_number"] == "20451500000001" and conf["ttn_filename"] == "ttn_abc.pdf"
    and conf["sale_price[19357]"] == "790" and conf["total_order_price"] == "790" and conf["pay_from_balance"] == "1" and conf["payment_choice"] == "balance" and conf["csrf_token"] == "TOK123" and conf["order_type"] == "drop")
chk("cart/add: product_id = внутрішній id (19357), не артикул", h.posts[0][1] == {"product_id": "19357", "quantity": 1})
h = FakeHttp(rows={"19357": 1})
r = dc.order_flow(ORDER, submit=True, http=h)
chk("кошик уже містить наш рядок з тією ж кількістю → повторно не додаємо", [u.split("route=")[-1] for u, _d, _f in h.posts][0] == "account/dropshipper/uploadTTN" and r["submitted"])
h = FakeHttp(confirm=json.dumps({"success": True}))
r = dc.order_flow(ORDER, submit=True, http=h)
chk("відповідь без ga4: номер із списку замовлень за ТТН (#7788)", r["submitted"] and r["dropt_order_ref"] == "7788")

# (7) кошик
h = FakeHttp(rows={"555": 1})
try:
    dc.order_flow(ORDER, submit=True, http=h)
    ok = False
except dc.OrderError as e:
    ok = "сторонні" in str(e) and not h.posts
chk("сторонні рядки в кошику → OrderError «очистіть кошик», жодного POST (чужий кошик не чіпаємо)", ok)
h = FakeHttp(rows={"19357": 3})
try:
    dc.order_flow(ORDER, submit=True, http=h)
    ok = False
except dc.OrderError:
    ok = not h.posts
chk("наш товар у кошику з іншою кількістю → OrderError без POST", ok)
h = FakeHttp(add_ok=False)
try:
    dc.order_flow(ORDER, submit=True, http=h)
    ok = False
except dc.OrderError:
    ok = all("confirm" not in u for u, _d, _f in h.posts)
chk("cart/add відхилено → OrderError, confirm не викликано", ok)

# (8) відповіді сервера на confirm
h = FakeHttp(confirm=json.dumps({"error": "Недостатньо коштів на балансі", "redirect_deposit": "/ua/deposit"}))
r = dc.order_flow(ORDER, submit=True, http=h)
chk("error + redirect_deposit → НЕ оформлено напевно, low_balance", r["submitted"] is False and r["not_submitted_certain"] is True and r["low_balance"] is True)
h = FakeHttp(confirm=json.dumps({"error": {"ttn_number": "Невірна ТТН"}}))
r = dc.order_flow(ORDER, submit=True, http=h)
chk("error-словник → НЕ оформлено напевно, текст поля у проблемах", r["not_submitted_certain"] is True and "ttn_number" in r["problems"][0])
h = FakeHttp(post_exc=TimeoutError("t"))
r = dc.order_flow(ORDER, submit=True, http=h)
chk("таймаут POST confirm → результат НЕВІДОМИЙ (not_submitted_certain False), runner лишить rt_ordering", r["submitted"] is False and r["not_submitted_certain"] is False)
h = FakeHttp(confirm="<html>502</html>")
r = dc.order_flow(ORDER, submit=True, http=h)
chk("не-JSON відповідь на confirm → невідомо", r["submitted"] is False and r["not_submitted_certain"] is False)
h = FakeHttp(upload=json.dumps({"success": True, "filename": "f.pdf", "ttn_number": "20451599999999"}))
try:
    dc.order_flow(ORDER, submit=True, http=h)
    ok = False
except dc.OrderError as e:
    ok = "розпізнав" in str(e) and all("confirm" not in u for u, _d, _f in h.posts)
chk("Dropt розпізнав у PDF іншу ТТН → OrderError, confirm не викликано", ok)
h = FakeHttp(upload=json.dumps({"success": False, "error": "не PDF"}))
try:
    dc.order_flow(ORDER, submit=True, http=h)
    ok = False
except dc.OrderError:
    ok = all("confirm" not in u for u, _d, _f in h.posts)
chk("завантаження PDF відхилено → OrderError, confirm не викликано", ok)
bad_pdf = tmp / "bad.pdf"
bad_pdf.write_bytes(b"not pdf")
try:
    dc.order_flow(dict(ORDER, marking_pdf=str(bad_pdf)), submit=True, http=FakeHttp())
    ok = False
except dc.OrderError:
    ok = True
chk("наліпка не PDF → OrderError ДО будь-яких змін", ok)
h = FakeHttp(cabinet=CABINET.replace("1 250.50", "100.00"))
r = dc.order_flow(ORDER, submit=True, http=h)
chk("мало балансу в preflight при submit=True → не оформлено напевно, жодного POST", r["not_submitted_certain"] is True and r["low_balance"] is True and not h.posts)
os.environ.pop("DROPT_CABINET_VERIFIED", None)

# (9) URL
chk("_url_ok: лише https://dropt.in.ua, без logout", dc._url_ok("https://dropt.in.ua/ua/cabinet") and not dc._url_ok("http://dropt.in.ua/x") and not dc._url_ok("https://evil.example/") and not dc._url_ok("https://dropt.in.ua/index.php?route=account/logout")
    and not dc._url_ok("https://dropt.in.ua.evil.com/"))

# (10) куки
sf = tmp / "state.json"
sf.write_text(json.dumps({"cookies": [{"name": "OCSESSID", "value": "abc", "domain": "dropt.in.ua", "path": "/"}, {"name": "x", "value": "y", "domain": "google.com", "path": "/"}]}), encoding="utf-8")
ck = dc.load_cookies(sf)
chk("load_cookies: беремо куки лише dropt.in.ua", [c["name"] for c in ck] == ["OCSESSID"])
for content in ("{не json", json.dumps({"cookies": [{"name": "x", "value": "y", "domain": "google.com"}]})):
    sf.write_text(content, encoding="utf-8")
    try:
        dc.load_cookies(sf)
        ok = False
    except dc.OrderError as e:
        ok = "--login" in str(e)
    if not ok:
        break
chk("load_cookies: битий файл / нема куків Dropt → OrderError з інструкцією", ok)
try:
    dc.load_cookies(tmp / "nonexistent.json")
    ok = False
except dc.OrderError:
    ok = True
chk("load_cookies: нема файлу → OrderError", ok)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
