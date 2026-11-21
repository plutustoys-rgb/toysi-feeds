# -*- coding: utf-8 -*-
"""kiddisvit_api.py — клієнт B2B API KIDDISVIT V2 (https://b2b.kiddisvit.ua/api/v2), ЛИШЕ ЧИТАННЯ каталогу, цін клієнта і залишків.

Джерело правди про методи: ДОВІДНИК_KIDDISVIT_API.md (Продажник, 16.11.2026) + живі проби 17.11.2026 (clients/list, catalog/products, catalog/brands, catalog/categories,
products/rests/prices, products/rests: усі HTTP 200). Менеджер Т. Сіденко 17.11 13:45: «не більше 25 запитів за 10 сек, ліміти не знімаємо» — клієнт тримає ≤ ~1,4 запиту/с (MIN_INTERVAL_S=0,7).
Cloudflare перед API відхиляє запити без власного User-Agent (помилка 1010) — тому заголовок обов'язковий. Ключ (Bearer) — з env KIDDISVIT_API_KEY (.env на VPS, ставить власник); НІКОЛИ не друкується й не потрапляє в винятки/логи.
Запис (orders/create, orders/delivery/ttn-data, orders/assembly) — методами нижче, ЛИШЕ за воротами KV_ORDERS_LIVE=1 (типово вимкнено; слово власника 21.11.2026 «будувати»; перше живе замовлення — пробне власника), без автоповторів (POST не ідемпотентний); логіка замовлень — kiddisvit_orders.py."""
import http.client
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

try:                              # як parser.py/royaltoys_parser.py: .env з робочої теки (VPS: /opt/plutustoys/.env); нема python-dotenv → лише змінні середовища
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))      # явний шлях: поруч із модулем (на VPS /opt/plutustoys/.env)
except ImportError:               # pragma: no cover
    pass

BASE_URL = os.environ.get("KIDDISVIT_API_BASE", "https://b2b.kiddisvit.ua/api/v2").rstrip("/")
USER_AGENT = "PlutusToys-b2b-client/1.0"
MIN_INTERVAL_S = 0.7           # 25 запитів / 10 с = 2,5/с дозволено (менеджер 17.11); 1/0,7 ≈ 1,4/с — запас майже вдвічі
TIMEOUT_S = 180                # catalog/products ≈ 26 МБ
MAX_RETRIES = 4
MAX_RETRY_AFTER_S = 60.0
RETRY_STATUSES = (429, 500, 502, 503, 504)


class KiddisvitError(Exception):
    """Збій API. У повідомленні немає ключа й тіла відповіді (лише код і шлях)."""


# HTTP-коди запису, які однозначно означають «відхилено ДО обробки, замовлення НЕ створене» (валідація/авторизація/ліміт). Усе інше при запису (2xx крім чистого 200+success, 3xx, 408, 409, 5xx, обрив) = результат НЕВІДОМИЙ.
WRITE_DEFINITE_REJECT = (400, 401, 403, 404, 405, 415, 422, 429)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None            # POST із ключем НЕ переходить за Location (urllib для 301/302 перейшов би GET-ом і переслав Authorization)


class KiddisvitUncertain(KiddisvitError):
    """POST без автоповтору: запит міг дійти до KIDDISVIT, але результат невідомий (таймаут/обрив/5xx). Замовлення могло бути створене — автоповтору НЕМАЄ, лише людина (звірити в кабінеті/orders/search)."""


