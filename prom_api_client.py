"""
prom_api_client.py — надійний, живо-перевірений клієнт для зміни товарів
Prom через POST /products/edit_by_external_id (ціна, delist/деактивація).

СТВОРЕНО (2026-07-21, пряма вимога власниці "роби фікси глобальні, щоб ця
проблема більше не повторювалась"): /products/edit_by_external_id мовчки
підтверджує лише ЧАСТИНУ пачки — HTTP 200, без винятку, `processed_ids` у
відповіді може містити менше id, ніж реально надіслано, БЕЗ жодного запису
в `errors` для решти (підтверджено живо 2026-07-18 на SKU 266990: звіт
показав "delist=359, Помилок: 0", але живий GET одразу після прогону
показав status="on_display" — товар НЕ зник; підтверджено ЗНОВУ 2026-07-21
на SKU 201887/185297 — 3 прогони поспіль `prom_catalog_sync.py` показують
їх "застарілими", кожен прогін відправляє запит на видалення, кожен
"успішний" за HTTP-статусом, товари й досі `status=on_display` живо).

Це вже було виправлено ОДИН раз (2026-07-18) для `prom_competitor_pricer.py`
(delist()/apply_price() при неконкурентній ціні) — жива GET-перевірка після
кожної зміни. Але `prom_catalog_sync.py` (окремий, паралельний привід для
того самого API-виклику — деактивація за відсутністю стоку/випадінням з
топ-970) мав СВОЮ, простішу батч-реалізацію без цієї перевірки, тож той
самий баг Prom API там і далі мовчки з'їдав 80%+ запитів на видалення
щоцикл (07:30: 462/516, 11:30: 21/247, 15:30: 49/283 — реально оброблено).

Єдиний спільний модуль замість двох копій — щоб наступний скрипт, якому
знадобиться змінити товар Prom, отримав цю перевірку АВТОМАТИЧНО, а не
повторив той самий пропуск втретє."""

import os

import requests

PROM_API_KEY = os.environ.get("PROM_API_KEY", "")
PROM_API_URL = "https://my.prom.ua/api/v1"
REQUEST_TIMEOUT = 20


class PromEditError(Exception):
    """edit_by_external_id повернув HTTP 200, але БЕЗ реальної зміни для
    конкретного ID (external_id відсутній у processed_ids, і/чи є запис
    у errors) — HTTP-статус сам по собі НЕ гарантує, що зміна реально
    відбулась."""


def fetch_product_by_external_id(external_id: str) -> dict | None:
    """GET-звірка живого стану ОДНОГО товару. Повертає None і для 404
    (товар видалений/не існує), і для будь-якої мережевої проблеми — той
    самий "не знаємо напевно" default. Навмисно НЕ bulk /products/list
    (той ігнорує фільтр external_id — підтверджено живо 2026-07-19,
    code_report_2026-07-18_pt11.md; підтверджено ЗНОВУ 2026-07-21)."""
    try:
        r = requests.get(
            f"{PROM_API_URL}/products/by_external_id/{external_id}",
            headers={"Authorization": f"Bearer {PROM_API_KEY}"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json().get("product")
    except ValueError:
        return None


def edit_by_external_id(payload: dict) -> None:
    """POST /products/edit_by_external_id для ОДНОГО товару (payload — один
    dict, не список — навмисно, щоб кожен виклик отримував незалежну живу
    перевірку нижче; масовий батч ховає, ЯКІ САМЕ з пачки насправді не
    пройшли), з живою GET-звіркою результату, якщо processed_ids/errors самі
    по собі не дають однозначної відповіді."""
    response = requests.post(
        f"{PROM_API_URL}/products/edit_by_external_id",
        headers={"Authorization": f"Bearer {PROM_API_KEY}"},
        json=[payload],
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError:
        raise PromEditError(f"Невалідна відповідь (не JSON) для {payload['id']}: {response.text[:300]}")
    ext_id = str(payload["id"])
    processed = {str(x) for x in data.get("processed_ids", [])}
    if ext_id in processed:
        return

    detail = (data.get("errors") or {}).get(ext_id) or data.get("errors")
    if detail:
        # Prom дав конкретну причину відхилу — реальна помилка, живий
        # запит нічого не додасть.
        raise PromEditError(f"Prom НЕ підтвердив зміну для {ext_id} (processed_ids порожній/без цього ID): {detail}")

    # Порожній processed_ids БЕЗ жодної деталі помилки — Prom так само
    # відповідає, коли запитувана зміна НІЧОГО не міняє (ціна вже така
    # сама; товар уже status=deleted). Без цієї звірки такий no-op
    # трактувався б як провал назавжди.
    product = fetch_product_by_external_id(ext_id)
    if product is None:
        if payload.get("status") == "deleted":
            # 404 для delist-запиту — найімовірніше, товар уже видалений
            # (Prom не віддає видалені товари через цей ендпоінт,
            # підтверджено живо на 10 SKU 2026-07-19). Уже досягнутий
            # цільовий стан — вважаємо успіхом.
            return
        raise PromEditError(f"Prom НЕ підтвердив зміну для {ext_id} (processed_ids порожній/без деталі помилки), "
                            f"живий GET також не знайшов товар — стан невідомий")

    if payload.get("status") == "deleted":
        if product.get("status") == "deleted":
            return
        raise PromEditError(f"Prom НЕ підтвердив видалення {ext_id}, живий GET показує status="
                            f"{product.get('status')!r} — товар досі не видалений")

    if "price" in payload:
        live_price = product.get("price")
        try:
            already_correct = live_price is not None and abs(float(live_price) - float(payload["price"])) < 1
        except (TypeError, ValueError):
            already_correct = False
        if already_correct:
            return
        raise PromEditError(f"Prom НЕ підтвердив зміну ціни {ext_id}, живий GET показує price="
                            f"{live_price!r} (очікували {payload['price']!r}) — зміна не відбулась")

    raise PromEditError(f"Prom НЕ підтвердив зміну для {ext_id} (processed_ids порожній/без деталі помилки), "
                        f"живий стан не відповідає запитуваній зміні")


def apply_price(external_id: str, price: float) -> None:
    edit_by_external_id({"id": external_id, "price": price})


def delist(external_id: str) -> None:
    """Видалення товару (status="deleted"). Спільна функція для ДВОХ
    незалежних приводів видалення в цьому проєкті: неконкурентна ціна
    (prom_competitor_pricer.py) і випадіння з топ-970/відсутність стоку
    (prom_catalog_sync.py) — обидва мають той самий ризик мовчазного
    відхилу Prom API, тож обидва мають отримувати той самий захист."""
    edit_by_external_id({"id": external_id, "status": "deleted"})
