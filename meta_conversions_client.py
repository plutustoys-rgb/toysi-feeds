"""meta_conversions_client.py — серверна відправка подій у Meta (Facebook/Instagram)
Conversions API. Задача Cowork (STATUS.md найновіше-13/14, 2026-08-13).

НАВІЩО серверний CAPI, а не клієнтський Pixel: вітрина Prom не дає вставити довільний
<script> на сайт (my.prom.ua/cms/web_analytic приймає ЛИШЕ Google Analytics G-ID), тож
браузерний піксель встановити неможливо. Conversions API шле події напряму з нашого
сервера (VPS) у Meta — обходить це обмеження. Meta сама рекомендує CAPI за замовчуванням.

ЩО шлемо: подію `Purchase` у момент передачі замовлення в Toysi (той самий тригер, що й
KODV-запис — order_router.py після mark_forwarded_to_toysi). event_id = internal_order_id
для дедуплікації на боці Meta (якщо колись додасться й браузерний піксель — події зіллються
за event_id, не подвояться).

СЕКРЕТ: `META_CONVERSIONS_API_TOKEN` береться ЛИШЕ з оточення (.env на VPS / .local_secrets),
НІКОЛИ не з коду, чату чи Cowork-папки (той самий принцип, що NovaPay/Toysi-ключі). Без
токена/датасету модуль — тихий no-op (best-effort, як _update_marketplace_status).

Формат .env (VPS `/opt/plutustoys/.env`, значення з `.local_secrets/meta_conversions_api.txt`):
    META_DATASET_ID=1577932053715594
    META_CONVERSIONS_API_TOKEN=<токен>

PII (телефон/ім'я/місто) хешуються SHA-256 з нормалізацією — вимога Meta; сирі персональні
дані НІКОЛИ не йдуть у запит (лише хеші).
"""
import hashlib
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

META_DATASET_ID = os.environ.get("META_DATASET_ID", "").strip()
META_CONVERSIONS_API_TOKEN = os.environ.get("META_CONVERSIONS_API_TOKEN", "").strip()
META_GRAPH_VERSION = "v21.0"
REQUEST_TIMEOUT = 15


def _sha256(value: str) -> str:
    """SHA-256 нормалізованого значення (нижній регістр, без крайніх пробілів) — формат
    хешування user_data, який вимагає Meta CAPI."""
    return hashlib.sha256((value or "").strip().lower().encode("utf-8")).hexdigest()


def _normalize_phone(phone: str) -> str:
    """Телефон у цифри з кодом країни (Meta: E.164 без '+', далі хешується).
    Українські формати: '0XXXXXXXXX' -> '380XXXXXXXXX'; 9 цифр -> '380' + 9;
    вже з '380' лишаємо як є."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    if digits.startswith("380"):
        return digits
    if digits.startswith("0") and len(digits) == 10:
        return "38" + digits           # 0XXXXXXXXX -> 380XXXXXXXXX
    if len(digits) == 9:
        return "380" + digits          # XXXXXXXXX -> 380XXXXXXXXX
    return digits                       # інший формат — віддаємо як є (Meta сама відсіє невалідне)


# Порядок ПІБ у customer_name залежить від площадки — Rozetka/EVA склеюють «Прізвище Ім'я
# По-батькові» (стандарт НП), Prom — «Ім'я Прізвище». Дзеркалить order_router._SURNAME_FIRST_PLATFORMS
# (продубльовано, щоб уникнути циклічного імпорту: order_router імпортує цей модуль). Тримати в
# синхроні з order_router.
_SURNAME_FIRST_PLATFORMS = {"rozetka", "eva"}


def _split_name(full_name: str, platform: str = "") -> tuple:
    """(first, last) з рядка ПІБ з урахуванням порядку джерела. Rozetka/EVA — «Прізвище Ім'я
    По-батькові» (last=1-е слово, first=2-е); решта (Prom, мок, невідоме) — «Ім'я Прізвище».
    По-батькові в Meta advanced matching не використовується (лише fn/ln), тож не повертаємо.
    Дзеркалить order_router._split_recipient_name — без цього fn↔ln у хешах атрибуції реклами
    для Rozetka/EVA переставлялись, знижуючи якість метчингу."""
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if platform in _SURNAME_FIRST_PLATFORMS:
        last = parts[0]
        first = parts[1] if len(parts) > 1 else ""
        return first, last
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _order_total(items: list) -> float:
    return sum(i.get("price", 0) * i.get("qty", 1) for i in (items or []))


def is_configured() -> bool:
    return bool(META_DATASET_ID and META_CONVERSIONS_API_TOKEN)


def send_purchase_event(order: dict) -> bool:
    """Відправляє подію `Purchase` у Meta CAPI для щойно переданого в Toysi замовлення.
    BEST-EFFORT: ніколи не піднімає виняток у викликача (order flow не має ламатись через
    Meta); повертає True лише при успішній відповіді Meta, інакше False (+лог у stderr).
    Тихий no-op (False), якщо датасет/токен не налаштовані."""
    if not is_configured():
        return False
    try:
        items = order.get("items") or []
        first, last = _split_name(order.get("customer_name", ""), order.get("platform", ""))
        phone = _normalize_phone(order.get("phone", ""))
        city, _, _ = ("", "", "")
        raw_branch = order.get("np_branch", "") or ""
        # Місто — перше слово/сегмент np_branch до коми (best-effort, лише для матчингу).
        if raw_branch:
            city = raw_branch.split(",")[0].strip()

        user_data = {}
        if phone:
            user_data["ph"] = [_sha256(phone)]
        if first:
            user_data["fn"] = [_sha256(first)]
        if last:
            user_data["ln"] = [_sha256(last)]
        if city:
            user_data["ct"] = [_sha256(city)]
        if not user_data:
            # Meta вимагає щонайменше одне поле user_data для матчингу — без жодного
            # подія марна; не шлемо (не помилка, просто нема з чим матчити).
            print(f"[meta_capi] {order.get('internal_order_id')}: нема даних для user_data — Purchase не відправлено",
                  file=sys.stderr)
            return False

        content_ids = [str(i.get("toysi_code")) for i in items if i.get("toysi_code")]
        num_items = sum(int(i.get("qty", 1) or 1) for i in items)

        event = {
            "event_name": "Purchase",
            "event_time": int(time.time()),
            "event_id": str(order.get("internal_order_id") or ""),   # дедуплікація на боці Meta
            "action_source": "website",   # замовлення розміщене на сайті маркетплейсу (Prom/EVA)
            "user_data": user_data,
            "custom_data": {
                "currency": "UAH",
                "value": round(_order_total(items), 2),
                "content_ids": content_ids,
                "content_type": "product",
                "order_id": str(order.get("internal_order_id") or ""),
                "num_items": num_items,
            },
        }

        resp = requests.post(
            f"https://graph.facebook.com/{META_GRAPH_VERSION}/{META_DATASET_ID}/events",
            json={"data": [event], "access_token": META_CONVERSIONS_API_TOKEN},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        received = ""
        try:
            received = f" (events_received={resp.json().get('events_received')})"
        except ValueError:
            pass
        print(f"[meta_capi] Purchase відправлено: {order.get('internal_order_id')} "
              f"value={event['custom_data']['value']} UAH{received}")
        return True
    except (requests.exceptions.RequestException, ValueError, TypeError, KeyError) as e:
        # best-effort — Meta-подія НІКОЛИ не має ламати чи блокувати передачу замовлення.
        print(f"[meta_capi] Не вдалось відправити Purchase для {order.get('internal_order_id')}: {e}",
              file=sys.stderr)
        return False
