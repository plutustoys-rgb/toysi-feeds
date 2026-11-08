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
        if "header_dynamic" in url:                            # як на живому сайті: кешована сторінка має порожній csrf-meta, токен — тут
            return 200, json.dumps({"logged": "account/logout" in self.cabinet, "csrf_token": "TOK123", "username": "x"})
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

# ---- аудит #1118: посилення ----
def cart_html(inner):
    return f"<table>{inner}</table>"


def raises_oe(fn):
    try:
        fn()
        return False
    except dc.OrderError:
        return True


chk("parse_cart_rows: рядок без кількості → OrderError (раніше читалось як 1)", raises_oe(lambda: dc.parse_cart_rows(cart_html('<tr data-product-id="7"><td>x</td></tr>'))))
chk("parse_cart_rows: <TR> верхній регістр / id без лапок читаються; value=\"2.0\" → OrderError (не ціле)",
    dc.parse_cart_rows(cart_html('<TR DATA-PRODUCT-ID=7><td><input class="quantity-input" value="3"></td></TR>')) == {"7": 3} and raises_oe(lambda: dc.parse_cart_rows(cart_html('<tr data-product-id="7"><td><input class="quantity-input" value="2.0"></td></tr>'))))
chk("parse_cart_rows: дубль id → OrderError; порожній кошик → {}", raises_oe(lambda: dc.parse_cart_rows(cart_html('<tr data-product-id="7"><td><input class="quantity-input" value="1"></td></tr>' * 2))) and dc.parse_cart_rows("<table></table>") == {})


def ld(price, sku="59298", extra=""):
    return f'<html>{extra}<script type="application/ld+json">{{"@type":"Product","sku":"{sku}","offers":{{"price":"{price}","availability":"https://schema.org/InStock"}}}}</script><input name="current_params" value="product_id=19357"></html>'


chk("parse_product: ціна «1 250.50» / «1250,50» (NBSP, кома) читається коректно", dc.parse_product(ld("1 250.50"))["price"] == 1250.50 and dc.parse_product(ld("1250,50"))["price"] == 1250.50 and dc.parse_product(ld("1 250,50"))["price"] == 1250.50)
chk("parse_product: ціну/sku беремо з JSON-LD Product, а не з першого збігу на сторінці (data-layer з іншою ціною раніше)", dc.parse_product(ld("500", extra='<script>dataLayer.push({"price":"9","sku":"WRONG"})</script>')) == {"internal_id": "19357", "sku": "59298", "price": 500.0, "in_stock": True})
chk("parse_product: без JSON-LD → price/sku None (preflight не вважатиме це «ок»)", dc.parse_product("<html></html>")["price"] is None)
no_price = FakeHttp(prod={"https://dropt.in.ua/ua/tovar-a": '<html><script type="application/ld+json">{"@type":"Product","sku":"59298","offers":{"availability":"InStock"}}</script><input name="current_params" value="product_id=19357"></html>'})
chk("preflight: ціна на сторінці не прочитана → проблема (раніше мовчки пропускалось)", any("не прочитано" in x for x in dc.preflight(ORDER, no_price)["problems"]))

# URL
chk("_url_ok: evil.com\\@dropt.in.ua, подвійне кодування logout, пробіли, userinfo, інший порт — відхилено",
    not dc._url_ok("https://evil.com\\@dropt.in.ua/x") and not dc._url_ok("https://dropt.in.ua/index.php?route=account/l%6Fgout") and not dc._url_ok("https://dropt.in.ua/index.php?route=account/l%256Fgout")
    and not dc._url_ok("https://dropt.in.ua/a b") and not dc._url_ok("https://user:pw@dropt.in.ua/x") and not dc._url_ok("https://dropt.in.ua:8443/x") and dc._url_ok("https://dropt.in.ua/ua/cabinet?tab=orders"))
chk("_product_url_ok: лише /ua/<slug> без підкаталогів/параметрів", dc._product_url_ok("https://dropt.in.ua/ua/tovar-a-1") and not dc._product_url_ok("https://dropt.in.ua/ua/a/b") and not dc._product_url_ok("https://dropt.in.ua/index.php?route=account/logout")
    and not dc._product_url_ok("https://dropt.in.ua/ua/x?y=1") and not dc._product_url_ok("https://dropt.in.ua/ru/x"))
bad_logout = dict(ORDER, items=[dict(ORDER["items"][0], source_url="https://dropt.in.ua/index.php?route=account/logout")])
h = FakeHttp()
chk("preflight: source_url = logout → проблема, жодного запиту на нього", not dc.preflight(bad_logout, h)["ready"] and all("logout" not in u for u in h.gets))


class Resp:
    def __init__(self, code, text="", loc=None):
        self.status_code, self.text, self.headers = code, text, ({"Location": loc} if loc else {})


