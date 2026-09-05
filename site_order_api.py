#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
site_order_api.py — HTTP-бекенд власного магазину plutustoys.com.ua (stdlib, без Flask,
як control_panel.py). Обслуговує СТАТИКУ сайту (site/) і API:

  GET  /api/np/city?q=<текст>                 → автокомпліт міста НП        → [{ref,name,area}]
  GET  /api/np/warehouse?city_ref=<ref>&q=<n> → автокомпліт відділення НП   → [{ref,description,number}]
  POST /api/order        {items:[{id,qty}], name, phone, email, city_name, warehouse_name}
                         → створює замовлення (prepaid, payment_confirmed=0) → {order_id,total,liqpay}
  POST /api/liqpay/callback  (data, signature)  → verify → mark_payment_confirmed → 200

MONEY-SAFETY:
- Ціни рахуються НА СЕРВЕРІ з site/index.json (id→pr) — ціни з кошика клієнта НЕ довіряються.
- Замовлення завжди prepaid + payment_confirmed=0 → order-pipeline форвардить у Toysi ЛИШЕ
  після підтвердженого колбека LiqPay (get_orders_ready_to_forward). Тестовий/несплачений
  платіж закупівлю не запускає.
- Колбек: підпис (verify_callback) + звірка суми з перерахованою + відсів sandbox-статусу в бою.
- Тест: ORDERS_DB_PATH=<тимчасова БД> ДО запуску (не бойова orders.db).

Конфіг (env): SITE_API_HOST/PORT, SITE_DIR (де index.json + сторінки), SITE_BASE_URL
(для result_url/server_url LiqPay). LiqPay-ключі — через liqpay_client (.env), sandbox за замовч.
"""
import os
import re
import sys
import json
import time
import secrets
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import nova_poshta
import liqpay_client
from orders_db import get_connection, init_db, insert_order, get_order, mark_payment_confirmed

HOST = os.environ.get("SITE_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("SITE_API_PORT", "8901"))
SITE_DIR = os.environ.get("SITE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "site"))
INDEX_PATH = os.path.join(SITE_DIR, "index.json")
BASE_URL = os.environ.get("SITE_BASE_URL", "https://plutustoys.com.ua").rstrip("/")

MAX_QTY_PER_ITEM = 50
MAX_ITEMS = 100

# ── Ціни з боку сервера (site/index.json: [{id,n,pr,p}, ...]) — НЕ довіряємо кошику клієнта ──
_price_lock = threading.Lock()
_price_cache = {"mtime": -1.0, "map": {}}


def price_map() -> dict:
    """id(str) → {'price':int,'name':str} із site/index.json, з кеш-рефрешем за mtime."""
    try:
        mtime = os.path.getmtime(INDEX_PATH)
    except OSError:
        return {}
    with _price_lock:
        if mtime != _price_cache["mtime"]:
            try:
                with open(INDEX_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                _price_cache["map"] = {
                    str(p["id"]): {"price": int(p["pr"]), "name": p.get("n", "")}
                    for p in data if "id" in p and "pr" in p
                }
                _price_cache["mtime"] = mtime
            except Exception:
                pass  # лишаємо попередній кеш, якщо файл тимчасово битий
        return _price_cache["map"]


def _norm_phone(raw: str) -> str:
    """Нормалізує телефон у формат 380XXXXXXXXX (12 цифр). Порожній рядок, якщо не валідний."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("0") and len(digits) == 10:
        digits = "38" + digits
    if digits.startswith("380") and len(digits) == 12:
        return digits
    return ""


class OrderError(Exception):
    """Валідаційна помилка замовлення — повертаємо клієнту 400 з поясненням."""


