# -*- coding: utf-8 -*-
"""zoobaza_parser.py — каталог постачальника ZooBaza (товари для тварин) з публічного YML-фіду.

Бриф: SELLER_CHANNEL.md запис (123), 2026-10-03 (власник: «нагагодити автоматизацію від двох постачальників»
— Toysi + ZooBaza). Зразок — royaltoys_parser.fetch_royaltoys_catalog (той самий вигляд запису).

ЦЕЙ МОДУЛЬ ЛИШЕ ЧИТАЄ ФІД. Він НЕ підключений ні до фідів, ні до репрайсера, ні до замовлень:
  • ціна на площадці — `decide_price_for_platform` з комісіями ПО КАТЕГОРІЇ (ставок для цих категорій нема —
    чекаємо аудитора кабінетів; дефолти іграшок тут НЕ застосовувати);
  • замовлення — API у ZooBaza НЕМАЄ (ручна схема: менеджер підтверджує наявність → передоплата B2B → відправка
    від нашого імені), тож «форвард» = повідомлення менеджеру, канал — окреме рішення власника.

ФАКТИ (звірено живо 2026-10-04): https://basmati.com.ua/zoobaza_full.php — 910 offers, 825 available; поля offer:
@id, @available, price, quantity_in_stock, currencyId=UAH, categoryId, picture, vendorCode, vendor, name,
description, param[Цвет]. quantity_in_stock = 10 для available і 0 для недоступних, тобто це ПРАПОРЕЦЬ, а не
залишок — наявність підтверджує менеджер. vendorCode = номер у B2B-каталозі (напр. 305457 «Жилет Барт 35х54»).

СОБІВАРТІСТЬ: `price` у фіді = ОПТ × 1.4 (виведено Продажником з каталогу: 690→966, 1284→1798, 1788→2503;
перевірено тут на двох позиціях). Ціни у фіді цілі → `cost` має похибку до ±0.5 грн. Справжній B2B-прайс — у
персональному каталозі з токеном (токен ТІЛЬКИ в .env, у репо не класти).

КАТЕГОРІЇ: білий список (сумки 1059, лежаки 1057, будки 1056 — за брифом); корми (1083/1086/1087) НІКОЛИ.
«Одяг для тварин» (105501, 71% каталогу) — рішення за Консультантом: прапорець ZOOBAZA_INCLUDE_CLOTHING
(за замовчуванням ВИМКНЕНО). 105502/105503/105504 — лише свідомо через ZOOBAZA_EXTRA_CATEGORIES.
Щотижневий контроль константи НЕ заплановано — викликати assert_cost_constant() з автоматики, коли вона буде.
"""
import math
import os
import sys
from typing import Dict, List

import requests
import xml.etree.ElementTree as ET

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ZOOBAZA_YML_URL = os.environ.get("ZOOBAZA_YML_URL", "https://basmati.com.ua/zoobaza_full.php")
REQUEST_TIMEOUT = 90


def _feed_to_opt() -> float:
    raw = os.environ.get("ZOOBAZA_FEED_TO_OPT", "1.4").strip()
    try:
        v = float(raw)
    except ValueError:
        raise ValueError(f"ZOOBAZA_FEED_TO_OPT='{raw}' — не число (десяткова ТОЧКА, напр. 1.4)")
    if not math.isfinite(v) or v <= 0:
        raise ValueError(f"ZOOBAZA_FEED_TO_OPT={raw} — має бути скінченним числом > 0")
    return v


ZOOBAZA_FEED_TO_OPT = _feed_to_opt()   # price_фіду / ОПТ

FORBIDDEN_CATEGORIES = {"1083", "1086", "1087"}          # корми — не беремо (навіть якщо додати в EXTRA)
# БІЛИЙ список за брифом (SELLER_CHANNEL 123): сумки-переноски, лежаки, будки. Нова/невідома категорія фіду
# (в т.ч. нова кормова) НЕ проходить автоматично. 105502 амуніція / 105503 гігієна / 105504 аксесуари у брифі
# не названі — додаються лише свідомо: ZOOBAZA_EXTRA_CATEGORIES=105502,105503,105504.
ALLOWED_CATEGORIES = {"1059", "1057", "1056"}
EXTRA_CATEGORIES = {c.strip() for c in os.environ.get("ZOOBAZA_EXTRA_CATEGORIES", "").split(",") if c.strip()}
CLOTHING_CATEGORY = "105501"
INCLUDE_CLOTHING = os.environ.get("ZOOBAZA_INCLUDE_CLOTHING", "0").strip() == "1"

# Контрольні SKU для перевірки константи: (підрядок назви, ОПТ з B2B-каталогу).
CONTROL_SKUS = [
    ("Жилет Барт Zoobaza - 35х54, коричневий", 690.0),
    ("Жилет Барт Zoobaza - 72х116, коричневий", 1284.0),
]
# «Челсі 50×60» (опт 1788 → 2503) із брифу у ПУБЛІЧНОМУ фіді не знайдено (2026-10-04) — третього контролю нема;
# доки його не знайдено, константа перевіряється двома позиціями Барт.