class Sess:
    def __init__(self, table):
        self.table, self.seen = table, []

    def get(self, url, timeout=None, allow_redirects=True, **kw):
        self.seen.append((url, allow_redirects))
        return self.table[url]


def make_http(table):
    hh = object.__new__(dc.Http)
    hh.s = Sess(table)
    return hh


hh = make_http({"https://dropt.in.ua/ua/a": Resp(302, loc="https://dropt.in.ua/index.php?route=account/logout")})
try:
    hh.get("https://dropt.in.ua/ua/a")
    ok = False
except dc.OrderError:
    ok = [u for u, _ in hh.s.seen] == ["https://dropt.in.ua/ua/a"]
chk("Http.get: редирект на logout НЕ виконується (перевірка до запиту)", ok)
hh = make_http({"https://dropt.in.ua/ua/a": Resp(301, loc="https://evil.example/x")})
try:
    hh.get("https://dropt.in.ua/ua/a")
    ok = False
except dc.OrderError:
    ok = [u for u, _ in hh.s.seen] == ["https://dropt.in.ua/ua/a"]
chk("Http.get: редирект на чужий хост НЕ виконується", ok)
hh = make_http({"https://dropt.in.ua/ua/a": Resp(302, loc="/ua/b"), "https://dropt.in.ua/ua/b": Resp(200, "OK")})
chk("Http.get: нормальний редирект у межах хоста — слідуємо вручну (allow_redirects=False)", hh.get("https://dropt.in.ua/ua/a") == (200, "OK") and all(a is False for _, a in hh.s.seen))
loop = {"https://dropt.in.ua/ua/a": Resp(302, loc="/ua/a")}
chk("Http.get: нескінченний редирект → OrderError", raises_oe(lambda: make_http(loop).get("https://dropt.in.ua/ua/a")))

# confirm: нестандартні відповіді
os.environ["DROPT_CABINET_VERIFIED"] = "1"
for body, label in ((json.dumps({"error": ["x"]}), "error=список"), (json.dumps({"error": True}), "error=true")):
    r = dc.order_flow(ORDER, submit=True, http=FakeHttp(confirm=body))
    chk(f"confirm {label}: без винятків, «не оформлено напевно»", r["submitted"] is False and r["not_submitted_certain"] is True)
r = dc.order_flow(ORDER, submit=True, http=FakeHttp(confirm=json.dumps({"success": True, "error": "попередження"})))
chk("confirm success+error одночасно → результат НЕВІДОМИЙ (щоб runner не повторив і не оплатив вдруге)", r["submitted"] is False and r["not_submitted_certain"] is False)


class RacyHttp(FakeHttp):
    def post(self, url, data=None, files=None, headers=None):
        res = super().post(url, data=data, files=files, headers=headers)
        if "uploadTTN" in url:
            self.rows["555"] = 1                               # власник додав товар у кошик під час завантаження PDF
        return res


h = RacyHttp()
try:
    dc.order_flow(ORDER, submit=True, http=h)
    ok = False
except dc.OrderError as e:
    ok = "змінився" in str(e) and all("dropshipper_checkout/confirm" not in u for u, _d, _f in h.posts)
chk("кошик змінився між читанням і confirm (власник додав товар) → оформлення зупинено, confirm не викликано", ok)
os.environ.pop("DROPT_CABINET_VERIFIED", None)
chk("_fmt: 790 → «790», 790.5 → «790.5», 1000000 → «1000000», 12345.67 → «12345.67»", dc._fmt(790) == "790" and dc._fmt(790.5) == "790.5" and dc._fmt(1000000) == "1000000" and dc._fmt(12345.67) == "12345.67")
sf2 = tmp / "state2.json"
sf2.write_text(json.dumps({"cookies": [{"name": "a", "value": "1", "domain": "evildropt.in.ua"}, {"name": "b", "value": "1", "domain": ".dropt.in.ua"}, {"name": "c", "value": "1", "domain": "dropt.in.ua"}]}), encoding="utf-8")
chk("load_cookies: evildropt.in.ua відхилено; dropt.in.ua і .dropt.in.ua — ні", [c["name"] for c in dc.load_cookies(sf2)] == ["b", "c"])

# ---- login(): фікс 07.11 («interrupted by another navigation» губив сесію власника) ----
import builtins  # noqa: E402
import types  # noqa: E402


