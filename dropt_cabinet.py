# -*- coding: utf-8 -*-
"""dropt_cabinet.py — кабінет дропшипера Dropt (https://dropt.in.ua/ua/cabinet) для dropt_order_runner.py.

КОНТРАКТ ПРОЧИТАНО ЖИВИМ ЧИТАННЯМ кабінету власника (07.11.2026, нічого не замовлялось і не завантажувалось; скрипти checkout_modal*.js, GET модалки):
  • вхід — Google/Telegram, пароля нема → автовходу НЕ робимо. Власник один раз входить у вікні Playwright (`python dropt_cabinet.py --login`), сесія зберігається у файл (0600) і копіюється на VPS;
  • GET /ua/cabinet → `<meta name="csrf-token">`, баланс («БАЛАНС … ₴N»), посилання `route=account/logout` (ознака входу);
  • внутрішній id товару ≠ артикул: сторінка товару містить `<input name="current_params" value="product_id=19357">` (артикул 59298 — у JSON-LD `sku`);
  • кошик: POST index.php?route=checkout/cart/add {product_id, quantity}; кошик у акаунті ОДИН, спільний із ручною роботою власника → стороннього НЕ чіпаємо (є сторонні рядки → відмова з поясненням);
  • GET index.php?route=checkout/modal → JSON {"html": …}: форма #modal-checkout-form (data-action = POST оформлення `checkout/dropshipper_checkout/confirm`), рядки tr[data-product-id], «Оплатити з балансу (доступно: N грн)»;
  • «Зовнішня ТТН»: POST index.php?route=account/dropshipper/uploadTTN (multipart `file` = PDF ≤2 МБ, `csrf_token`, X-Requested-With) → JSON {success, filename, ttn_number};
  • оформлення: POST multipart на data-action: order_type=drop, delivery_type=external, ttn_number, ttn_filename, sale_price[<id>]=<наша ціна>, total_order_price, pay_from_balance=1, payment_choice=balance, payment_method=prepaid, csrf_token;
    відповідь JSON: {error: "текст" | {поле: текст}, redirect_deposit} (мало коштів) або {success, redirect, ga4:{transaction_id}}.
НЕ ПІДТВЕРДЖЕНО ЖИВИМ ЗАМОВЛЕННЯМ: точний набір обов'язкових полів confirm для external, значення total_order_price при qty>1, формат номера замовлення у відповіді. Тому final_implemented() = False, доки хтось не поставить DROPT_CABINET_VERIFIED=1
після першого тестового замовлення власника (qty 1); помилки валідації від сервера повертаються як «НЕ оформлено напевно» (кошик/баланс не змінено).

Безпека: лише https://dropt.in.ua; жодних URL з logout; секрети/куки ніде не друкуються; `order_flow(submit=False)` нічого не змінює (лише читання)."""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
BASE = "https://dropt.in.ua"
HOST_OK = "dropt.in.ua"
STATE_FILE = Path(os.environ.get("DROPT_CABINET_STATE_FILE", str(HERE / ".secrets" / "dropt_cabinet_state.json")))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TIMEOUT = 45
MAX_PDF_BYTES = 2 * 1024 * 1024
PRICE_TOLERANCE = 1.02            # закупівля на сторінці Dropt не вища за нашу cost ×1.02 + 1 грн (інакше ціна зросла — ручне рішення)


class OrderError(Exception):
    pass


def final_implemented() -> bool:
    """True лише коли оформлення перевірено живим тестовим замовленням (DROPT_CABINET_VERIFIED=1 у .env VPS ставить Код/власник після нього)."""
    return os.environ.get("DROPT_CABINET_VERIFIED") == "1"


# ───────────────────────────── HTTP ─────────────────────────────
def _url_ok(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    return u.scheme == "https" and (u.hostname or "").lower() == HOST_OK and u.port in (None, 443) and "logout" not in url.lower()


class Http:
    """Тонка обгортка над requests.Session: лише dropt.in.ua, без logout, куки зі стану Playwright. У тестах підміняється об'єктом з тими самими get/post."""

    def __init__(self, state_file=None):
        import requests
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "uk,en;q=0.8"})
        for c in load_cookies(state_file or STATE_FILE):
            self.s.cookies.set(c["name"], c["value"], domain=c.get("domain") or HOST_OK, path=c.get("path") or "/")

    def _check(self, url):
        if not _url_ok(url):
            raise OrderError(f"заборонений URL (лише https://{HOST_OK}, без logout)")

    def get(self, url, **kw):
        self._check(url)
        r = self.s.get(url, timeout=TIMEOUT, allow_redirects=True, **kw)
        self._check(r.url)
        return r.status_code, r.text

    def post(self, url, data=None, files=None, headers=None):
        self._check(url)
        r = self.s.post(url, data=data, files=files, headers=headers, timeout=TIMEOUT, allow_redirects=False)
        return r.status_code, r.text


