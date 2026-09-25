#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
site_order_api.py — HTTP-бекенд власного магазину plutustoys.com.ua (stdlib, без Flask,
як control_panel.py). Обслуговує СТАТИКУ сайту (site/) і API:

  GET  /api/np/city?q=<текст>                 → автокомпліт міста НП        → [{ref,name,area}]
  GET  /api/np/warehouse?city_ref=<ref>&q=<n> → автокомпліт відділення НП   → [{ref,description,number}]
  POST /api/order        {items:[{id,qty}], name, phone, email, city_name, warehouse_name,
                          np_city_ref, np_city_area, np_warehouse_number,
                          payment_method: "cod"|"prepaid"}
                         → створює замовлення (payment_confirmed=0) → {order_id,total,payment_method,liqpay}
                           cod  → liqpay=null, замовлення прийнято одразу (накладений платіж НП)
                           prepaid → liqpay={...} для редіректу (або null, якщо ключі не задані)
  POST /api/liqpay/callback  (data, signature)  → verify → mark_payment_confirmed → 200

MONEY-SAFETY:
- Ціни рахуються НА СЕРВЕРІ з site/index.json (id→pr) — ціни з кошика клієнта НЕ довіряються.
- payment_confirmed=0 на старті для ОБОХ способів. order_router: COD форвардиться одразу
  (накладений — гроші беруться при отриманні), prepaid форвардиться ЛИШЕ після підтвердженого
  колбека LiqPay (get_orders_ready_to_forward). Тестовий/несплачений prepaid закупівлю не запускає.
- payment_method невідоме/відсутнє → cod (консервативно; не запускає передоплатну гілку помилково).
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
from datetime import datetime, timedelta
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
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COD_BREAKER_STATE_FILE = os.path.join(BASE_DIR, ".local_secrets", "site_cod_breaker_state.json")

MAX_QTY_PER_ITEM = 50
MAX_ITEMS = 100