def run_login(html_or_exc, status=200, close_raises=False, redirects=None):
    """Підроблений Playwright. goto дозволено РІВНО раз (відкриття кабінету); другий goto, як у житті, падає. Перевірка входу — через ctx.request.get."""
    calls = {"goto": 0, "req": 0}
    state = {"cookies": [{"name": "s", "value": "1", "domain": "dropt.in.ua"}, {"name": "g", "value": "1", "domain": ".google.com"}], "origins": [{"origin": "x"}]}

    class Resp:
        def __init__(self, st=None, loc=None):
            self.status = st or status
            self.headers = {"location": loc} if loc else {}

        def text(self):
            return html_or_exc

    class Req:
        def get(self, url, timeout=None, max_redirects=None):
            calls["req"] += 1
            calls.setdefault("urls", []).append(url)
            assert max_redirects == 0                           # автоматичні редіректи Playwright вимкнено
            if isinstance(html_or_exc, Exception):
                raise html_or_exc
            if redirects and url in redirects:
                return Resp(302, redirects[url])
            return Resp()

    class Page:
        def goto(self, url, timeout=None):
            calls["goto"] += 1
            if calls["goto"] > 1:
                raise RuntimeError("Page.goto: Navigation is interrupted by another navigation")

        def wait_for_load_state(self, *a, **k):
            raise RuntimeError("timeout")                       # навіть таймаут очікування не валить вхід

    class Ctx:
        request = Req()

        def new_page(self):
            return Page()

        def storage_state(self):
            return state

    class Browser:
        def new_context(self):
            return Ctx()

        def close(self):
            if close_raises:
                raise RuntimeError("Connection closed")

    class PW:
        class chromium:
            @staticmethod
            def launch(headless=False):
                return Browser()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    m = types.ModuleType("playwright.sync_api")
    m.sync_playwright = lambda: PW()
    pkg = types.ModuleType("playwright")
    old = {k: sys.modules.get(k) for k in ("playwright", "playwright.sync_api")}
    sys.modules["playwright"], sys.modules["playwright.sync_api"] = pkg, m
    old_state, old_input = dc.STATE_FILE, builtins.input
    target = tmp / "login_state.json"
    if target.exists():
        target.unlink()
    dc.STATE_FILE = target
    builtins.input = lambda *a: ""
    try:
        rc = dc.login()
    finally:
        builtins.input = old_input
        dc.STATE_FILE = old_state
        for k, v in old.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    saved = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
    return rc, saved, calls


rc, saved, calls = run_login(CABINET)
chk("login: вхід є → сесія збережена, лише куки dropt.in.ua, без origins", rc == 0 and saved is not None and [c["name"] for c in saved["cookies"]] == ["s"] and saved["origins"] == [])
chk("login: повторного page.goto після Enter НЕМАЄ (було причиною краху), перевірка — запитом із куками", calls["goto"] == 1 and calls["req"] == 1)
rc, saved, calls = run_login(CABINET_GUEST)
chk("login: у відповіді нема ознак входу → rc 1, файл сесії НЕ створено", rc == 1 and saved is None)
rc, saved, calls = run_login(CABINET, status=302)
chk("login: статус не 200 → rc 1, сесію не збережено", rc == 1 and saved is None)
rc, saved, calls = run_login(RuntimeError("net down"))
chk("login: збій перевірки (мережа) → rc 1 без винятку, сесію не збережено", rc == 1 and saved is None)
rc, saved, calls = run_login(CABINET, redirects={"https://dropt.in.ua/ua/cabinet": "https://dropt.in.ua/index.php?route=account/logout"})
chk("login: редірект кабінету на logout НЕ виконується (запиту на logout нема), сесію не збережено", rc == 1 and saved is None and all("logout" not in u for u in calls["urls"]))
rc, saved, calls = run_login(CABINET, redirects={"https://dropt.in.ua/ua/cabinet": "https://evil.example/ua/cabinet"})
chk("login: редірект на чужий хост НЕ виконується, сесію не збережено", rc == 1 and saved is None and calls["urls"] == ["https://dropt.in.ua/ua/cabinet"])
rc, saved, calls = run_login(CABINET, redirects={"https://dropt.in.ua/ua/cabinet": "/ua/cabinet/"})
chk("login: звичайний редірект у межах dropt.in.ua слідуємо вручну → вхід підтверджено, сесія збережена", rc == 0 and saved is not None and calls["urls"] == ["https://dropt.in.ua/ua/cabinet", "https://dropt.in.ua/ua/cabinet/"])
rc, saved, calls = run_login(CABINET, redirects={"https://dropt.in.ua/ua/cabinet": "/ua/cabinet"})
chk("login: нескінченний редірект → rc 1, сесію не збережено", rc == 1 and saved is None)
rc, saved, calls = run_login(CABINET, close_raises=True)
chk("login: browser.close() кидає виняток (вікно закрито) → збережена сесія НЕ втрачена, rc 0", rc == 0 and saved is not None)

# ---- 08.11: живий кабінет кешований, csrf-meta порожній, токен — з common/header_dynamic ----
LIVE_CABINET = CABINET.replace('content="TOK123"', 'content=""')