def load_cookies(path) -> list:
    """Куки dropt.in.ua зі збереженого стану Playwright (storage_state). Нема/биті → OrderError з інструкцією."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise OrderError("нема збереженої сесії кабінету Dropt — власник має виконати вхід: python dropt_cabinet.py --login і скопіювати файл на VPS") from None
    out = [c for c in (d.get("cookies") or []) if isinstance(c, dict) and str(c.get("domain", "")).lstrip(".").endswith(HOST_OK) and c.get("name")]
    if not out:
        raise OrderError("у збереженій сесії Dropt нема куків dropt.in.ua — потрібен новий вхід (--login)")
    return out


# ───────────────────────────── розбір сторінок (чисті функції) ─────────────────────────────
def parse_csrf(html: str):
    m = re.search(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']', html) or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']csrf-token["\']', html)
    return m.group(1) if m else None


def parse_balance(html: str):
    """«БАЛАНС ›  ₴0.00» на сторінці кабінету → float або None."""
    m = re.search(r"БАЛАНС[^₴\d]{0,40}₴\s*([\d\s ]+(?:[.,]\d{1,2})?)", html)
    return _money(m.group(1)) if m else None


def parse_modal_balance(html: str):
    """«Оплатити з балансу (доступно: 0.00 грн)» у модалці → float або None."""
    m = re.search(r"доступно:\s*([\d\s ]+(?:[.,]\d{1,2})?)\s*грн", html)
    return _money(m.group(1)) if m else None


def _money(s):
    try:
        return float(re.sub(r"[\s ]", "", s).replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_product(html: str) -> dict:
    """Сторінка товару → {internal_id, sku, price, in_stock}. Відсутнє → None у полі."""
    m = re.search(r'name=["\']current_params["\'][^>]*value=["\']product_id=(\d+)["\']', html) or re.search(r'value=["\']product_id=(\d+)["\'][^>]*name=["\']current_params["\']', html)
    sku = re.search(r'"sku"\s*:\s*"([^"]+)"', html)
    price = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
    avail = re.search(r'"availability"\s*:\s*"([^"]+)"', html)
    return {"internal_id": m.group(1) if m else None, "sku": sku.group(1) if sku else None, "price": float(price.group(1)) if price else None,
            "in_stock": bool(avail and avail.group(1).rstrip("/").endswith("InStock"))}


def parse_cart_rows(modal_html: str) -> dict:
    """Рядки кошика з HTML модалки → {internal_id: qty}."""
    rows = {}
    for m in re.finditer(r"<tr[^>]*data-product-id=[\"'](\d+)[\"'][^>]*>(.*?)</tr>", modal_html, re.S):
        q = re.search(r"quantity-input[^>]*value=[\"'](\d+)[\"']", m.group(2)) or re.search(r"value=[\"'](\d+)[\"'][^>]*quantity-input", m.group(2))
        rows[m.group(1)] = int(q.group(1)) if q else 1
    return rows


def parse_confirm_url(modal_html: str):
    m = re.search(r'id=["\']modal-checkout-form["\'][^>]*data-action=["\']([^"\']+)["\']', modal_html) or re.search(r'data-action=["\']([^"\']+)["\'][^>]*id=["\']modal-checkout-form["\']', modal_html)
    return m.group(1).replace("&amp;", "&") if m else None


def _json(text):
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else None
    except (TypeError, ValueError):
        return None


# ───────────────────────────── операції ─────────────────────────────
def session_info(http) -> dict:
    code, html = http.get(f"{BASE}/ua/cabinet")
    csrf = parse_csrf(html)
    logged = code == 200 and "account/logout" in html and bool(csrf)
    return {"logged_in": logged, "csrf": csrf, "balance": parse_balance(html) if logged else None}


def ensure_session(http=None) -> dict:
    """Сесія жива (інакше OrderError: потрібен вхід власника; автовходу нема)."""
    http = http or Http()
    info = session_info(http)
    if not info["logged_in"]:
        raise OrderError("сесія кабінету Dropt протухла або не виконано вхід — власник має виконати: python dropt_cabinet.py --login, потім скопіювати файл сесії на VPS")
    return info


def product_info(http, item: dict) -> dict:
    """Внутрішній id/ціна/наявність товару зі сторінки (source_url з supplier_dropt.json). Артикул сторінки має збігатися з замовленим."""
    url = str(item.get("source_url") or "")
    if not _url_ok(url):
        raise OrderError(f"{item.get('vendor_code')}: нема коректної адреси сторінки товару Dropt (source_url)")
    code, html = http.get(url)
    if code != 200:
        raise OrderError(f"{item.get('vendor_code')}: сторінка товару Dropt недоступна (HTTP {code})")
    p = parse_product(html)
    if not p["internal_id"] or not p["sku"]:
        raise OrderError(f"{item.get('vendor_code')}: на сторінці Dropt не знайдено внутрішній id/артикул (змінилась верстка?)")
    if str(p["sku"]).strip() != str(item.get("vendor_code")).strip():
        raise OrderError(f"артикул на сторінці Dropt «{p['sku']}» ≠ замовлений «{item.get('vendor_code')}» — посилання застаріло")
    return p


def _validate(order: dict) -> list:
    items = order.get("items") or []
    if not items:
        raise OrderError("у замовленні нема позицій")
    for it in items:
        try:
            if int(it["qty"]) < 1 or float(it["cost"]) <= 0 or float(it["sale_price"]) <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise OrderError(f"некоректна позиція {it.get('vendor_code')}: потрібні qty≥1, cost>0, sale_price>0") from None
        if not str(it.get("vendor_code") or "").strip():
            raise OrderError("позиція без артикулу")
    return items


def preflight(order: dict, http=None) -> dict:
    """Лише читання: сесія жива, товари знайдені й у наявності, ціна на сторінці не зросла, баланс ≥ сума закупівлі. Кошик НЕ чіпає.
    → {"ready", "problems", "low_balance", "balance", "total", "ids": {vendor_code: internal_id}}."""
    items = _validate(order)
    http = http or Http()
    info = ensure_session(http)
    problems, ids, total = [], {}, 0.0
    for it in items:
        try:
            p = product_info(http, it)
        except OrderError as e:
            problems.append(str(e))
            continue
        ids[str(it["vendor_code"])] = p["internal_id"]
        if not p["in_stock"]:
            problems.append(f"{it['vendor_code']}: на сторінці Dropt нема в наявності")
        cost = float(it["cost"])
        if p["price"] is not None and p["price"] > cost * PRICE_TOLERANCE + 1:
            problems.append(f"{it['vendor_code']}: ціна «Дроп» на сторінці {p['price']} вища за закупівлю в замовленні {cost} — ціна зросла, потрібне ручне рішення")
        total += cost * int(it["qty"])
    bal = info.get("balance")
    low = bal is not None and bal + 0.005 < total
    if bal is None:
        problems.append("баланс Dropt не вдалося прочитати (змінилась верстка?)")
    elif low:
        problems.append(f"недостатньо балансу Dropt: {bal:.2f} < {total:.2f} грн")
    return {"ready": not problems, "problems": problems, "low_balance": bool(low), "balance": bal, "total": round(total, 2), "ids": ids}


def _open_modal(http):
    code, text = http.get(f"{BASE}/index.php?route=checkout/modal")
    j = _json(text)
    if code != 200 or not j or not isinstance(j.get("html"), str):
        raise OrderError(f"модалка оформлення Dropt недоступна (HTTP {code})")
    return j["html"]


def _ttn_digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def order_flow(order: dict, submit: bool = False, http=None) -> dict:
    """submit=False → лише preflight. submit=True → кошик (без сторонніх рядків) → PDF ТТН → «Оформити» з балансу.
    → {"submitted", "dropt_order_ref", "not_submitted_certain", "low_balance", "total_on_checkout", "balance_after", "problems"}.
    OrderError, що вийшла назовні, = відмова ДО кліку (runner трактує як «напевно не оформлено»). Усі збої ПІСЛЯ POST оформлення повертаються всередині результату з not_submitted_certain=False."""
    http = http or Http()
    pre = preflight(order, http)
    if not submit:
        return {"submitted": False, "not_submitted_certain": True, "ready": pre["ready"], "problems": pre["problems"], "low_balance": pre["low_balance"]}
    if not pre["ready"]:
        return {"submitted": False, "not_submitted_certain": True, "low_balance": pre["low_balance"], "problems": pre["problems"]}
    if not final_implemented():
        raise OrderError("оформлення в Dropt не підтверджено живим тестовим замовленням (DROPT_CABINET_VERIFIED≠1) — реальне оформлення закрите")
    items = _validate(order)
    pdf = Path(str(order.get("marking_pdf") or ""))
    try:
        data = pdf.read_bytes()
    except OSError:
        raise OrderError("файл наліпки ТТН недоступний") from None
    if not data.startswith(b"%PDF-") or not 0 < len(data) <= MAX_PDF_BYTES:
        raise OrderError("наліпка ТТН не є коректним PDF ≤ 2 МБ")
    ttn = _ttn_digits(order.get("ttn_number"))
    if len(ttn) != 14:
        raise OrderError(f"номер ТТН некоректний: {order.get('ttn_number')!r}")
    ids = pre["ids"]
    want = {ids[str(it["vendor_code"])]: int(it["qty"]) for it in items}
    info = ensure_session(http)
    csrf = info["csrf"]
    # --- кошик: сторонні рядки не чіпаємо; свої добираємо ---
    have = parse_cart_rows(_open_modal(http))
    foreign = {k: v for k, v in have.items() if k not in want}
    if foreign:
        raise OrderError(f"у кошику Dropt є сторонні товари ({', '.join(sorted(foreign))}) — очистіть кошик у кабінеті, інакше вони потраплять в це замовлення")
    for pid, qty in want.items():
        if have.get(pid) == qty:
            continue
        if pid in have:
            raise OrderError(f"у кошику Dropt товар {pid} уже є з кількістю {have[pid]} ≠ {qty} — очистіть кошик у кабінеті")
        code, text = http.post(f"{BASE}/index.php?route=checkout/cart/add", data={"product_id": pid, "quantity": qty}, headers={"X-Requested-With": "XMLHttpRequest"})
        j = _json(text)
        if code != 200 or not j or j.get("error"):
            raise OrderError(f"кошик Dropt: додавання {pid} не вдалось (HTTP {code})")
    modal = _open_modal(http)
    if parse_cart_rows(modal) != want:
        raise OrderError(f"кошик Dropt після додавання не збігається із замовленням: {parse_cart_rows(modal)} ≠ {want}")
    action = parse_confirm_url(modal)
    if not action or not _url_ok(action):
        raise OrderError("не знайдено адресу оформлення (data-action) у модалці Dropt")
    bal_modal = parse_modal_balance(modal)
    if bal_modal is not None and bal_modal + 0.005 < pre["total"]:
        return {"submitted": False, "not_submitted_certain": True, "low_balance": True, "problems": [f"недостатньо балансу Dropt: {bal_modal:.2f} < {pre['total']:.2f} грн"]}
    # --- PDF ТТН ---
    code, text = http.post(f"{BASE}/index.php?route=account/dropshipper/uploadTTN", data={"csrf_token": csrf}, files={"file": (pdf.name, data, "application/pdf")}, headers={"X-Requested-With": "XMLHttpRequest"})
    j = _json(text)
    if code != 200 or not j or not j.get("success") or not j.get("filename"):
        raise OrderError(f"завантаження PDF ТТН у Dropt не вдалось: {(j or {}).get('error') or 'HTTP ' + str(code)}")
    rec = _ttn_digits(j.get("ttn_number"))
    if rec and rec != ttn:
        raise OrderError(f"Dropt розпізнав у PDF ТТН {rec}, а наша {ttn} — перевірте наліпку")
    # --- оформлення ---
    form = {"order_type": "drop", "delivery_type": "external", "ttn_number": ttn, "ttn_filename": j["filename"], "pay_from_balance": "1", "payment_choice": "balance", "payment_method": "prepaid",
            "comment": "", "csrf_token": csrf}
    total_sale = 0.0
    for it in items:
        form[f"sale_price[{ids[str(it['vendor_code'])]}]"] = f"{float(it['sale_price']):g}"
        total_sale += float(it["sale_price"]) * int(it["qty"])
    form["total_order_price"] = f"{total_sale:g}"
    try:
        code, text = http.post(action, data=form, headers={"X-Requested-With": "XMLHttpRequest"})
    except Exception as e:  # noqa: BLE001 — результат кліку невідомий (таймаут/мережа)
        return {"submitted": False, "not_submitted_certain": False, "problems": [f"POST оформлення Dropt: результат невідомий ({type(e).__name__}) — звірте замовлення в кабінеті"]}
    j = _json(text)
    if code != 200 or j is None:
        return {"submitted": False, "not_submitted_certain": False, "problems": [f"оформлення Dropt: незрозуміла відповідь (HTTP {code}) — звірте замовлення в кабінеті"]}
    if j.get("error"):
        err = j["error"]
        txt = err if isinstance(err, str) else "; ".join(f"{k}: {v}" for k, v in err.items())
        return {"submitted": False, "not_submitted_certain": True, "low_balance": bool(j.get("redirect_deposit")), "problems": [f"Dropt відхилив оформлення: {txt}"[:300]]}
    if not j.get("success"):
        return {"submitted": False, "not_submitted_certain": False, "problems": ["оформлення Dropt: відповідь без success/error — звірте замовлення в кабінеті"]}
    ref = None
    ga4 = j.get("ga4")
    if isinstance(ga4, dict) and ga4.get("transaction_id"):
        ref = str(ga4["transaction_id"])
    ref = ref or find_order_ref(http, ttn) or f"ttn:{ttn}"
    bal_after = None
    try:
        bal_after = session_info(http).get("balance")
    except Exception:  # noqa: BLE001
        pass
    return {"submitted": True, "dropt_order_ref": ref, "not_submitted_certain": False, "total_on_checkout": round(pre["total"], 2), "balance_after": bal_after, "problems": []}


def find_order_ref(http, ttn: str):
    """Номер замовлення зі списку «Замовлення» кабінету за нашою ТТН (best-effort; None якщо не знайшли)."""
    try:
        code, html = http.get(f"{BASE}/ua/cabinet?tab=orders")
        if code != 200:
            return None
        for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.S):
            row = m.group(1)
            if ttn in re.sub(r"\D", "", re.sub(r"<[^>]+>", " ", row)).replace(" ", "") or ttn in row:
                cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
                if cells and re.fullmatch(r"#?\d{3,}", cells[0]):
                    return cells[0].lstrip("#")
        return None
    except Exception:  # noqa: BLE001
        return None


# ───────────────────────────── вхід власника ─────────────────────────────
def login() -> int:
    """Вікно Playwright: власник входить у кабінет (Google/Telegram), Enter у терміналі → сесія в STATE_FILE (0600). Пароль/код не зберігаємо."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto(f"{BASE}/ua/cabinet", timeout=60000)
            print("[DroptCabinet] Увійдіть у кабінет Dropt у вікні, що відкрилось (Google/Telegram), дочекайтесь сторінки «Кабінет дропшипера», потім натисніть Enter тут.")
            try:
                input()
            except EOFError:
                print("[DroptCabinet] Немає інтерактивного вводу — --login запускати вручну в терміналі.", file=sys.stderr)
                return 1
            page.goto(f"{BASE}/ua/cabinet", timeout=60000)
            html = page.content()
            if "account/logout" not in html:
                print("[DroptCabinet] Не бачу ознак входу (посилання на вихід). Сесію НЕ збережено.")
                return 1
            STATE_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(ctx.storage_state(), f, ensure_ascii=False)
            os.replace(tmp, STATE_FILE)
        finally:
            browser.close()
    print(f"[DroptCabinet] Сесію збережено: {STATE_FILE}")
    return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Кабінет Dropt: --login (вхід власника), --check (сесія і баланс, лише читання)")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    if a.login:
        return login()
    if a.check:
        try:
            info = ensure_session()
        except OrderError as e:
            print(f"[DroptCabinet] {e}", file=sys.stderr)
            return 1
        print(f"[DroptCabinet] сесія жива; баланс: {info['balance']}")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
