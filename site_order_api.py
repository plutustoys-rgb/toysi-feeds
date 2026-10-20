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
- Колбек: підпис (verify_callback) + відсів sandbox-статусу в бою + оплата лише для success/sandbox (wait_accept та інші pending/reversed НЕ підтверджують, лише алерт) +
  валюта == UAH + звірка суми з перерахованою.
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
import site_promo
from orders_db import get_connection, init_db, insert_order, get_order, mark_payment_confirmed, set_liqpay_payment_id

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


_LIQPAY_STATUS_HINT = {
    "wait_accept": "кошти списані, але магазин ще не пройшов перевірку LiqPay (без активації за 60 днів платежі автоматично скасовуються)",
    "hold_wait": "сума заблокована на рахунку відправника (не списана)",
    "wait_secure": "платіж на перевірці LiqPay",
    "wait_compensation": "платіж успішний, буде зарахований у щодобовій проводці",
    "wait_card": "не встановлений спосіб відшкодування в одержувача (магазину)",
    "wait_lc": "акредитив: кошти списані, очікується підтвердження доставки товару",
    "wait_reserve": "кошти зарезервовані для проведення повернення за раніше поданою заявкою",
    "reversed": "платіж повернений",
}


def _alert_liqpay_status(res: dict) -> None:
    """Непідтверджений/повернений статус LiqPay: замовлення НЕ передаємо в Toysi й чек НЕ виписуємо (payment_confirmed лишається 0) — сповіщаємо власника,
    самодіагностично (що, чому, що робити). Тротлінг: раз на 12 год на пару замовлення+статус. Збій алерту не валить колбек."""
    if _NO_TELEGRAM:
        return
    status = res.get("status")
    oid = f"site_{res.get('order_id')}"
    extra = ("Якщо замовлення вже відправлене чи чек видано, потрібне повернення чека (kodv_return_receipt.py) і рішення по посилці."
             if status == "reversed" else
             "Замовлення НЕ підтверджено: у Toysi не передаємо, чек не виписуємо. Звір статус платежу в кабінеті LiqPay (LiqPay не документує повторну відправку колбека після активації магазину: не очікуй, що замовлення підтвердиться само).")
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(f"liqpay_status:{oid}:{status}",
                             f"💳 LiqPay: {oid} має статус {status} ({_LIQPAY_STATUS_HINT.get(status, 'див. документацію LiqPay')}). {extra}",
                             cooldown_sec=12 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[site_order_api] алерт LiqPay-статусу не надіслано (не критично): {e}", file=sys.stderr)


def _test_order_preview(order: dict, total) -> str:
    """Тестове (sandbox) замовлення НЕ йде в Toysi (рішення власника 12.10: «замість Тойсі направ тестове замовлення у Telegram до нас»): показуємо, ЩО пішло б у Toysi, —
    позиції, доставку, оплату. Без ПІБ/телефону/email (лише те, що потрібно перевірити: склад, місто/відділення, суму)."""
    try:
        lines = ["Що пішло б у Toysi (у Toysi НЕ передано):"]
        for it in (order.get("items") or [])[:10]:
            lines.append(f"• {str(it.get('name') or it.get('toysi_code') or '?')[:60]} × {it.get('qty')} · {it.get('price')} грн")
        if len(order.get("items") or []) > 10:
            lines.append(f"• … ще {len(order['items']) - 10} поз.")
        _dest = re.sub(r'\d{6,}', '…', str(order.get('np_branch') or '—')[:120])   # довгі числа (телефон) маскуємо
        lines.append(f"Доставка: {_dest}")
        lines.append(f"Оплата: {order.get('payment_method') or '—'} · разом {total} грн")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001  # сповіщення не має валити колбек LiqPay (оплата вже зафіксована)
        print(f"[site_order_api] прев'ю тестового замовлення не побудовано: {type(e).__name__}", file=sys.stderr)
        return ""


def _notify_new_order(order: dict, total, test: bool = False, note: str = "") -> None:
    """Єдине повідомлення власнику в Telegram (режим «лише замовлення»): надходження замовлення сайту. Без персональних даних; збій не ламає прийом."""
    if _NO_TELEGRAM:
        return
    try:
        from orders_db import clean_customer_comment
        _cc = clean_customer_comment(order.get("customer_comment"))
        if _cc:
            note = (f"{note} · " if note else "") + f"Коментар покупця: {_cc}"
        if order.get("promo_code"):
            note = (f"{note} · " if note else "") + f"Знижка {order['promo_code']}: −{order.get('promo_discount_total')} грн"
        from telegram_notify import notify_new_order
        notify_new_order("site", order.get("order_id"), total, len(order.get("items") or []), order.get("payment_method", ""), test=test, note=note)
    except Exception as e:  # noqa: BLE001
        print(f"[site_order_api] сповіщення про нове замовлення не надіслано: {type(e).__name__}", file=sys.stderr)


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


def _expected_total(raw):
    """Сума, яку ПОКАЗАВ покупцю кошик (ціле, грн); необов'язкове поле: нема/некоректне → None (поведінка як раніше, без перевірки). Ніколи не кидає виняток:
    приймаємо лише int, float із цілим значенням і рядок із 1–12 ASCII-цифр (юнікод-цифри '²', арабські, дуже довгі числа → None)."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw if abs(raw) < 10**12 else None
    if isinstance(raw, float):
        return int(raw) if raw == raw and abs(raw) < 1e12 and raw.is_integer() else None
    if isinstance(raw, str) and re.fullmatch(r"[0-9]{1,12}", raw):
        return int(raw)
    return None


class StockChanged(Exception):
    """Залишок Toysi за свіжим знімком (site/stock.json) менший за замовлене: замовлення НЕ створюється, клієнт отримує 409 {code: stock_changed, lines: [...]}."""

    def __init__(self, lines):
        super().__init__("stock_changed")
        self.lines = lines


# Товари партнера RoyalToys (id «rt-<id>», рішення власника 19.10.2026): лише передоплата (накладений ВИМКНЕНО: ТТН наша, повернення накладеного — нам), склад — fail-closed
# (нема свіжого знімка / ключа «rt-…» у ньому → «немає»), на відміну від Toysi, де невідомий залишок не блокує.
RT_PREFIX = "rt-"


def is_rt_id(pid) -> bool:
    return str(pid or "").strip().lower().startswith(RT_PREFIX)


STOCK_FILE = os.path.join(SITE_DIR, "stock.json")
STOCK_MAX_AGE = timedelta(minutes=45)      # знімок пишеться кожні 15 хв (stock-snapshot.timer); 3 інтервали без оновлення = знімок застарів → перевірку пропускаємо (fail-open)
_stock_cache = {"mtime": None, "data": None}


def stock_snapshot():
    """({id: залишок}, datetime ts) зі свіжого знімка залишків, інакше (None, None). Збій читання/застарілий знімок = «невідомо» (політика Консультанта 13.10: збій перевірки залишку НЕ блокує замовлення)."""
    try:
        mtime = os.path.getmtime(STOCK_FILE)
        if mtime != _stock_cache["mtime"]:
            with open(STOCK_FILE, encoding="utf-8") as f:
                d = json.load(f)
            ts = datetime.fromisoformat(d["ts"])
            if ts.tzinfo is not None:            # знімок з поясом (+03:00) → локальний наївний час сервера, як datetime.now()
                ts = ts.astimezone().replace(tzinfo=None)
            _stock_cache["data"] = (d["s"], ts)
            _stock_cache["mtime"] = mtime
        s, ts = _stock_cache["data"]
        if not isinstance(s, dict) or datetime.now() - ts > STOCK_MAX_AGE:
            return None, None
        return s, ts
    except Exception:  # noqa: BLE001 — відсутній/пошкоджений файл
        return None, None


def check_stock(items: list) -> list:
    """Рядки, яких за свіжим знімком уже нема або менше, ніж замовлено: [{id, name, requested, available}]. Невідомий залишок (нема знімка/старий/id немає у знімку) → рядок НЕ чіпаємо."""
    snap, _ts = stock_snapshot()
    need = {}
    for it in items:
        need.setdefault(it["toysi_code"], {"name": it["name"], "requested": 0})["requested"] += it["qty"]
    if snap is None:
        snap = {}
        rt_only = True    # знімка нема/застарів: Toysi-рядки не чіпаємо (fail-open), rt- — вважаємо «немає» (fail-closed)
    else:
        rt_only = False
    bad = []
    for pid, v in need.items():
        if rt_only and not is_rt_id(pid):
            continue
        if pid not in snap:
            if is_rt_id(pid):
                bad.append({"id": pid, "name": v["name"], "requested": v["requested"], "available": 0})
            continue
        try:
            avail = int(snap[pid])
        except (TypeError, ValueError):
            if is_rt_id(pid):
                bad.append({"id": pid, "name": v["name"], "requested": v["requested"], "available": 0})
            continue
        if avail < v["requested"]:
            bad.append({"id": pid, "name": v["name"], "requested": v["requested"], "available": max(0, avail)})
    return bad


# Ручний ввід відділення (довідник НП мертвий або покупець не торкнувся підказки — ЦЕ ДОЗВОЛЕНО, див. assets/app.js
# initNpAutocomplete). Реальний інцидент 10.10.2026 (замовлення PT-20261010-142322-afbf46): покупець вписав голе «369» →
# np_branch «м. Київ, 369» → order_router.parse_np_branch (_WAREHOUSE_RE вимагає «відділення»/«відд.»/«№» ПЕРЕД цифрами) не
# витяг номер → shipping_warehouse_id не виставлено → Toysi прийняв замовлення як АДРЕСНУ доставку. Тому тут, ДО складання
# np_branch, розпізнаємо номер у ДВОХ однозначних випадках і віддаємо його так само, як при виборі з автокомпліту
# (np_warehouse_number):
#   1) у тексті лишається ЛИШЕ номер + службові слова («369», «№369», «НП 369», «Нової пошти 369», «369 відділення», «369-е відділення»);
#   2) у тексті РІВНО ОДНА група цифр, і вона впритул до слова-ключа відділення («відділення/відд./НП/Нова пошта/поштомат 369», ключ ПЕРЕД числом) або
#      «369 відділення…» (число НА ПОЧАТКУ, ключ одразу після) — тоді решта тексту (місто, «біля метро») номера не змінює.
# Усе інше (вулиця з номером будинку, кілька чисел, число не біля ключа, «0») НЕ вгадуємо. Голе 5-цифрове число без слова
# «поштомат» теж не беремо: це може бути поштовий індекс. Загальний _WAREHOUSE_RE у order_router навмисно вузький
# (для Prom-тексту «№3» збігалось з чужим відділенням) — його не змінюємо.
# Слова-ключі, що САМІ по собі означають «відділення/поштомат Нової пошти». Цифри ВПРИТУЛ до слова («відділення166», «Нова пошта279») допускаються: ключ не «з'їдає» цифри (літерний клас замість \w), інакше старий _WAREHOUSE_RE зрізав би цифри й слав №6 замість 166.
# Цілими словами: lookbehind (?<!\w) відсікає «автономер»,
# «мономер», «Новоселівка»; «Нова Поштова» вулиця НЕ збігається — форма «пошта» лише в закінченнях називного/родового/…).
_MANUAL_WH_BRANCH_KEYWORD = (
    r"(?<!\w)(?:в[іи]дд?[іи]ле?н[^\W\d_]*|відділ(?![^\W\d_])|відд\.?(?![^\W\d_])|отделен[^\W\d_]*|отд\.?(?![^\W\d_])|нп(?!\w)|н\.\s?п\.?"
    r"|нов(?:а|ої|ою|ую|ая|ой|ой)\s+п(?:ошта|ошти|ошті|оштою|ошту|очта|очты|очте|очтой|очту)(?![^\W\d_])|п[оo]чтомат[^\W\d_]*|поштомат[^\W\d_]*)"
)
# Службові символи/слова, які можуть стояти поруч із номером, коли в тексті НЕ лишається нічого іншого (правило 1): «№369», «#369», «N 369», «номер 369».
_MANUAL_WH_NOISE = _MANUAL_WH_BRANCH_KEYWORD + r"|[№#]|(?<!\w)номер(?!\w)|(?<!\w)n(?!\w)"
_MANUAL_WH_NOISE_RE = re.compile(_MANUAL_WH_NOISE, re.IGNORECASE)
_MANUAL_WH_ORDINAL_RE = re.compile(r"(?<=\d)\s*-?\s*(?:е|є)(?!\w)", re.IGNORECASE)          # «369-е відділення»
_MANUAL_WH_PUNCT_RE = re.compile(r"[^\w\s№#.]")                                                 # «369!», «(369)», ««369»», «-»
# Правило 2 — ТІЛЬКИ слова-ключі відділення (БЕЗ «#», «номер», «n» і голого «№»: «домофон #1234», «кв. №5», «будинок номер 5» — це адреса).
_MANUAL_WH_BEFORE_RE = re.compile(rf"{_MANUAL_WH_BRANCH_KEYWORD}\s*[№#]?\s*(\d+)(?!\d)", re.IGNORECASE)
_MANUAL_WH_AFTER_RE = re.compile(rf"^\s*(\d+)\s*{_MANUAL_WH_BRANCH_KEYWORD}", re.IGNORECASE)
# Інший перевізник у тексті («5 відділення Укрпошти», «Justin відділення 5») — номер не наш, нічого не вгадуємо.
_MANUAL_WH_OTHER_CARRIER_RE = re.compile(
    r"укр\.?\s*пошт|justin|джастін|meest|міст\s*експрес|делів|delivery|ін\s*тайм|intime|rozetka|розетка|самовивіз|самовывоз|(?<!\w)сат(?!\w)|(?<!\w)sat(?!\w)",
    re.IGNORECASE,
)


def _manual_warehouse_number(text: str):
    """Номер відділення/поштомата з ручного вводу (див. коментар вище) або None. «0», кілька чисел, число не біля ключа,
    інший перевізник → None; 5 цифр лише разом зі словом «поштомат»."""
    raw = text or ""
    if len(raw) > 300 or _MANUAL_WH_OTHER_CARRIER_RE.search(raw):
        return None
    norm = " ".join(_MANUAL_WH_ORDINAL_RE.sub(" ", _MANUAL_WH_PUNCT_RE.sub(" ", raw)).split())
    groups = re.findall(r"\d+", norm)
    if len(groups) != 1 or len(groups[0]) > 5:
        return None
    num = groups[0]
    only_digits = _MANUAL_WH_NOISE_RE.sub(" ", norm).strip(" \t.")
    if only_digits == num:
        ok = True                                                    # правило 1: лишився тільки номер
    else:
        before = _MANUAL_WH_BEFORE_RE.search(norm)
        after = _MANUAL_WH_AFTER_RE.search(norm)
        ok = bool((before and before.group(1) == num) or (after and after.group(1) == num))   # правило 2
    if not ok:
        return None
    value = int(num)
    if value <= 0:
        return None
    if len(num) == 5 and not re.search(r"поштомат", norm, re.IGNORECASE):
        return None                                                  # голе 5-цифрове — можливо індекс, не гадаємо
    return str(value)


def _manual_warehouse_label(original: str, number: str) -> str:
    """Текст відділення для np_branch: «Відділення №N» (формат, який знає parse_np_branch) + ОРИГІНАЛ покупця в дужках —
    щоб при суперечці з покупцем чи Toysi було видно, що саме він вписав (аудит #671: початковий текст не губимо)."""
    kind = "Поштомат №" if re.search(r"поштомат", original or "", re.IGNORECASE) else "Відділення №"
    shown = re.sub(r"[()«»\"\x00-\x1f]", " ", original or "")
    shown = " ".join(shown.split())[:40]
    return f"{kind}{number} (введено вручну: «{shown}»)"


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
    unavailable = check_stock(items)
    if unavailable:
        raise StockChanged(unavailable)

    name = (payload.get("name") or "").strip()
    if len(name) < 3 or len(name.split()) < 2:
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
    rt_cnt = sum(1 for i in items if is_rt_id(i["toysi_code"]))
    if rt_cnt and rt_cnt != len(items):
        # змішаний кошик (Toysi + RoyalToys): у них різні постачальники/відправлення, а автоматичного поділу замовлення немає — оплачене замовлення зависло б. Просимо оформити окремо (fail-closed)
        raise OrderError("Товари партнера (RoyalToys) відправляються окремою посилкою: оформіть їх окремим замовленням від інших товарів")
    if payment_method != "prepaid" and any(is_rt_id(i["toysi_code"]) for i in items):
        # fail-closed: у кошику товар партнера RoyalToys — накладений платіж не приймаємо (і невідомий/відсутній спосіб теж), лише картка онлайн
        raise OrderError("Товари партнера відправляються лише за повною передоплатою: оберіть оплату карткою онлайн")

    # Структурні реф-поля НП з автокомпліту фронта: якщо клієнт ОБРАВ місто/відділення зі
    # списку — маємо точний CityRef + № відділення → order_router віддасть Toysi їх НАПРЯМУ,
    # без пошуку в НП (як EVA). Порожні (ручний ввід без вибору) → None → форвард парсить
    # np_branch текстом, як раніше. Валідуємо формат: ref — GUID-подібний, № — цифри.
    _cref = (payload.get("np_city_ref") or "").strip()
    np_city_ref = _cref if re.fullmatch(r"[0-9a-fA-F-]{10,}", _cref) else None
    _wnum = (payload.get("np_warehouse_number") or "").strip()
    np_warehouse_number = _wnum if _wnum.isdigit() else None
    # Ручний ввід («369», «№369», «відд. 369»…) без вибору з автокомпліту: рефа нема, а голе число order_router не розпізнає
    # (див. _manual_warehouse_number) → виставляємо № відділення самі; текст — «Відділення №N (введено вручну: «оригінал»)».
    if np_warehouse_number is None:
        _manual_num = _manual_warehouse_number(warehouse)
        if _manual_num:
            np_warehouse_number = _manual_num
            warehouse = _manual_warehouse_label(warehouse, _manual_num)

    order_id = "PT-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    customer_comment = _clean_comment(payload.get("comment"))
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
        "customer_comment": customer_comment,   # коментар покупця (≤300, очищений); Код зберігає в orders.customer_comment і додає в comment для Toysi
    }
    return order, total


MAX_COMMENT_LEN = 300
_COMMENT_CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")


def _clean_comment(raw) -> str:
    """Коментар покупця: лише рядок; керуючі символи й розриви рядків → пробіл, пробіли стиснуто, обрізано до MAX_COMMENT_LEN. Невалідний тип (не рядок) → порожньо.
    Це текст для людини (менеджер Toysi, власник): без HTML-довіри — екранування робить споживач (Telegram тощо)."""
    if not isinstance(raw, str):
        return ""
    s = _COMMENT_CTRL_RE.sub(" ", raw)
    s = " ".join(s.split())
    return s[:MAX_COMMENT_LEN].strip()


def _promo_code_allowed(conn, phone: str) -> bool:
    """PLUTUS5: 1 використання на телефон. Рахуємо замовлення сайту з promo_code=PLUTUS5, не скасовані; передоплатні НЕОПЛАЧЕНІ (покупець не дійшов до оплати) не рахуються."""
    since = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    row = conn.execute(
        """SELECT 1 FROM orders WHERE platform = 'site' AND promo_code = ? AND phone = ? AND (status IS NULL OR status NOT LIKE '%cancel%')
             AND (payment_method != 'prepaid' OR payment_confirmed = 1 OR created_at >= ?) LIMIT 1""",
        (site_promo.LABEL_CODE, phone, since),
    ).fetchone()
    return row is None


def _clean_code(code) -> str:
    """Нерядковий промокод (число/об'єкт) не має валити запит 500: це просто «нема коду»."""
    return code.strip() if isinstance(code, str) else ""


PROMO_MESSAGES = {
    "unknown": "Такого промокоду не існує.",
    "used": "Цей промокод уже використано з цього номера телефону.",
    "not_applicable": "Промокод дійсний, але до цього кошика знижка не застосовується (товари дешеві, або діє більша автоматична знижка).",
    "unavailable": "Знижки тимчасово недоступні, спробуйте пізніше.",
}


def quote_promo(items: list, code, phone, strict: bool = False) -> dict:
    """Розрахунок знижки для кошика БЕЗ створення замовлення (читання БД: ліміт по телефону). items: [{toysi_code, qty, price}] каталожні. Повертає (res, new_items, new_total).
    strict=True (шлях /api/order): збій перевірки ліміту → код недоступний (fail-closed); quote для UI толерантний (показує знижку, а замовлення все одно перевірить)."""
    code = _clean_code(code)
    allowed = True
    if code and phone:
        try:
            with get_connection() as conn:
                allowed = _promo_code_allowed(conn, phone)
        except Exception as e:  # noqa: BLE001
            print(f"[site_order_api] promo limit check: {e}", file=sys.stderr)
            if strict:
                return {"applied": None, "lines": {}, "discount_total": 0, "code_status": "unavailable", "costs_available": True}, items, sum(int(i["price"]) * int(i["qty"]) for i in items)
    res = site_promo.compute(items, site_promo.load_cost_map(), code, allowed)
    new_items, new_total = site_promo.apply_to_items(items, res)
    return res, new_items, new_total


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
                # limit 50: у «Іванівка» 37 збігів, «Михайлівка» 40 — з 8 покупець зі свого села його не бачив би (аудит #682); випадайка скролиться (.ac max-height)
                cities = nova_poshta.search_cities((q.get("q") or [""])[0], limit=50)
                return self._json(200, {"cities": cities})
            if parsed.path == "/api/np/warehouse":
                city_ref = (q.get("city_ref") or [""])[0]
                whs = nova_poshta.search_warehouses(city_ref, (q.get("q") or [""])[0])
                return self._json(200, {"warehouses": whs})
        except nova_poshta.NovaPoshtaAPIError as e:
            # внутрішні подробності НП («FindByString is not specified» тощо) — лише в лог, не покупцеві (тестувальник 12.10); «не задано параметр» = порожній результат, не збій НП
            print(f"[site_order_api] {self.command} {self.path}: NP: {e}", file=sys.stderr)
            if "is not specified" in str(e) and ("FindByString" in str(e) or "CityRef" in str(e)):
                return self._json(200, {"cities": []} if parsed.path == "/api/np/city" else {"warehouses": []})
            return self._json(502, {"error": "Нова Пошта тимчасово недоступна"})
        except Exception as e:
            print(f"[site_order_api] {self.command} {self.path}: {e}", file=sys.stderr)
            return self._json(500, {"error": "внутрішня помилка"})
        return self._json(404, {"error": "невідомий ендпоінт"})

    # ── POST: замовлення + колбек LiqPay ──
    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/promo/quote":
            return self._handle_promo_quote()
        if parsed.path == "/api/order":
            return self._handle_order()
        if parsed.path == "/api/liqpay/callback":
            return self._handle_callback()
        return self._json(404, {"error": "невідомий ендпоінт"})

    def _handle_promo_quote(self):
        """POST /api/promo/quote {items:[{id,qty}], promo_code?, phone?} → розрахунок знижки для UI (без створення замовлення). Нічого не пише в БД."""
        payload = self._read_json()
        try:
            pm = price_map()
            raw = payload.get("items")
            if not pm or not isinstance(raw, list) or not raw or len(raw) > MAX_ITEMS:
                raise OrderError("Кошик порожній або каталог тимчасово недоступний")
            items = []
            for it in raw:
                it = it if isinstance(it, dict) else {}
                pid = str(it.get("id", "")).strip()
                try:
                    qty = int(it.get("qty", 0))
                except (TypeError, ValueError):
                    qty = 0
                if pid not in pm or qty < 1 or qty > MAX_QTY_PER_ITEM:
                    raise OrderError(f"Некоректна позиція {pid}")
                items.append({"toysi_code": pid, "name": pm[pid]["name"], "qty": qty, "price": pm[pid]["price"]})
            phone = _norm_phone(payload.get("phone") or "")
            res, new_items, new_total = quote_promo(items, payload.get("promo_code"), phone)       # _clean_code всередині
        except OrderError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            print(f"[site_order_api] promo quote: {e}", file=sys.stderr)
            return self._json(500, {"error": "внутрішня помилка"})
        return self._json(200, {
            "applied": res["applied"], "code_status": res["code_status"], "message": PROMO_MESSAGES.get(res["code_status"], ""),
            "discount_total": res["discount_total"], "list_total": sum(i["price"] * i["qty"] for i in items), "total": new_total,
            "lines": [{"id": i["toysi_code"], "qty": i["qty"], "list_price": i["list_price"], "unit_price": i["price"]} for i in new_items]})

    def _handle_order(self):
        payload = self._read_json()
        try:
            order, total = build_order(payload)
            code = _clean_code(payload.get("promo_code"))
            res, new_items, new_total = quote_promo(order["items"], code, order.get("phone"), strict=True)
            if code and res["code_status"] in ("unknown", "used", "not_applicable", "unavailable"):
                # покупець ввів код, але знижки НЕ буде: не створюємо замовлення, щоб сума не стала сюрпризом (UI показує причину й пропонує продовжити без коду)
                return self._json(400, {"code": "promo_" + res["code_status"], "error": PROMO_MESSAGES[res["code_status"]]})
            if res["applied"]:
                order["items"], total = new_items, new_total
                order["promo_code"] = res["applied"]
                order["promo_discount_total"] = res["discount_total"]
            _exp = _expected_total(payload.get("expected_total"))
            if _exp is not None and _exp != total:
                # ціни змінились між кошиком і оформленням (ребілд сайту, акція почалась/закінчилась, Toysi підняв ціну): НЕ створюємо замовлення й НЕ списуємо суму, якої покупець не бачив
                return self._json(409, {"code": "price_changed", "error": "Ціни в кошику змінились: перевірте оновлену суму.", "expected_total": _exp, "total": total,
                                        "lines": [{"id": i["toysi_code"], "name": i["name"], "qty": i["qty"], "price": i["price"]} for i in order["items"]]})
            _check_cod_ceiling(order["payment_method"], total)
        except StockChanged as e:
            # НЕ створюємо замовлення: UI (Shopify-code) показує «щойно закінчилось», прибирає/зменшує лише ці рядки й просить підтвердити решту
            return self._json(409, {"code": "stock_changed", "error": "Деяких товарів уже немає в такій кількості: оновіть кошик.", "lines": e.lines})
        except OrderError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            print(f"[site_order_api] {self.command} {self.path}: {e}", file=sys.stderr)
            return self._json(500, {"error": "внутрішня помилка"})

        try:
            with get_connection() as conn:
                if order.get("promo_code") == site_promo.LABEL_CODE:
                    conn.execute("BEGIN IMMEDIATE")      # блокування запису: перевірка ліміту й вставка атомарні (проти гонки двох замовлень з одним телефоном)
                    if not _promo_code_allowed(conn, order["phone"]):
                        conn.rollback()
                        return self._json(400, {"code": "promo_used", "error": PROMO_MESSAGES["used"]})
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
        if order["payment_method"] == "cod":
            _notify_new_order(order, total)     # передоплата — після підтвердження оплати в колбеку (неоплачене не є «надходженням»)

        resp = {"order_id": order["order_id"], "total": total,
                "payment_method": order["payment_method"]}
        if order["payment_method"] == "cod":
            # Накладений платіж — онлайн-оплата не потрібна; замовлення прийнято одразу.
            resp["liqpay"] = None
            resp["message"] = "Замовлення прийнято. Оплата при отриманні на Новій Пошті."
            return self._json(200, resp)
        # prepaid: форма LiqPay, якщо ключі задані; інакше — фронт покаже, що онлайн-оплата недоступна
        _mp = liqpay_client.mode_problem() if liqpay_client.is_configured() else ""
        if _mp:
            print(f"[site_order_api] ⚠️ LiqPay: чекаут НЕ побудовано: {_mp}", file=sys.stderr)
            _notify(f"💳 LiqPay: чекаут для site_{order['order_id']} НЕ побудовано: {_mp}. Виправ LIQPAY_* у .env на VPS і перезапусти site-order-api.")
            _notify_new_order(order, total, note="ОЧІКУЄ ОПЛАТУ, чекаут LiqPay недоступний (розбіжність режиму/ключів): звʼяжіться з покупцем")   # єдиний канал, що доходить у Telegram за режимом «лише замовлення»
        if liqpay_client.is_configured() and not _mp:
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
            if res.get("pending") or res.get("reversed"):
                _alert_liqpay_status(res)
            return self._text(200, f"ok (status={res['status']})")  # не оплачено — просто підтверджуємо прийом
        # валюта: чекаут будується в UAH; інша (або відсутня) = розбіжність, не підтверджуємо (money-safety, як і суму нижче)
        if str(res.get("currency") or "").upper() != liqpay_client.CURRENCY:
            return self._text(400, f"currency mismatch: got {res.get('currency')}, expected {liqpay_client.CURRENCY}")

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
                newly_confirmed = mark_payment_confirmed(conn, internal)  # ідемпотентно; 1 лише при атомарному переході 0→1
                set_liqpay_payment_id(conn, internal, (res.get("data") or {}).get("payment_id"))   # для тексту чека (номер платежу LiqPay)
                is_test = res["status"] == "sandbox"
                if is_test:
                    # ТЕСТОВИЙ платіж (пісочниця): гроші не рухались, тож замовлення НЕ має йти в Toysi й під фіскальний чек (раніше sandbox-оплата підтверджувалась і форвардилась як справжня)
                    conn.execute("UPDATE orders SET status = 'sandbox_test' WHERE internal_order_id = ? AND forwarded_to_toysi_at IS NULL", (internal,))
                conn.commit()
                if newly_confirmed:
                    _notify_new_order(order, expected, test=is_test, note=_test_order_preview(order, expected) if is_test else "")   # перше підтвердження оплати = надходження передоплаченого замовлення
        except Exception as e:
            print(f"[site_order_api] callback помилка: {e}", file=sys.stderr)
            return self._text(500, "internal error")
        return self._text(200, "ok")


def run():
    init_db()  # гарантуємо схему/міграції (site-платформа) на цільовій orders.db
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    mode = "SANDBOX" if liqpay_client.LIQPAY_SANDBOX else "LIVE"
    configured = "ключі є" if liqpay_client.is_configured() else "БЕЗ ключів (заглушка)"
    _mp = liqpay_client.mode_problem()
    print(f"[site_order_api] http://{HOST}:{PORT}  SITE_DIR={SITE_DIR}  LiqPay={mode} ({configured})" + (f"  ⚠️ РОЗБІЖНІСТЬ РЕЖИМУ/КЛЮЧІВ: {_mp}" if _mp else ""))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    run()
