"""
Пілотний фід ~100 товарів RoyalToys — окремий скрипт (не змерджений у
generate_prom_feed.py/generate_rozetka_feed.py), Фаза 1 плану розширення
асортименту другим постачальником.

Обрано категорії/вендори, де RoyalToys РЕАЛЬНО доповнює асортимент Toysi,
а не дублює — на основі:
1. Точного SKU-рівня порівняння (royaltoys_comparison_2026-07-06_sku.md,
   compare_royaltoys_toysi.py) — вендори sbabam/coolthings/tikiwiki звідти
   ("унікальні збіги" — Toysi там присутній, але значно слабший за RoyalToys).
2. Аналізу ПОВНОГО каталогу Toysi (28 835 товарів, не лише топ-970) по
   кожному вендору RoyalToys — вибрано вендори з нульовим або майже нульовим
   покриттям Toysi (дерев'яні розвивайки, магнітні іграшки, конструктори
   типу Лего, дерев'яні головоломки, букети з м'яких іграшок, настільні ігри,
   тимчасові татуювання, декоративні подушки/плюш-персонажі).

Свідомо ВИКЛЮЧЕНО: вендори зі значним покриттям Toysi у повному каталозі
(bambi, dankotoys, gtoys, intex, vladitoys, tigres, funko, colorplast,
origami, easyfit, battat, bamsic — десятки/сотні позицій у Toysi вже) та
великогабаритні/дорогі категорії (bambiracer — електромобілі, медіана
8700 грн опт — не для першого пілотного пакету).

Фаза 2 (маршрутизація замовлень order_router.py/toysi_order_submit.py для
цих товарів) — окремо, після появи товарів у каталозі й підтвердження попиту.
"""
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime

from parser import fetch_toysi_catalog
from royaltoys_parser import fetch_royaltoys_catalog
from generate_prom_feed import calc_price, MIN_SUPPLIER_PRICE, SHOP_NAME, SHOP_COMPANY, SHOP_URL, _wrap_cdata
from compare_royaltoys_toysi import normalize_name, normalize_vendor, MATCH_THRESHOLD

OUTPUT_FILE = "feeds/royaltoys_pilot_feed.xml"

# vendor_norm -> цільова кількість товарів у пілотному пакеті (сума = 100)
PILOT_VENDOR_TARGETS = {
    "ubumblebees":   12,  # дерев'яні пазли/розвивайки — 0 у Toysi
    "dodotoys":      10,  # пазли, мильні бульбашки — 0 у Toysi
    "macik":          8,  # брязкальця/плюш для малюків — 0 у Toysi
    "magdum":         8,  # магнітні іграшки/ігри — 0 у Toysi
    "qman":           8,  # конструктори типу Лего — 0 у Toysi
    "igroteco":       6,  # дерев'яні конструктори — 0 у Toysi
    "заморочка":      6,  # дерев'яні головоломки — 0 у Toysi
    "igratoria":      6,  # букети з м'яких іграшок — 0 у Toysi
    "sbabam":         8,  # стретч/плюш — Toysi слабший (23 проти 56 у RT)
    "coolthings":     6,  # фігурки-сюрпризи — Toysi 26 проти 31 у RT
    "tikiwiki":       5,  # наклейки/стретч — Toysi слабкий (6 проти 20 у RT)
    "games7days":     6,  # настільні ігри — 0 у Toysi
    "freshtattoo":    6,  # тимчасові татуювання — 0 у Toysi
    "wpmerchandise":  5,  # декоративні подушки/плюш-персонажі — 0 у Toysi
}


def _is_duplicate_of_toysi(rt_item: dict, toysi_by_vendor: dict) -> bool:
    """Чи цей товар RoyalToys — по суті той самий товар, що вже є в Toysi
    (той самий бренд + дуже схожа назва). Дублікати пропускаємо — мета
    пілоту саме доповнення, а не повтор того, що вже продається."""
    vkey = normalize_vendor(rt_item.get("vendor", ""))
    candidates = toysi_by_vendor.get(vkey)
    if not candidates:
        return False
    rt_words = normalize_name(rt_item.get("name_ua") or rt_item.get("name", ""))
    for cand_words in candidates:
        if not rt_words or not cand_words:
            continue
        inter = len(rt_words & cand_words)
        union = len(rt_words | cand_words)
        if union and inter / union >= MATCH_THRESHOLD:
            return True
    return False


def _select_diverse(items: list, target: int) -> list:
    """Обирає `target` товарів, розподіляючи вибір рівномірно по підкатегоріях
    вендора (round-robin), щоб не взяти купу однакових варіацій одного товару."""
    by_cat = defaultdict(list)
    for item in items:
        by_cat[item.get("category_name", "")].append(item)
    for cat_items in by_cat.values():
        cat_items.sort(key=lambda i: float(i.get("price") or 0))

    buckets = list(by_cat.values())
    selected = []
    i = 0
    while len(selected) < target and any(buckets):
        bucket = buckets[i % len(buckets)]
        if bucket:
            selected.append(bucket.pop(0))
        i += 1
    return selected[:target]