class KiddisvitClient:
    def __init__(self, key: str | None = None, base_url: str | None = None, opener=None, sleep=time.sleep, clock=time.monotonic):
        self._key = (key if key is not None else os.environ.get("KIDDISVIT_API_KEY", "")).strip()
        self.base = (base_url or BASE_URL).rstrip("/")
        self._opener = opener or self._urlopen
        self._write_opener = opener or self._urlopen_write
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._last = None

    @staticmethod
    def _urlopen(req, timeout):
        return urllib.request.urlopen(req, timeout=timeout)

    @staticmethod
    def _urlopen_write(req, timeout):
        return urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout)

    def has_key(self) -> bool:
        return bool(self._key)

    def _throttle(self) -> None:
        with self._lock:
            now = self._clock()
            if self._last is not None:
                wait = MIN_INTERVAL_S - (now - self._last)
                if wait > 0:
                    self._sleep(wait)
            self._last = self._clock()

    def _request(self, method: str, path: str, body: dict | None = None, as_is: bool = False, retry: bool = True):
        """as_is=True — повернути розібраний JSON як є (orders/assembly відповідає {"success": …} без обгортки status/data). retry=False — БЕЗ автоповторів і з KiddisvitUncertain на
        невизначених збоях (POST orders/*: повтор міг би створити друге замовлення)."""
        p_ = path.lstrip("/")
        if method.upper() != "GET" and p_.startswith("orders/") and p_.split("?")[0].rstrip("/") != "orders/search":      # ТОЧНИЙ збіг (аудит #1933: префікс пропускав «orders/searchX/../create»)
            self._need_live(p_)          # ворота діють на рівні _request, а не лише в обгортках (аудит #1933): будь-який POST orders/* крім search — лише за KV_ORDERS_LIVE=1 і без автоповторів
            retry = False
        if not self._key:
            raise KiddisvitError("KIDDISVIT_API_KEY не задано")
        url = f"{self.base}/{path.lstrip('/')}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Authorization": "Bearer " + self._key, "Accept": "application/json", "User-Agent": USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/json"
        last_status = None
        retries = MAX_RETRIES if retry else 0
        for attempt in range(retries + 1):
            self._throttle()
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with (self._opener if retry else self._write_opener)(req, TIMEOUT_S) as r:
                    raw = r.read().decode("utf-8", "replace")
                    status = getattr(r, "status", 200)
            except urllib.error.HTTPError as e:
                status, raw = e.code, ""
                retry_after = e.headers.get("Retry-After") if getattr(e, "headers", None) else None
                if status in RETRY_STATUSES and attempt < retries:
                    last_status = status
                    try:
                        delay = float(retry_after) if retry_after else 0.0
                        delay = min(max(delay, 1.0), MAX_RETRY_AFTER_S) if math.isfinite(delay) and delay > 0 else 0.0     # стеля: Retry-After 86400/inf/nan не вішає прогін
                    except ValueError:
                        delay = 0.0
                    self._sleep(max(delay, 5.0 * (attempt + 1)))
                    continue
                if not retry and status not in WRITE_DEFINITE_REJECT:
                    raise KiddisvitUncertain(f"{method} {path}: HTTP {status} (результат невідомий, автоповтору нема)") from None
                raise KiddisvitError(f"{method} {path}: HTTP {status}") from None
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:      # HTTPException: IncompleteRead на великих відповідях (catalog/products ≈26 МБ) — не OSError
                last_status = type(e).__name__
                if attempt < retries:
                    self._sleep(5.0 * (attempt + 1))
                    continue
                if not retry:
                    raise KiddisvitUncertain(f"{method} {path}: {type(e).__name__} (результат невідомий, автоповтору нема)") from None
                raise KiddisvitError(f"{method} {path}: {type(e).__name__}") from None
            if status != 200:
                if not retry:
                    raise KiddisvitUncertain(f"{method} {path}: HTTP {status} (не 200: результат невідомий, автоповтору нема)")      # 201/202/204/3xx при записі = можливо створено
                raise KiddisvitError(f"{method} {path}: HTTP {status}")
            try:
                d = json.loads(raw)
            except ValueError:
                if not retry:
                    raise KiddisvitUncertain(f"{method} {path}: HTTP 200, але відповідь не JSON (результат невідомий)") from None
                raise KiddisvitError(f"{method} {path}: відповідь не JSON") from None
            if as_is:
                return d
            if not isinstance(d, dict) or d.get("status") != "success":
                msg = f"{method} {path}: status={str(d.get('status') if isinstance(d, dict) else type(d).__name__)[:30]}"
                if not retry:
                    raise KiddisvitUncertain(msg + " (запис: 200 без status=success — перевірте в кабінеті/orders/search, автоповтору нема)")
                raise KiddisvitError(msg)
            return d.get("data")
        raise KiddisvitError(f"{method} {path}: вичерпано спроби (останній збій {last_status})")

    # ---- читання -------------------------------------------------------------------------------------------------------------------------
    def clients_list(self) -> dict:
        """clients/list: {user,…,clients:[{ClientID,ContractID,OrganizationID,…}],shops:[{shopID,…}]}."""
        return self._request("GET", "clients/list")

    def brands(self) -> list:
        """catalog/brands: [{ID, Name, brand_id}]."""
        return self._request("GET", "catalog/brands") or []

    def categories(self, locale: str = "ua") -> list:
        """catalog/categories/{locale}: [{id, id_crm, parentID, name}]."""
        return self._request("GET", f"catalog/categories/{urllib.parse.quote(locale)}") or []

    def products(self, locale: str = "ua", in_stock: bool = True, updated_at: str = "2000-01-01 00:00:00") -> list:
        """catalog/products/{locale}/{stock_status}/{updated_at} (GET; перевірено 17.11): весь каталог одним запитом ≈26 МБ. Елемент: productID, name, description(HTML), brand_name, brand_id, barcode, sku, price (РРЦ, акційна якщо діє), price_old, url, category_id, images[], properties[{name,value}]."""
        path = f"catalog/products/{urllib.parse.quote(locale)}/{1 if in_stock else 0}/{urllib.parse.quote(updated_at)}"
        d = self._request("GET", path) or {}
        return d.get("products") or []

    def prices_by_brand(self, client_id: str, contract_id: str, brand_id: str) -> list:
        """products/rests/prices для ВСІХ товарів бренду (без списку id і без warehouses: з warehouses відповідь порожня — перевірено 17.11).
        Елемент: ProductID, ProductArticle, clientPrice (наша закупівля), retailPrice, clientPriceWithoutAction, retailPriceWithoutAction, startAction, endAction, rest[{WarehouseID,Rest}]."""
        d = self._request("POST", "products/rests/prices", {"clientID": client_id, "ContractID": contract_id, "brands": [{"brandID": brand_id}]}) or {}
        return d.get("products") or []

    def rests(self) -> list:
        """products/rests: [{ProductID, rest:[{WarehouseID, Rest}]}] — залишки всього каталогу."""
        d = self._request("GET", "products/rests") or {}
        return d.get("products") or []

    # ---- запис (ТІЛЬКИ за воротами KV_ORDERS_LIVE=1; POST без автоповторів) -------------------------------------------------------------------
    @staticmethod
    def orders_live() -> bool:
        return os.environ.get("KV_ORDERS_LIVE", "").strip() == "1"

    def _need_live(self, what: str) -> None:
        if not self.orders_live():
            raise KiddisvitError(f"{what}: запис у KIDDISVIT вимкнено (KV_ORDERS_LIVE≠1)")

    def orders_create(self, payload: dict) -> dict:
        """POST orders/create → data: {number («КЗ-…»), id, status_OK (1 = сформовано/зарезервовано, 0 = створено, але НЕ зарезервовано), Comment, assembly, …}. Без автоповтору: невизначений збій → KiddisvitUncertain (замовлення могло бути створене)."""
        self._need_live("orders/create")
        d = self._request("POST", "orders/create", payload, retry=False)
        if not isinstance(d, dict) or not d:
            raise KiddisvitUncertain("orders/create: success без data (результат невідомий, автоповтору нема)")
        return d

    def orders_ttn_data(self, order_id: str, documents: list) -> list:
        """POST orders/delivery/ttn-data: documents=[{type: "marking"|"document", BinaryData: base64}]; відповідь — масив [{id, status, errorMessage}]. Без автоповтору."""
        self._need_live("orders/delivery/ttn-data")
        d = self._request("POST", "orders/delivery/ttn-data", {"id": str(order_id), "Documents": documents}, as_is=True, retry=False)
        if isinstance(d, dict) and "data" in d:
            d = d["data"]
        return d if isinstance(d, list) else ([d] if isinstance(d, dict) else [])

    def orders_assembly(self, order_id: str, documents: list) -> dict:
        """POST orders/assembly (ref_Order, Documents…): відповідь {"success": true} або {"success": false, "Error": …}. Лише для клієнтів, яким менеджер дозволив; без автоповтору."""
        self._need_live("orders/assembly")
        d = self._request("POST", "orders/assembly", {"ref_Order": str(order_id), "Documents": documents}, as_is=True, retry=False)
        return d if isinstance(d, dict) else {}

    def orders_search(self, client_id: str, contract_id: str, date_from: str, date_to: str) -> list:
        """POST orders/search (ЧИТАННЯ, ворота не потрібні): [{number, id, status, TTN, assembly, Sum, orderStatuses, …}]. Дати РРРРММДД."""
        d = self._request("POST", "orders/search", {"ClientID": client_id, "ContractID": contract_id, "dateFrom": date_from, "dateTo": date_to}) or {}
        return d.get("Orders") or []


if __name__ == "__main__":
    # Самоперевірка без друку секретів: python kiddisvit_api.py
    c = KiddisvitClient()
    if not c.has_key():
        print("KIDDISVIT_API_KEY не задано", file=sys.stderr)
        sys.exit(2)
    try:
        d = c.clients_list()
        print("OK clients/list: клієнтів", len(d.get("clients") or []), "магазинів", len(d.get("shops") or []))
    except KiddisvitError as e:
        print("ЗБІЙ:", e, file=sys.stderr)
        sys.exit(1)