class DynHttp(FakeHttp):
    def __init__(self, dyn_body, dyn_code=200, **kw):
        super().__init__(**kw)
        self.dyn_body, self.dyn_code = dyn_body, dyn_code

    def get(self, url, **kw):
        if "header_dynamic" in url:
            self.gets.append(url)
            return self.dyn_code, self.dyn_body
        return super().get(url, **kw)


ok_dyn = json.dumps({"logged": True, "csrf_token": "LIVE" + "x" * 60, "username": "u", "logout_url": "https://dropt.in.ua/index.php?route=account/logout"})
chk("session_info: ПОРОЖНІЙ csrf-meta на сторінці + токен у header_dynamic → сесія жива, csrf з header_dynamic",
    dc.session_info(DynHttp(ok_dyn, cabinet=LIVE_CABINET))["logged_in"] is True and dc.session_info(DynHttp(ok_dyn, cabinet=LIVE_CABINET))["csrf"] == "LIVE" + "x" * 60)
chk("session_info: header_dynamic каже logged=false при посиланні logout на сторінці → НЕ залогінений", dc.session_info(DynHttp(json.dumps({"logged": False, "csrf_token": "t"}), cabinet=LIVE_CABINET))["logged_in"] is False)
chk("session_info: header_dynamic не JSON / 500 / порожній токен → НЕ залогінений (fail-closed)",
    all(dc.session_info(DynHttp(b, c, cabinet=LIVE_CABINET))["logged_in"] is False for b, c in (("<html>", 200), (ok_dyn, 500), (json.dumps({"logged": True, "csrf_token": ""}), 200), ("[]", 200))))
chk("session_info: запит header_dynamic ходить лише на dropt.in.ua без logout (_url_ok)", dc._url_ok(dc.DYNAMIC_URL))
h = DynHttp(ok_dyn, cabinet=LIVE_CABINET)
os.environ["DROPT_CABINET_VERIFIED"] = "1"
try:
    dc.order_flow(ORDER, submit=True, http=h)
except dc.OrderError:
    pass
os.environ.pop("DROPT_CABINET_VERIFIED", None)
chk("order_flow на живій формі сторінки: csrf у POST береться з header_dynamic, не порожній", any(d.get("csrf_token") == "LIVE" + "x" * 60 for _u, d, _f in h.posts))

# ---- 08.11: розмітка балансу з ЖИВОГО кабінету (зняти з живої сторінки: змішаний регістр, теги всередині) ----
LIVE_BAL = '<span id="cart-total">1 товар(ів) - ₴502.20</span><span class="ds-hero-bal__item"><span class="ds-hero-lbl">Баланс <i class="ds-hero-arr">›</i></span><b class="ds-hero-money">₴0.00</b></span><span class="ds-hero-bal__item"><span class="ds-hero-lbl">На холді</span><b class="ds-hero-money ds-hero-money--hold">₴7.00</b></span>'
chk("parse_balance: жива розмітка «Баланс <i>›</i></span><b>₴0.00</b>» → 0.0 (а не ₴ кошика 502.2 і не «на холді»)", dc.parse_balance(LIVE_BAL) == 0.0)
chk("parse_balance: «Баланс ... ₴1 250.50» з пробілом-розділювачем і тегами → 1250.5; старий формат «БАЛАНС ›\n₴1 250.50» теж", dc.parse_balance(LIVE_BAL.replace("₴0.00", "₴1&nbsp;250.50").replace("&nbsp;", " ")) == 1250.5 and dc.parse_balance(CABINET) == 1250.5)
chk("parse_balance: нема блоку балансу → None", dc.parse_balance("<html>₴5</html>") is None)
chk("parse_modal_balance: живо «доступно: <strong id=\"user-balance\">0.00 грн</strong>» → 0.0; старий формат без тегів теж", dc.parse_modal_balance('Оплатити з балансу (доступно: <strong id="user-balance">0.00 грн</strong>)') == 0.0 and dc.parse_modal_balance("(доступно: 1 250.50 грн)") == 1250.5)

chk("session_info: logged як РЯДОК («true»/«false») або число → НЕ залогінений (лише булів true)", all(dc.session_info(DynHttp(json.dumps({"logged": v, "csrf_token": "t" * 64}), cabinet=LIVE_CABINET))["logged_in"] is False for v in ("true", "false", 1, "1")))
chk("parse_balance: блок «Баланс» без власного ₴, а далі ₴ кошика/«на холді» → None, а не чуже число", dc.parse_balance('<a>Баланс</a><div><span>Кошик</span><b>₴502.20</b></div>') is None and dc.parse_balance('<span>Баланс</span><span>На холді</span><b>₴7.00</b>') is None)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