def build_order(payload: dict) -> tuple:
    """Валідує вхід і будує dict замовлення. Повертає (order, total). Кидає OrderError."""
    pm = price_map()
    if not pm:
        raise OrderError("Каталог тимчасово недоступний, спробуйте пізніше")

    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise OrderError("Кошик порожній")
    if len(raw_items) > MAX_ITEMS:
        raise OrderError("Забагато позицій у замовленні")

    items, total = [], 0
    for it in raw_items:
        it = it if isinstance(it, dict) else {}   # елемент не-обʼєкт → чиста валідаційна відмова, не 500
        pid = str(it.get("id", "")).strip()
        try:
            qty = int(it.get("qty", 0))
        except (TypeError, ValueError):
            qty = 0
        if pid not in pm:
            raise OrderError(f"Товар {pid} недоступний")
        if qty < 1 or qty > MAX_QTY_PER_ITEM:
            raise OrderError(f"Некоректна кількість для товару {pid}")
        total += pm[pid]["price"] * qty
        items.append({"toysi_code": pid, "qty": qty})
    if not items:
        raise OrderError("Кошик порожній")

    name = (payload.get("name") or "").strip()
    if len(name) < 3 or " " not in name:
        raise OrderError("Вкажіть імʼя та прізвище отримувача")
    phone = _norm_phone(payload.get("phone"))
    if not phone:
        raise OrderError("Вкажіть коректний номер телефону (+380…)")
    city = (payload.get("city_name") or "").strip()
    warehouse = (payload.get("warehouse_name") or "").strip()
    if not city or not warehouse:
        raise OrderError("Оберіть місто та відділення Нової Пошти")

    order_id = "PT-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    order = {
        "order_id": order_id,
        "platform": "site",
        "status": "new",
        "payment_method": "prepaid",   # web-оплата карткою; forward лише по payment_confirmed=1
        "payment_confirmed": 0,
        "customer_name": name,
        "phone": phone,
        "np_branch": f"{city}, {warehouse}",   # order_router.parse_np_branch розбере при форварді
        "items": items,
        "carrier": "nova_poshta",
    }
    return order, total


def recompute_total(items: list) -> int:
    """Перераховує суму збережених items (для звірки з колбеком LiqPay)."""
    pm = price_map()
    total = 0
    for it in items or []:
        pid = str(it.get("toysi_code", ""))
        qty = int(it.get("qty", 0))
        if pid in pm:
            total += pm[pid]["price"] * qty
    return total