# ── Гардрейли COD (CONSULTANT_CHANNEL.md 2026-09-13 → ескалював 2026-09-16 з «на розсуд» до
# необхідного: EVA-звірка КОДВ, 25 замовлень 01.08-05.09, 8/25=32% скасовано покупцем/відхилено —
# «кожне третє не доходить до грошей», а сайт форвардить COD у Toysi ОДРАЗУ, до оплати, платимо
# зворотну логістику наперед). Консультант НЕ пише код і не встановлює точні пороги — лише
# ризик+напрямок; конкретні числа нижче МОЇ, tunable через env, не звірені з живими продажами
# сайту (трафіку на сайті ще нема, WebSocket-канал 0 замовлень) — коли зʼявляться перші реальні
# COD-замовлення, звірити разом з Консультантом і КОДВ, чи пороги адекватні.
# Стеля суми COD-замовлення — «умовно 3 000 ₴» (Консультант, дослівно). Вище — лише передоплата.
SITE_COD_CEILING = int(os.environ.get("SITE_COD_CEILING", "3000"))
# Ліміт COD-замовлень з ОДНОГО телефону за 24 год — Консультант НЕ назвав число («рішення твоє»).
# 2 — обережний дефолт: блокує спам/накрутку одним номером, не заважає легітимному повторному
# замовленню того самого дня (напр. забув товар). Рахуємо ЛИШЕ COD — prepaid самообмежується
# оплатою наперед, той самий ризик там відсутній.
SITE_COD_PHONE_DAILY_LIMIT = int(os.environ.get("SITE_COD_PHONE_DAILY_LIMIT", "2"))
# Глобальний circuit-breaker COD (Консультант, CONSULTANT_CHANNEL.md 2026-09-18: "N=10,
# деградація на передоплату, не алерт") — на відміну від двох гальм вище (стеля суми, ліміт на
# телефон), рахує ВЕСЬ сайт, не одного клієнта: EVA-звірка КОДВ виміряла 32% скасувань COD
# (01.08-05.09, кожне замовлення перевірене окремо), а сайт форвардить COD у Toysi ДО оплати —
# кожне скасування коштує зворотну логістику. Триггер і скидання — МОЇ рішення (число N=10
# Консультанта), tunable/скидається через env, не звірено з живими продажами (трафіку нема).
SITE_COD_CIRCUIT_BREAKER_N = int(os.environ.get("SITE_COD_CIRCUIT_BREAKER_N", "10"))
# Сталий trip (не самоскидається за часом/вікном) — свідомо: "circuit breaker" означає розмикач,
# що лишається розімкненим, поки хтось свідомо не проглянув причину й не скинув. Скидання:
# SITE_COD_CIRCUIT_BREAKER_RESET_AFTER=<ISO-мітка> — лічильник рахує лише скасування ПІСЛЯ неї.
SITE_COD_CIRCUIT_BREAKER_RESET_AFTER = os.environ.get("SITE_COD_CIRCUIT_BREAKER_RESET_AFTER", "")
# Ті самі статуси, що order_status_tracker.py:_UNSUCCESSFUL_DELIVERY_STATUSES (P0-2 "неуспішні
# замовлення") — щоб не тримати другий паралельний список "що вважається відмовою".
_COD_UNSUCCESSFUL_STATUSES = {"cancelled", "returned"}
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:  # noqa: BLE001
        print(f"[site_order_api] Telegram не надіслано (не критично): {e}", file=sys.stderr)

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
        # price+name ОБОВʼЯЗКОВІ у позиції: order_router рахує COD moneyback як
        # sum(item["price"]*qty) (order_router.py:228) — без price COD-ТТН вийде на 0 ₴.
        items.append({"toysi_code": pid, "name": pm[pid]["name"], "qty": qty, "price": pm[pid]["price"]})
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
    # Область обраного міста (НП AreaDescription, з автокомпліту фронта) — щоб Toysi не переплутав
    # однойменні населені пункти різних областей (інцидент EVA 8-081747967, 24.09.2026: «Південне»
    # Харківської обл. → «Южное» під Одесою; апідок Toysi прямо радить область/район У shipping_city).
    # Клієнт-подане значення — санітуємо (обмежена довжина, без дужок/переносів, що зламали б
    # "(Xобл.)"-формат, який parse_np_branch очікує).
    _area_raw = (payload.get("np_city_area") or "").strip()[:60]
    city_area = re.sub(r"[()\r\n]", "", _area_raw).strip()
    city_area = re.sub(r"\s*обл(?:асть|\.)?\s*$", "", city_area, flags=re.IGNORECASE).strip()

    # Email — ОПЦІЙНИЙ (рішення SMM: email опційне). Приймаємо, лише якщо схоже на валідний
    # (@ + крапка в домені); інакше тихо None — не блокуємо замовлення через кривий необовʼязковий
    # email. Зберігаємо в orders (колонка email) → далі для листа-підтвердження сайту.
    email_raw = (payload.get("email") or "").strip()
    email = email_raw if ("@" in email_raw and "." in email_raw.split("@")[-1]) else None

    # Спосіб оплати — з фронта: "cod" (накладений платіж НП) або "prepaid" (картка онлайн).
    # Default cod — консервативно й money-safe: невідомий/відсутній → накладений (forward одразу),
    # а не передоплата (яка форвардиться лише по payment_confirmed=1). order_router:
    # COD форвардиться одразу, prepaid — лише коли payment_confirmed=1.
    pay_raw = (payload.get("payment_method") or "").strip().lower()
    payment_method = "prepaid" if pay_raw == "prepaid" else "cod"

    # Структурні реф-поля НП з автокомпліту фронта: якщо клієнт ОБРАВ місто/відділення зі
    # списку — маємо точний CityRef + № відділення → order_router віддасть Toysi їх НАПРЯМУ,
    # без пошуку в НП (як EVA). Порожні (ручний ввід без вибору) → None → форвард парсить
    # np_branch текстом, як раніше. Валідуємо формат: ref — GUID-подібний, № — цифри.
    _cref = (payload.get("np_city_ref") or "").strip()
    np_city_ref = _cref if re.fullmatch(r"[0-9a-fA-F-]{10,}", _cref) else None
    _wnum = (payload.get("np_warehouse_number") or "").strip()
    np_warehouse_number = _wnum if _wnum.isdigit() else None

    order_id = "PT-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    order = {
        "order_id": order_id,
        "platform": "site",
        "status": "new",
        "payment_method": payment_method,   # cod=накладений (forward одразу) | prepaid=картка (forward по payment_confirmed=1)
        "payment_confirmed": 0,
        "customer_name": name,
        "phone": phone,
        "email": email,
        # людський рядок (фолбек, якщо рефів нема) — "(Xобл.)" одразу після міста, той самий
        # формат, що й Prom/Rozetka, щоб order_router.parse_np_branch витяг area_hint коректно.
        "np_branch": f"{city} ({city_area} обл.), {warehouse}" if city_area else f"{city}, {warehouse}",
        "np_city_ref": np_city_ref,            # точний CityRef НП (автокомпліт) → Toysi напряму
        "np_warehouse_number": np_warehouse_number,  # точний № відділення (автокомпліт) → Toysi напряму
        "items": items,
        "carrier": "nova_poshta",
    }
    return order, total


def _check_cod_ceiling(payment_method: str, total: int) -> None:
    """Стеля суми COD (Консультант 13.09). Вище стелі не відмовляємо в купівлі повністю —
    просимо передоплату, як і для будь-якого дорогого замовлення."""
    if payment_method == "cod" and total > SITE_COD_CEILING:
        raise OrderError(
            f"Накладений платіж доступний до {SITE_COD_CEILING} ₴. "
            "Оформіть, будь ласка, замовлення передоплатою карткою."
        )


