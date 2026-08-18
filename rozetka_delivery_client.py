"""rozetka_delivery_client.py — клієнт ROZETKA Delivery API (octopus).

НАВІЩО (2026-08-17/18): для замовлень Rozetka з доставкою в точки видачі Rozetka
МИ самі формуємо відправлення (ТТН) через ROZETKA Delivery — підтверджено Toysi
(«ТТН формуєте Ви», див. rozetka.md). Потік: create_track → get_label (маркування) →
передати маркування Toysi в чат (telegram_userbot_client) + Toysi order_create з
перевізником "Rozetka", відправник Київ Алматинська 4.

API: rz-delivery-octopus.rozetka.ua (Swagger /api/docs/, реальна спека — rozetka.md).
Той самий патерн, що nova_poshta.py: креденшели з .env, requests, свій клас помилки з
самодіагностикою; порожній результат пошуку ≠ помилка API.

АВТЕНТИФІКАЦІЯ: Bearer JWT (72г від останнього використання). Статичний токен партнера
(не протухає) → login-as дає робочий JWT; на 401 — рефреш і один ретрай.

ПОТРІБНО в .env (власник підключає ROZETKA Delivery в кабінеті й дає креденшели):
    ROZETKA_DELIVERY_STATIC_TOKEN=...            # бажано (статичний токен партнера)
    # АБО phone/password:
    ROZETKA_DELIVERY_PHONE=...  ROZETKA_DELIVERY_PASSWORD=...
    # відправник (Toysi-точка Rozetka «Київ, Алматинська, 4») — реквізити ВІДПРАВНИКА:
    ROZETKA_DELIVERY_SENDER_CITY=Київ
    ROZETKA_DELIVERY_SENDER_DEPARTMENT=<id точки-відправника, резолв find_departments>
    ROZETKA_DELIVERY_SENDER_NAME=Plutonix
    ROZETKA_DELIVERY_SENDER_PHONE=+380...

БЕЗПЕКА: креденшели лише з .env, у код/лог не потрапляють. JWT у пам'яті процесу.

CLI:
    python rozetka_delivery_client.py --verify           # перевірити авторизацію (GET /auth/verify)
    python rozetka_delivery_client.py --find-dept "Київ" # знайти точки видачі (резолв department id)
"""
import argparse
import base64
import os
import sys

import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

BASE_URL = os.environ.get("ROZETKA_DELIVERY_BASE_URL", "https://rz-delivery-octopus.rozetka.ua")
STATIC_TOKEN = os.environ.get("ROZETKA_DELIVERY_STATIC_TOKEN", "").strip()
PHONE = os.environ.get("ROZETKA_DELIVERY_PHONE", "").strip()
PASSWORD = os.environ.get("ROZETKA_DELIVERY_PASSWORD", "").strip()
REQUEST_TIMEOUT = 30

SENDER = {
    "city": os.environ.get("ROZETKA_DELIVERY_SENDER_CITY", "Київ").strip(),
    "department": os.environ.get("ROZETKA_DELIVERY_SENDER_DEPARTMENT", "").strip(),
    "name": os.environ.get("ROZETKA_DELIVERY_SENDER_NAME", "Plutonix").strip(),
    "phone": os.environ.get("ROZETKA_DELIVERY_SENDER_PHONE", "").strip(),
}


class RozetkaDeliveryError(Exception):
    """API ROZETKA Delivery явно відмовив (нема креденшелів, мережа, HTTP-помилка,
    протухла авторизація). На відміну від порожнього результату пошуку — це не
    нормальний стан, викликач має повідомити про проблему, а не «не знайдено»."""


_jwt_cache = {"token": None}


def _authenticate() -> str:
    """Отримати робочий JWT: статичний токен → login-as (бажано), інакше phone/password."""
    try:
        if STATIC_TOKEN:
            resp = requests.post(f"{BASE_URL}/api/auth/login-as",
                                 json={"token": STATIC_TOKEN}, timeout=REQUEST_TIMEOUT)
        elif PHONE and PASSWORD:
            resp = requests.post(f"{BASE_URL}/api/auth/login",
                                 json={"phone": PHONE, "password": PASSWORD}, timeout=REQUEST_TIMEOUT)
        else:
            raise RozetkaDeliveryError(
                "нема креденшелів: задай ROZETKA_DELIVERY_STATIC_TOKEN (бажано) "
                "АБО ROZETKA_DELIVERY_PHONE+PASSWORD у .env. Спершу підключи ROZETKA Delivery в кабінеті.")
    except requests.RequestException as e:
        raise RozetkaDeliveryError(f"помилка з'єднання при авторизації: {e}") from e
    if not resp.ok:
        raise RozetkaDeliveryError(
            f"авторизація відхилена (HTTP {resp.status_code}): {resp.text[:200]} — "
            f"перевір {'статичний токен' if STATIC_TOKEN else 'phone/password'} у .env")
    data = resp.json()
    token = data.get("access_token") or data.get("token")
    if not token:
        raise RozetkaDeliveryError(f"у відповіді авторизації немає access_token: {str(data)[:200]}")
    return token


def _jwt(force: bool = False) -> str:
    if _jwt_cache["token"] and not force:
        return _jwt_cache["token"]
    _jwt_cache["token"] = _authenticate()
    return _jwt_cache["token"]