class Handler(SimpleHTTPRequestHandler):
    server_version = "PlutusToysSiteAPI/1.0"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=SITE_DIR, **kw)

    def log_message(self, fmt, *args):
        # тихий лог у stderr (systemd journal), без спаму статикою
        pass

    # ── утиліти відповіді ──
    def _json(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code: int, text: str):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _read_form(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return {k: v[0] for k, v in parse_qs(raw).items()}

    # ── GET: статика + NP-автокомпліт ──
    def do_GET(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return super().do_GET()   # статика сайту з SITE_DIR
        q = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/np/city":
                cities = nova_poshta.search_cities((q.get("q") or [""])[0])
                return self._json(200, {"cities": cities})
            if parsed.path == "/api/np/warehouse":
                city_ref = (q.get("city_ref") or [""])[0]
                whs = nova_poshta.search_warehouses(city_ref, (q.get("q") or [""])[0])
                return self._json(200, {"warehouses": whs})
        except nova_poshta.NovaPoshtaAPIError as e:
            return self._json(502, {"error": "Нова Пошта тимчасово недоступна", "detail": str(e)})
        except Exception as e:
            print(f"[site_order_api] {self.command} {self.path}: {e}", file=sys.stderr)
            return self._json(500, {"error": "внутрішня помилка"})
        return self._json(404, {"error": "невідомий ендпоінт"})

    # ── POST: замовлення + колбек LiqPay ──
    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/order":
            return self._handle_order()
        if parsed.path == "/api/liqpay/callback":
            return self._handle_callback()
        return self._json(404, {"error": "невідомий ендпоінт"})

    def _handle_order(self):
        payload = self._read_json()
        try:
            order, total = build_order(payload)
        except OrderError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            print(f"[site_order_api] {self.command} {self.path}: {e}", file=sys.stderr)
            return self._json(500, {"error": "внутрішня помилка"})

        try:
            with get_connection() as conn:
                created = insert_order(conn, order)
                if created:
                    # фіксуємо виставлену суму для звірки з колбеком (стійко до дрейфу цін у каталозі)
                    conn.execute(
                        "UPDATE orders SET site_charged_total=? WHERE internal_order_id=?",
                        (total, f"site_{order['order_id']}"),
                    )
                conn.commit()
        except Exception as e:
            print(f"[site_order_api] insert_order помилка: {e}", file=sys.stderr)
            return self._json(500, {"error": "не вдалося зберегти замовлення"})
        if not created:
            return self._json(409, {"error": "замовлення вже існує"})

        resp = {"order_id": order["order_id"], "total": total}
        # LiqPay: форма оплати, якщо ключі задані; інакше — замовлення прийнято без онлайн-оплати (sandbox/заглушка)
        if liqpay_client.is_configured():
            desc = f"Замовлення {order['order_id']} на PlutusToys"
            ck = liqpay_client.build_checkout(
                order_id=order["order_id"], amount=total, description=desc,
                result_url=f"{BASE_URL}/thanks.html",
                server_url=f"{BASE_URL}/api/liqpay/callback",
            )
            resp["liqpay"] = {"action_url": ck["action_url"], "data": ck["data"],
                              "signature": ck["signature"], "sandbox": ck["sandbox"]}
        else:
            resp["liqpay"] = None
            resp["message"] = ("Замовлення прийнято. Онлайн-оплату (LiqPay) ще не підключено — "
                               "менеджер звʼяжеться для підтвердження.")
        return self._json(200, resp)

    def _handle_callback(self):
        form = self._read_form()
        data, signature = form.get("data", ""), form.get("signature", "")
        res = liqpay_client.verify_callback(data, signature)
        if not res["valid"]:
            return self._text(400, "invalid signature")
        # захист: sandbox-статус не приймаємо у бойовому режимі
        if res["status"] == "sandbox" and not liqpay_client.LIQPAY_SANDBOX:
            return self._text(400, "sandbox status rejected in production")
        if not res["paid"]:
            return self._text(200, f"ok (status={res['status']})")  # не оплачено — просто підтверджуємо прийом

        internal = f"site_{res['order_id']}"
        try:
            with get_connection() as conn:
                order = get_order(conn, internal)
                if not order:
                    return self._text(404, "order not found")
                # звіряємо із зафіксованою при оформленні сумою (стійко до дрейфу цін);
                # фолбек на перерахунок для давніх замовлень без site_charged_total
                expected = order.get("site_charged_total")
                if expected is None:
                    expected = recompute_total(order["items"])
                amount = res.get("amount")
                if amount is not None and abs(float(amount) - float(expected)) > 0.01:
                    # сума не збігається — НЕ підтверджуємо (money-safety), лишаємо для розбору
                    return self._text(400, f"amount mismatch: got {amount}, expected {expected}")
                mark_payment_confirmed(conn, internal)  # ідемпотентно
                conn.commit()
        except Exception as e:
            print(f"[site_order_api] callback помилка: {e}", file=sys.stderr)
            return self._text(500, "internal error")
        return self._text(200, "ok")


def run():
    init_db()  # гарантуємо схему/міграції (site-платформа) на цільовій orders.db
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    mode = "SANDBOX" if liqpay_client.LIQPAY_SANDBOX else "LIVE"
    configured = "ключі є" if liqpay_client.is_configured() else "БЕЗ ключів (заглушка)"
    print(f"[site_order_api] http://{HOST}:{PORT}  SITE_DIR={SITE_DIR}  LiqPay={mode} ({configured})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    run()