def _check_cod_phone_limit(conn, phone: str) -> None:
    """Забагато COD-замовлень з одного телефону за 24 год → відмова (клієнт може оформити
    передоплатою). Рахує ЛИШЕ COD-замовлення сайту (SITE_COD_PHONE_DAILY_LIMIT вище)."""
    since = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE platform='site' AND payment_method='cod' "
        "AND phone=? AND created_at>=?",
        (phone, since),
    ).fetchone()
    if row and row[0] >= SITE_COD_PHONE_DAILY_LIMIT:
        raise OrderError(
            "Забагато замовлень накладеним платежем з цього номера за останню добу. "
            "Оформіть, будь ласка, наступне замовлення передоплатою карткою."
        )


def _cod_circuit_breaker_count(conn) -> int:
    """Скільки COD-замовлень САЙТУ загалом скасовано/повернено — ГЛОБАЛЬНО, не по телефону
    (на відміну від _check_cod_phone_limit). `delivery_status` оновлює order_status_tracker.py
    (VPS) для всіх платформ однаково — сайт нового окремого джерела не потребує."""
    params = ["site"] + sorted(_COD_UNSUCCESSFUL_STATUSES)
    placeholders = ",".join("?" * len(_COD_UNSUCCESSFUL_STATUSES))
    sql = (
        f"SELECT COUNT(*) FROM orders WHERE platform=? AND payment_method='cod' "
        f"AND delivery_status IN ({placeholders})"
    )
    if SITE_COD_CIRCUIT_BREAKER_RESET_AFTER:
        sql += " AND created_at >= ?"
        params.append(SITE_COD_CIRCUIT_BREAKER_RESET_AFTER)
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else 0


def _cod_breaker_already_alerted(count: int) -> bool:
    """Антиспам для Telegram-алерту (файл стану, той самий патерн, що plutus_seller_watchdog.py —
    жодної таблиці orders.db для цього не заводимо, це не грошові дані)."""
    try:
        with open(COD_BREAKER_STATE_FILE, encoding="utf-8") as f:
            return json.load(f).get("alerted_count") == count
    except (OSError, ValueError):
        return False


def _cod_breaker_mark_alerted(count: int) -> None:
    try:
        os.makedirs(os.path.dirname(COD_BREAKER_STATE_FILE), exist_ok=True)
        with open(COD_BREAKER_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"alerted_count": count}, f)
    except OSError:
        pass


def _check_cod_circuit_breaker(conn) -> None:
    """Глобальний circuit-breaker: N=10 скасованих/повернених COD-замовлень САЙТУ загалом →
    COD ЗАКРИТО для ВСІХ наступних замовлень (не лише цього телефону), поки хтось свідомо не
    скине (SITE_COD_CIRCUIT_BREAKER_RESET_AFTER). Перший заблокований запит після спрацювання
    шле Telegram-алерт ОДИН раз (доки count не зміниться — нове скасування)."""
    count = _cod_circuit_breaker_count(conn)
    if count < SITE_COD_CIRCUIT_BREAKER_N:
        return
    if not _cod_breaker_already_alerted(count):
        _notify(
            f"🔴 Circuit-breaker COD спрацював: {count} скасованих/повернених COD-замовлень "
            f"сайту (поріг {SITE_COD_CIRCUIT_BREAKER_N}). COD закрито для ВСІХ нових замовлень "
            f"сайту, доки хтось свідомо не скине через SITE_COD_CIRCUIT_BREAKER_RESET_AFTER."
        )
        _cod_breaker_mark_alerted(count)
    raise OrderError(
        "Накладений платіж тимчасово недоступний на сайті. "
        "Оформіть, будь ласка, замовлення передоплатою карткою."
    )


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
            _check_cod_ceiling(order["payment_method"], total)
        except OrderError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            print(f"[site_order_api] {self.command} {self.path}: {e}", file=sys.stderr)
            return self._json(500, {"error": "внутрішня помилка"})

        try:
            with get_connection() as conn:
                if order["payment_method"] == "cod":
                    _check_cod_circuit_breaker(conn)   # глобальний гейт — перед per-телефон
                    _check_cod_phone_limit(conn, order["phone"])
                created = insert_order(conn, order)
                if created:
                    # фіксуємо виставлену суму для звірки з колбеком (стійко до дрейфу цін у каталозі)
                    conn.execute(
                        "UPDATE orders SET site_charged_total=? WHERE internal_order_id=?",
                        (total, f"site_{order['order_id']}"),
                    )
                conn.commit()
        except OrderError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            print(f"[site_order_api] insert_order помилка: {e}", file=sys.stderr)
            return self._json(500, {"error": "не вдалося зберегти замовлення"})
        if not created:
            return self._json(409, {"error": "замовлення вже існує"})

        resp = {"order_id": order["order_id"], "total": total,
                "payment_method": order["payment_method"]}
        if order["payment_method"] == "cod":
            # Накладений платіж — онлайн-оплата не потрібна; замовлення прийнято одразу.
            resp["liqpay"] = None
            resp["message"] = "Замовлення прийнято. Оплата при отриманні на Новій Пошті."
            return self._json(200, resp)
        # prepaid: форма LiqPay, якщо ключі задані; інакше — фронт покаже, що онлайн-оплата недоступна
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