def fetch_zoobaza_catalog(url: str = None) -> Dict[str, dict]:
    """Скачує фід і повертає {offer_id: запис}. Усі 910 позицій; фільтр — окремо (filter_catalog)."""
    r = requests.get(url or ZOOBAZA_YML_URL, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return parse_zoobaza_xml(r.content)


def parse_zoobaza_xml(content: bytes) -> Dict[str, dict]:
    root = ET.fromstring(content)
    shop = root.find("shop")
    cats_el = shop.find("categories") if shop is not None else None
    offers_el = shop.find("offers") if shop is not None else None
    if cats_el is None or offers_el is None:
        raise ValueError("фід ZooBaza має неочікувану структуру (нема shop/categories/offers) — "
                         "ймовірно, сторінка помилки замість YML; каталог НЕ оновлено")
    cat_names = {(c.get("id") or "").strip(): (c.text or "").strip() for c in cats_el}
    catalog: Dict[str, dict] = {}
    bad_price = dup = 0
    for o in offers_el.findall("offer"):
        pid = (o.get("id") or "").strip()
        if not pid:
            continue
        try:
            price = float((o.findtext("price") or "").strip())
        except ValueError:
            bad_price += 1
            continue                                   # без ціни товар непридатний
        if not math.isfinite(price) or price <= 0:
            bad_price += 1
            continue
        if pid in catalog:
            dup += 1                                   # лишаємо ПЕРШУ позицію, не мовчки перезаписуємо
            continue
        cat = (o.findtext("categoryId") or "").strip()
        catalog[pid] = {
            "id": pid,
            "supplier": "ZooBaza",
            "vendor_code": (o.findtext("vendorCode") or "").strip(),
            "name": (o.findtext("name") or "").strip(),
            "description": (o.findtext("description") or "").strip(),
            "price": price,                                           # ціна у фіді (= ОПТ × 1.4)
            "cost": round(price / ZOOBAZA_FEED_TO_OPT, 2),            # орієнтовна B2B-собівартість
            "available": (o.get("available") or "").strip().lower() == "true",
            "stock_flag": (o.findtext("quantity_in_stock") or "").strip(),   # прапорець, НЕ залишок
            "vendor": (o.findtext("vendor") or "").strip(),
            "pictures": [p.text.strip() for p in o.findall("picture") if p.text and p.text.strip()],
            "category_id": cat,
            "category_name": cat_names.get(cat, ""),
            "color": next((p.text.strip() for p in o.findall("param")
                           if (p.get("name") or "") == "Цвет" and p.text), ""),
        }
    if not catalog:
        raise ValueError("фід ZooBaza розібрано, але валідних offers 0 — каталог НЕ оновлено")
    print(f"[ZooBaza] Завантажено товарів: {len(catalog)}"
          + (f"; пропущено без валідної ціни: {bad_price}" if bad_price else "")
          + (f"; дублікатів id: {dup}" if dup else ""))
    return catalog


def filter_catalog(catalog: Dict[str, dict], include_clothing: bool = None) -> Dict[str, dict]:
    """Лише доступні з БІЛОГО списку категорій (ALLOWED + EXTRA); одяг — за прапорцем (за замовчуванням
    ВИМКНЕНО до рішення Консультанта); корми — ніколи."""
    inc = INCLUDE_CLOTHING if include_clothing is None else include_clothing
    allowed = (ALLOWED_CATEGORIES | EXTRA_CATEGORIES | ({CLOTHING_CATEGORY} if inc else set())) - FORBIDDEN_CATEGORIES
    return {pid: it for pid, it in catalog.items() if it["available"] and it["category_id"] in allowed}


def verify_cost_constant(catalog: Dict[str, dict], tolerance: float = 1.0) -> List[dict]:
    """Перевірка ZOOBAZA_FEED_TO_OPT на контрольних SKU: |price/константа − ОПТ| ≤ tolerance (ціни цілі).
    Повертає [{name, found, expected_opt, got_cost, ok}] — викликач вирішує, що робити (алерт тощо)."""
    res = []
    for needle, opt in CONTROL_SKUS:
        hit = next((it for it in catalog.values() if needle in it["name"]), None)
        if not hit:
            res.append({"name": needle, "found": False, "expected_opt": opt, "got_cost": None, "ok": None})
            continue
        res.append({"name": hit["name"], "found": True, "expected_opt": opt, "got_cost": hit["cost"],
                    "ok": abs(hit["cost"] - opt) <= tolerance})
    return res


def assert_cost_constant(catalog: Dict[str, dict], min_checked: int = 2) -> None:
    """Для автоматики: кидає, якщо перевірено < min_checked контролів (інакше `ok=None` дав би тихе «все гаразд»)
    або хоч один не збігся. Контрольні SKU зникли з фіду → гучна помилка, а не мовчання."""
    res = verify_cost_constant(catalog)
    checked = [r for r in res if r["ok"] is not None]
    if len(checked) < min_checked:
        raise AssertionError(f"контроль ZOOBAZA_FEED_TO_OPT: знайдено лише {len(checked)} із {len(res)} контрольних SKU")
    bad = [r for r in checked if not r["ok"]]
    if bad:
        raise AssertionError(f"ZOOBAZA_FEED_TO_OPT={ZOOBAZA_FEED_TO_OPT} не збігається з ОПТ: {bad}")


if __name__ == "__main__":
    cat = fetch_zoobaza_catalog()
    flt = filter_catalog(cat)
    print(f"[ZooBaza] після фільтра (білий список {sorted(ALLOWED_CATEGORIES | EXTRA_CATEGORIES)}, "
          f"одяг={'так' if INCLUDE_CLOTHING else 'ні'}): {len(flt)}")
    for r in verify_cost_constant(cat):
        print(f"[ZooBaza] контроль: {r}")
