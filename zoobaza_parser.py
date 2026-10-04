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

КАТЕГОРІЇ: корми (1083/1086/1087) НІКОЛИ не беремо. «Одяг для тварин» (105501, 71% каталогу) — рішення за
Консультантом: керується прапорцем ZOOBAZA_INCLUDE_CLOTHING (за замовчуванням ВИМКНЕНО).
"""
import os
import sys
from typing import Dict, List

import requests
import xml.etree.ElementTree as ET

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ZOOBAZA_YML_URL = os.environ.get("ZOOBAZA_YML_URL", "https://basmati.com.ua/zoobaza_full.php")
REQUEST_TIMEOUT = 90
ZOOBAZA_FEED_TO_OPT = float(os.environ.get("ZOOBAZA_FEED_TO_OPT", "1.4"))   # price_фіду / ОПТ

FORBIDDEN_CATEGORIES = {"1083", "1086", "1087"}          # корми — не беремо
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
    cat_names = {(c.get("id") or "").strip(): (c.text or "").strip() for c in shop.find("categories")}
    catalog: Dict[str, dict] = {}
    for o in shop.find("offers").findall("offer"):
        pid = (o.get("id") or "").strip()
        if not pid:
            continue
        try:
            price = float((o.findtext("price") or "").strip())
        except ValueError:
            continue                                   # без ціни товар непридатний
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
    print(f"[ZooBaza] Завантажено товарів: {len(catalog)}")
    return catalog


def filter_catalog(catalog: Dict[str, dict], include_clothing: bool = None) -> Dict[str, dict]:
    """Лише доступні, не корми; одяг — за прапорцем (за замовчуванням ВИМКНЕНО до рішення Консультанта)."""
    inc = INCLUDE_CLOTHING if include_clothing is None else include_clothing
    out = {}
    for pid, it in catalog.items():
        if not it["available"] or it["category_id"] in FORBIDDEN_CATEGORIES:
            continue
        if it["category_id"] == CLOTHING_CATEGORY and not inc:
            continue
        out[pid] = it
    return out


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


if __name__ == "__main__":
    cat = fetch_zoobaza_catalog()
    flt = filter_catalog(cat)
    print(f"[ZooBaza] після фільтра (доступні, без кормів, одяг={'так' if INCLUDE_CLOTHING else 'ні'}): {len(flt)}")
    for r in verify_cost_constant(cat):
        print(f"[ZooBaza] контроль: {r}")