def select_pilot_items(toysi_catalog: dict, royaltoys_catalog: dict) -> list:
    toysi_by_vendor = defaultdict(list)
    for item in toysi_catalog.values():
        vkey = normalize_vendor(item.get("vendor", ""))
        if vkey:
            toysi_by_vendor[vkey].append(normalize_name(item.get("name", "")))

    rt_by_vendor = defaultdict(list)
    for item in royaltoys_catalog.values():
        vkey = normalize_vendor(item.get("vendor", ""))
        if vkey in PILOT_VENDOR_TARGETS:
            rt_by_vendor[vkey].append(item)

    selected_items = []
    stats = {}
    for vkey, target in PILOT_VENDOR_TARGETS.items():
        candidates = [
            item for item in rt_by_vendor.get(vkey, [])
            if item.get("available") and item.get("stock", 0) > 0
            and _price_ok(item.get("price"))
            and not _is_duplicate_of_toysi(item, toysi_by_vendor)
        ]
        chosen = _select_diverse(candidates, target)
        stats[vkey] = (len(candidates), len(chosen))
        selected_items.extend(chosen)

    print("[RoyalToys pilot] Відбір за вендором (кандидатів придатних / обрано):", file=sys.stderr)
    for vkey, (n_cand, n_chosen) in stats.items():
        print(f"  {vkey}: {n_cand} / {n_chosen} (ціль {PILOT_VENDOR_TARGETS[vkey]})", file=sys.stderr)

    return selected_items


def _price_ok(price_raw) -> bool:
    try:
        return float(price_raw or 0) >= MIN_SUPPLIER_PRICE
    except (TypeError, ValueError):
        return False


def _build_xml(items: list) -> ET.Element:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    yml = ET.Element("yml_catalog", date=now)
    shop = ET.SubElement(yml, "shop")
    ET.SubElement(shop, "name").text = SHOP_NAME
    ET.SubElement(shop, "company").text = SHOP_COMPANY
    ET.SubElement(shop, "url").text = SHOP_URL

    currencies = ET.SubElement(shop, "currencies")
    ET.SubElement(currencies, "currency", id="UAH", rate="1")

    cat_map = {}
    for item in items:
        cid = (item.get("category_id") or "").strip()
        cname = (item.get("category_name") or "").strip()
        if cid and cid not in cat_map:
            cat_map[cid] = cname or cid

    categories_el = ET.SubElement(shop, "categories")
    for cid in sorted(cat_map):
        ET.SubElement(categories_el, "category", id=cid).text = cat_map[cid]

    offers_el = ET.SubElement(shop, "offers")
    for item in items:
        # Префікс "rt-" — щоб id/vendorCode не зіткнулися з Toysi при
        # майбутньому злитті в один фід (у Toysi суто числові id).
        item_id = f"rt-{item['id']}"
        try:
            cost = float(item.get("price") or 0)
        except (TypeError, ValueError):
            continue
        retail = calc_price(cost)
        stock = item.get("stock", 0)
        available = "true" if stock > 0 else "false"

        offer = ET.SubElement(offers_el, "offer", id=item_id, available=available)
        ET.SubElement(offer, "vendorCode").text = f"rt-{item.get('vendor_code') or item['id']}"
        ET.SubElement(offer, "name").text = item.get("name_ua") or item.get("name", "")
        ET.SubElement(offer, "price").text = f"{retail:.2f}"
        ET.SubElement(offer, "currencyId").text = "UAH"
        ET.SubElement(offer, "quantity_in_stock").text = str(stock)

        if item.get("category_id"):
            ET.SubElement(offer, "categoryId").text = item["category_id"]

        for pic_url in item.get("pictures", [])[:10]:
            ET.SubElement(offer, "picture").text = pic_url

        # RoyalToys деколи віддає бренд із зайвим "#" на початку (напр. "#sbabam") —
        # прибираємо для клієнтського фіда.
        ET.SubElement(offer, "vendor").text = (item.get("vendor", "") or "").lstrip("#").strip()
        ET.SubElement(offer, "supplier").text = item.get("supplier", "RoyalToys")

        description = item.get("description_ua") or item.get("description", "")
        ET.SubElement(offer, "description").text = description

    return yml


def generate_feed(output_file: str = OUTPUT_FILE) -> None:
    print("[RoyalToys pilot] Завантажуємо каталог Toysi (для дедуплікації)...")
    toysi_catalog = fetch_toysi_catalog()
    print("[RoyalToys pilot] Завантажуємо каталог RoyalToys...")
    royaltoys_catalog = fetch_royaltoys_catalog()

    if not toysi_catalog or not royaltoys_catalog:
        print("[RoyalToys pilot] Один з каталогів порожній — файл не створено.")
        return

    items = select_pilot_items(toysi_catalog, royaltoys_catalog)
    print(f"[RoyalToys pilot] Обрано товарів: {len(items)}")

    root = _build_xml(items)
    ET.indent(root, space="  ")
    xml_str = ET.tostring(root, encoding="unicode")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str
    xml_str = _wrap_cdata(xml_str)

    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(xml_str)

    print(f"[RoyalToys pilot] Готово! Збережено: {output_file}")


if __name__ == "__main__":
    generate_feed()