def _request(method: str, path: str, *, json=None, params=None, _retry: bool = True):
    """Запит із Bearer JWT. На 401 — один рефреш+ретрай (JWT протух). Самодіагностичні помилки."""
    headers = {"Authorization": f"Bearer {_jwt()}", "Content-Language": "uk"}
    try:
        resp = requests.request(method, f"{BASE_URL}{path}", headers=headers,
                                json=json, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise RozetkaDeliveryError(f"{method} {path}: помилка з'єднання: {e}") from e
    if resp.status_code == 401 and _retry:
        _jwt(force=True)  # JWT протух (72г) — рефреш і один ретрай
        return _request(method, path, json=json, params=params, _retry=False)
    if not resp.ok:
        raise RozetkaDeliveryError(f"{method} {path} → HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except ValueError as e:
        raise RozetkaDeliveryError(f"{method} {path}: відповідь не JSON: {resp.text[:200]}") from e


def verify() -> dict:
    """GET /api/auth/verify — перевірка авторизації/партнера (health-check для --verify)."""
    return _request("GET", "/api/auth/verify")


def find_departments(city: str, query: str = "") -> list:
    """GET /api/department — точки видачі Rozetka (для резолву recipient/sender department id).
    Порожній результат — НЕ помилка (просто нічого не знайдено)."""
    params = {"city": city}
    if query:
        params["search"] = query
    data = _request("GET", "/api/department", params=params)
    return data.get("data", data) if isinstance(data, dict) else data


def create_track(visible_id: str, recipient: dict, params: dict, *,
                 delivery_payer: str = "receiver", cost: float = 0, insurance_cost: float = 0,
                 places: int = 1, track_type: str = "dept-dept") -> dict:
    """POST /api/track — створити відправлення ROZETKA Delivery. Повертає {track_id, ...}.

    visible_id  — наш номер замовлення (для звірки).
    recipient   — {city, first_name, last_name, phone, department} (обрана клієнтом точка Rozetka).
    params      — {weight, length, width, height} габарити.
    delivery_payer — 'receiver' (клієнт платить доставку) чи 'sender'.
    cost        — сума накладеного платежу (>0 якщо післяоплата), інакше 0.
    track_type  — 'dept-dept' (точка-точка: Toysi-точка → точка клієнта) за замовч.

    Відправник — з .env (SENDER). Якщо не заданий SENDER['department'] — помилка (щоб не
    створити відправлення без коректного пункту відправлення Toysi «Алматинська, 4»)."""
    if not SENDER["department"]:
        raise RozetkaDeliveryError(
            "не заданий ROZETKA_DELIVERY_SENDER_DEPARTMENT (пункт-відправник Toysi «Київ, Алматинська, 4»). "
            "Резолвни його через `--find-dept \"Київ\"` і додай id у .env.")
    for f in ("weight", "length", "width", "height"):
        if not params.get(f):
            raise RozetkaDeliveryError(f"у params немає габариту '{f}' — ROZETKA Delivery вимагає вагу й розміри")
    body = {
        "visible_id": str(visible_id),
        "type": track_type,
        "places": places,
        "delivery_payer": delivery_payer,
        "cost": cost,
        "insurance_cost": insurance_cost,
        "params": params,
        "sender": {
            "city": SENDER["city"],
            "department": SENDER["department"],
            "first_name": SENDER["name"],
            "last_name": "",
            "phone": SENDER["phone"],
        },
        "recipient": recipient,
    }
    return _request("POST", "/api/track", json=body)


def get_label(track_id) -> bytes:
    """GET /api/track/{id}/label — PDF-етикетка (маркування). Повертає БАЙТИ PDF (декодований base64)."""
    data = _request("GET", f"/api/track/{track_id}/label")
    b64 = data.get("label") if isinstance(data, dict) else None
    if not b64:
        raise RozetkaDeliveryError(f"у відповіді label немає поля 'label': {str(data)[:200]}")
    try:
        return base64.b64decode(b64)
    except (ValueError, TypeError) as e:
        raise RozetkaDeliveryError(f"label не декодується з base64: {e}") from e


def get_status(track_id) -> dict:
    """GET /api/track/{id}/status — статуси відправлення (для order_status_tracker)."""
    return _request("GET", f"/api/track/{track_id}/status")


def _main() -> None:
    ap = argparse.ArgumentParser(description="Клієнт ROZETKA Delivery API (octopus).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--verify", action="store_true", help="перевірити авторизацію (GET /auth/verify)")
    g.add_argument("--find-dept", metavar="CITY", help="знайти точки видачі в місті (резолв department id)")
    g.add_argument("--status", metavar="TRACK_ID", help="статус відправлення")
    args = ap.parse_args()
    try:
        if args.verify:
            info = verify()
            # друкуємо лише неконфіденційні поля партнера
            safe = {k: info.get(k) for k in ("id", "merchant_id", "status", "name") if k in info}
            print(f"[RzDelivery] Авторизація OK. Партнер: {safe}")
        elif args.find_dept:
            depts = find_departments(args.find_dept)
            print(f"[RzDelivery] Знайдено точок: {len(depts)}")
            for d in (depts or [])[:20]:
                print(f"  id={d.get('id')}  {d.get('name')}  {(d.get('location') or {})}")
        elif args.status:
            st = get_status(args.status)
            print(f"[RzDelivery] Статус {args.status}: {st.get('last_status')}")
    except RozetkaDeliveryError as e:
        print(f"[RzDelivery] ПОМИЛКА: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main()
