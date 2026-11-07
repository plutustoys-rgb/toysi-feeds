# -*- coding: utf-8 -*-
"""dropt_eva.py — товари Dropt у EVA-фіді (рішення власника 04.11.2026: «розміщення на сайті та у фіді EVA»; закупівля — «Дроп», формула ціни як в RT).

ЩО ЦЕ. Той самий принцип, що rt_eva.py, але без Toysi-дублів і без вагового відбору: у Dropt-каталозі нема товарів Toysi/RT, тож відбір — це ПРАВИЛА «назва товару → лист EVA» (RULES). Категорії самого Dropt для EVA непридатні
(виміряно 07.11 на 787 позиціях: у «Товари для ПК» лежать Bluetooth-колонки, в «Інтерактивні іграшки» — волейбольний м'яч), тому класифікуємо за НАЗВОЮ, а категорію Dropt використовуємо лише як виключення.
Лист EVA — ЛИШЕ валідний (eva_category_reference.csv ∪ eva_category_map.json). Нема правила → товар у фід НЕ йде. У довіднику EVA нема листів для навушників, колонок, мікрофонів, екшн-камер, радіоприймачів, роутерів,
TV-приставок — ці товари лишаються тільки на сайті.

РОЗМІРИ. EVA вимагає розміри товару (в назві/описі/параметрах). Dropt габаритів упаковки публічно не дає, тому беремо розмір ТОВАРУ з назви/опису (напр. «240х150 см», «Ø220 см», «50×40×125 см»); нема — товар не емітуємо.

БРАМА. EVA_DROPT_IN_FEED=1 (.env) І EVA_ORDERS_SUPPORTED=True у цьому модулі. Другого прапорця свідомо немає в .env: поки конвеєр замовлень Dropt (dropt_order_runner.py) бере лише platform='site',
EVA-замовлення з dropt- ніхто б не виконав, а покупець EVA заплатив би. Прапорець перемикає PR, який навчить runner EVA-замовлень (precheck скасування, 11→12 з ТТН), — НЕ раніше.
Збій модуля ніколи не ламає фід: _maybe_add_dropt_offers у generate_eva_feed ловить усе й повертає дерево без Dropt."""
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

import rt_eva

HERE = Path(__file__).resolve().parent
LISTED_FILE = Path(os.environ.get("DROPT_EVA_LISTED_FILE", str(HERE / "dropt_eva_listed_state.json")))
EVA_ORDERS_SUPPORTED = False          # див. докстрінг: перемикає лише PR із EVA-замовленнями в dropt_order_runner
SUPPLIER_MAX_AGE_H = 3.0              # як ITEM_MAX_AGE_S експортера: файл старший — Dropt у цей фід не додаємо (EVA лишає попередній стан)
DROPT_MIN_STOCK = 2                   # Dropt дає лише ознаку «є» (3): поріг як у RT
MIN_MARKUP = 1.3                      # санітарна перевірка: price >= cost × 1.3 (supplier_dropt: price = site_price(cost))
ID_RE = re.compile(r"dropt-[0-9A-Za-z_-]{1,40}")


def gate_on() -> bool:
    return os.environ.get("EVA_DROPT_IN_FEED") == "1" and EVA_ORDERS_SUPPORTED


# ───────────────────────────── правила: назва → лист EVA ─────────────────────────────
# Порядок = пріоритет (перше влучання). Шаблони — по назві в нижньому регістрі. Лише ТОЧНІ відповідності листу; сумнів → правила нема.
# (id листа EVA — з eva_category_reference.csv; назва — для <category> у фіді)
RULES = [
    # --- виключення: що схоже на товар листа, але ним не є ---
    (r"\bстіл-конструктор|ігровий стіл|дитячий стіл", None, None),
    (r"рюкзак для перенесення домашніх тварин|\bтачка\b", None, None),
    (r"новорічн\w* (?:нічник|декор|сніжний|свічка|ліхтар)|різдвян", None, None),
    (r"тренажер|еспандер|для плавання|підстилка", None, None),
    (r"телескопічн", None, None),
    # --- тварини: іграшки для тварин раніше за загальні «інтерактивні/плюшеві» ---
    (r"іграшк\w*.*для (?:котів|кішок|кота)|для (?:котів|кішок).*іграшк|ігровий трек для кішок", "104012", "Іграшки для котів"),
    (r"іграшк\w*.*для (?:собак|собаки)|для собак.*іграшк", "104026", "Іграшки для собак"),
    # --- дитячі іграшки та дитяча кімната ---
    (r"радіокерован|на радіокеруванні|квадрокоптер|\bдрон\b", "105752", "Радіокеровані моделі"),
    (r"скарбничк|сейф[- ]скарбнич|дитячий сейф", "121869", "Скарбнички"),
    (r"конструктор", "105338", "Конструктори"),
    (r"головоломк", "105918", "Головоломки"),
    (r"намет|тунел", "105920", "Намети, тунелі"),
    (r"дитячий ігровий будиночок|ігровий будиночок|будиночок для дітей", "116260", "Дитячі ігрові будиночки"),
    (r"автокрісл|бустер", "121815", "Дитячі автокрісла"),
    (r"ходунки", "116264", "Ходунки та стрибунки"),
    (r"електробібікар|бібікар|дитячий електромобіл|електромобіль", "116265", "Дитячі електро- та веломобілі"),
    (r"шезлонг", "121859", "Дитячі шезлонги-гойдалки та колисальні центри"),
    (r"манеж", "116256", "Манежі"),
    (r"стільчик для годування", "116257", "Стільчики для годування"),
    (r"дошка[- ]планшет|дошка для малювання|планшет для малювання|малюй світлом|графічний планшет для дітей", "121898", "Дошки для малювання"),
    (r"дитячий комп.?ютер|планшет для малюків", "121899", "Дитячі комп'ютери"),
    (r"дитячий фотоапарат|дитяча відеокамера", "110859", "Дитячі фотоапарати та відеокамери"),
    (r"мʼяка іграшка|м.яка іграшка|плюшев", "101231", "Мʼякі іграшки"),
    (r"нічник|проектор зоряного неба", "115652", "Дитячі нічники"),
    (r"інтерактивн\w+ (?:іграшк|плюш)", "121901", "Інтерактивні іграшки"),
    # --- шкільне / сумки ---
    (r"шкільний рюкзак|ранець|дитячий рюкзак", "106607", "Дитячі рюкзаки"),
    (r"спортивна (?:дорожня )?сумка", "104564", "Спортивні сумки та чохли"),
    # --- електроніка (лише там, де є лист EVA) ---
    (r"power ?bank|повербанк|павербанк", "119658", "Універсальні мобільні батареї"),
    (r"\bакумулятор\b|батарейк", "120712", "Акумулятори та батарейки"),
    (r"зарядний пристрій для ноутбук", None, None),
    (r"зарядний пристрій", "122716", "Зарядні пристрої"),
    (r"\bкабель\b", "122718", "Кабелі та адаптери"),
    (r"мережев\w+ подовжувач|подовжувач\w* .*розет|мережевий фільтр", "121358", "Мережеві фільтри, адаптери та подовжувачі"),
    (r"ігрова приставка|ігрова консоль|ретро міні-ігрова", "122677", "Ігрові консолі та дитячі приставки"),
    (r"окуляри віртуальної реальності", "121605", "3D і VR окуляри"),
    (r"тримач (?:для )?(?:телефон|смартфон)|магнітний тримач", "117906", "Тримачі"),
    (r"світлодіодна стрічка", "114011", "Світлодіодна стрічка"),
    (r"метеостанц", "122665", "Термометри та гігрометри"),
    # --- Новий рік ---
    (r"гірлянд", "112356", "Світлодіодні гірлянди"),
    (r"штучна ялинка|штучна ялина", "112304", "Штучні ялинки"),
    (r"новорічна береза|світлодіодна береза", "112258", "Декоративні гілки"),
    # --- туризм і відпочинок ---
    (r"спальний мішок", "121260", "Спальні мішки"),
    (r"туристичний килимок", "121261", "Туристичні килимки"),
    (r"надувний диван|надувне крісло|надувн\w+ ліжко|надувний матрац", "121883", "Надувні меблі й аксесуари"),
    (r"складн\w+ (?:туристичний )?(?:стіл|стілець|крісло)", "121267", "Складані меблі"),
    (r"набір для пікніка", "122279", "Набори для пікніка"),
    (r"(?<!для )\bбасейн\b", "118503", "Басейни"),
    # --- тварини ---
    (r"кігтеточк|когтеточк", "106057", "Кігтеточки"),
    (r"лежанка для собак", "106954", "Лежанки для собак"),
    (r"лежанка для котів|лежанка для кішок", "106955", "Лежанки для котів"),
    (r"лоток для собак|туалет для собак", "109752", "Туалет для собак"),
    (r"шлейка для собак", "108205", "Шлейки для собак"),
    (r"нашийник для собак", "106052", "Нашийники для собак"),
    (r"повідець для собак|повідок для собак", "108202", "Повідець для собак"),
    (r"пелюшк\w* для собак", "104208", "Пелюшки для собак"),
    (r"переноска для собак", "106109", "Переноски для собак"),
    (r"переноска для (?:котів|кішок)", "115403", "Переноски для котів"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), i, n) for p, i, n in RULES]


def classify(name: str):
    """→ (eva_id, eva_name) або None. Перше влучання; None-правило = виключення (товар у фід не йде)."""
    low = (name or "").lower().replace("’", "ʼ")
    for rx, eid, ename in _COMPILED:
        if rx.search(low):
            return (eid, ename) if eid else None
    return None


# ───────────────────────────── розмір товару з тексту ─────────────────────────────
_UNIT = r"(?:(?:см|мм|м|cm|mm)\b)"
_NUM = r"\d{1,4}(?:[.,]\d{1,2})?"
_SIZE_RES = [
    re.compile(rf"{_NUM}\s*[xх×*]\s*{_NUM}(?:\s*[xх×*]\s*{_NUM})?\s*{_UNIT}?", re.IGNORECASE),     # 240х150 см, 50×40×125, 35*20*20 см
    re.compile(rf"[øØ⌀]\s*{_NUM}\s*{_UNIT}?", re.IGNORECASE),                                          # Ø220 см
    re.compile(rf"(?:діаметр|довжин\w*|висот\w*|ширин\w*)\s*[:\-]?\s*{_NUM}\s*{_UNIT}", re.IGNORECASE),  # довжина 3 м
    re.compile(rf"(?<![\w.,]){_NUM}\s*{_UNIT}", re.IGNORECASE),                                         # 33 см, 50 м
]


def extract_size(*texts):
    """Перший розмір ТОВАРУ з назви/опису → рядок для параметра «Розміри», або None. Ігнорує «4K», «18650», проценти, ємності (мАг/мл/Вт)."""
    for t in texts:
        t = re.sub(r"<[^>]+>", " ", t or "")
        for rx in _SIZE_RES:
            m = rx.search(t)
            if m:
                s = re.sub(r"\s+", " ", m.group(0)).strip(" ,.;")
                if re.search(r"\d", s) and len(s) <= 40:
                    return s
    return None


# ───────────────────────────── постачальник ─────────────────────────────
def supplier_path() -> Path:
    from dropt_supplier_export import OUT_FILE
    return Path(OUT_FILE)


def _read_supplier():
    try:
        return json.loads(supplier_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def supplier_fresh(max_h: float = SUPPLIER_MAX_AGE_H, now=None) -> bool:
    d = _read_supplier()
    try:
        ts = datetime.fromisoformat(str((d or {}).get("updated")).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = ((now or datetime.now(timezone.utc)) - ts)
        return timedelta(minutes=-5) <= age <= timedelta(hours=max_h)
    except (TypeError, ValueError, AttributeError):
        return False


def load_supplier() -> dict:
    d = _read_supplier()
    items = (d or {}).get("items")
    return items if isinstance(items, dict) and items else {}


# ───────────────────────────── емісія ─────────────────────────────
def build_offer_fields(sid: str, r: dict, eva: tuple, ge, forbidden_fn):
    """→ (dict полів offer, '') або (None, причина). Усі перевірки обов'язкових полів EVA — зіпсований offer не емітимо."""
    if not ID_RE.fullmatch(sid):
        return None, "id"
    vendor = (r.get("vendor") or "").strip()
    if not vendor:
        return None, "no_vendor"
    if ge._is_stop_brand(vendor):
        return None, "stop_brand"
    raw_name = r.get("name") or ""
    if ge._name_has_forbidden_word(raw_name):
        return None, "forbidden_word"
    if forbidden_fn({"name": raw_name, "vendor": vendor, "category_name": r.get("category") or ""}):
        return None, "forbidden_policy"
    stock, cost, price = rt_eva._num(r.get("stock")), rt_eva._num(r.get("cost")), rt_eva._num(r.get("price"))
    if stock is None or stock < DROPT_MIN_STOCK:
        return None, "stock"
    if not cost or cost <= 0 or cost < ge.MIN_SUPPLIER_PRICE or not price or price < cost * MIN_MARKUP:
        return None, "price"
    pics = rt_eva._pictures(r, ge.EVA_MAX_PICTURES)
    if not pics:
        return None, "no_pictures"
    name = ge._normalize_trailing_color_case(ge._limit_punctuation(ge._denoise_caps(ge._clean_text(raw_name))))
    if len(name) < 3:
        return None, "name"
    desc = ge._truncate(ge._clean_text(ge._sanitize_eva_description(ge._strip_urls(r.get("description") or ""))), ge.EVA_DESCRIPTION_MAX_LEN)
    if len(desc) < ge.EVA_DESCRIPTION_MIN_LEN:
        return None, "short_desc"
    size = extract_size(raw_name, r.get("description") or "")
    if not size:
        return None, "no_size"                         # EVA: розміри товару обов'язкові; в назві/описі нема — не емітуємо
    params = [("Розміри (см)" if re.search(r"\bсм\b|cm", size, re.I) else "Розміри", size), ("Виробник", vendor)]
    return {"id": sid, "name": name, "price": round(price), "stock": int(stock), "category": eva[0], "pictures": pics, "vendor": ge._clean_text(ge.normalize_vendor(vendor)),
            "description": desc, "params": params}, ""


def validate_offer(f: dict, valid_ids: set, name_max: int) -> str:
    if not ID_RE.fullmatch(f["id"]):
        return "id"
    if not f["name"] or len(f["name"]) > name_max:
        return "name"
    if f["price"] <= 0 or f["stock"] <= 0:
        return "price/stock"
    if f["category"] not in valid_ids:
        return "category"
    if not f["pictures"] or not all(p.startswith("https://") and len(p) <= 1999 for p in f["pictures"]):
        return "pictures"
    if not f["vendor"] or len(f["description"]) < 30 or len(f["params"]) < 2:
        return "fields"
    return ""


def _load_listed() -> dict:
    try:
        d = json.loads(LISTED_FILE.read_text(encoding="utf-8"))
        v = d.get("listed")
        return {str(k): (x if isinstance(x, dict) else {}) for k, x in v.items()} if isinstance(v, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def append_dropt_offers(root: ET.Element, ge, sup_items: dict) -> dict:
    """Дописує у <shop>: категорії (якщо ще нема) і offer'и Dropt. Повертає статистику. root — результат ge._build_xml (до indent/CDATA).
    Стабільність: уже листований товар тримає ТУ САМУ категорію (EVA: повторна модерація при зміні) і лишається у фіді, поки проходить фільтри; зник — заглушка available=false."""
    from forbidden_products import is_forbidden
    shop = root.find("shop")
    cats_el, offers_el = shop.find("categories"), shop.find("offers")
    have_cats = {c.get("id") for c in cats_el}
    valid = rt_eva.valid_eva_ids() | have_cats
    seen = {ge._dedup_key(o.findtext("name_ua") or "") for o in offers_el}
    stats = Counter()
    prev = _load_listed()
    active = {}
    for sid, r in sorted(sup_items.items()):
        eva = classify(r.get("name") or "")
        if not eva:
            stats["skip_no_rule"] += 1
            continue
        sticky = (prev.get(sid) or {}).get("cat")
        if sticky and sticky in valid:
            eva = (sticky, (prev[sid].get("catname") or eva[1]))
        f, why = build_offer_fields(sid, r, eva, ge, is_forbidden)
        if f is None:
            stats["skip_" + why] += 1
            continue
        bad = validate_offer(f, valid, ge.EVA_NAME_MAX_LEN)
        if bad:
            stats["invalid_" + bad] += 1
            continue
        active[sid] = (f, eva)
    for sid, (f, eva) in sorted(active.items()):              # унікальні назви: збіг із іншим offer'ом → дописуємо артикул
        key = ge._dedup_key(f["name"])
        if key in seen:
            code = (sup_items[sid].get("vendor_code") or "").strip() or sid
            suffix = f" ({code})"
            f["name"] = ge._truncate(f["name"], ge.EVA_NAME_MAX_LEN - len(suffix)) + suffix
            key = ge._dedup_key(f["name"])
            stats["renamed_dup"] += 1
        seen.add(key)
    new_prev = dict(prev)
    for sid, (f, eva) in sorted(active.items()):
        if f["category"] not in have_cats:
            ET.SubElement(cats_el, "category", id=f["category"]).text = ge._clean_text(eva[1])
            have_cats.add(f["category"])
        o = ET.SubElement(offers_el, "offer", id=sid, available="true")
        ET.SubElement(o, "name_ua").text = f["name"]
        ET.SubElement(o, "price").text = f"{f['price']:.2f}"
        ET.SubElement(o, "currencyId").text = "UAH"
        ET.SubElement(o, "stock_quantity").text = str(f["stock"])
        ET.SubElement(o, "categoryId").text = f["category"]
        for p in f["pictures"]:
            ET.SubElement(o, "picture").text = p
        ET.SubElement(o, "vendor").text = f["vendor"]
        ET.SubElement(o, "description_ua").text = f["description"]
        for pn, pv in f["params"]:
            ET.SubElement(o, "param", name=ge._clean_text(pn)).text = ge._clean_text(str(pv))
        new_prev[sid] = {"cat": f["category"], "catname": eva[1], "name": f["name"], "price": f["price"], "vendor": f["vendor"], "pics": f["pictures"][:3], "desc": f["description"][:500]}
        stats["emitted"] += 1
    for sid in sorted(set(new_prev) - set(active)):           # колись листовані, зараз не активні → заглушка available=false
        snap = new_prev.get(sid) or {}
        r = sup_items.get(sid) or {}
        price = rt_eva._num(r.get("price")) or rt_eva._num(snap.get("price"))
        name = r.get("name") or snap.get("name") or ""
        cat = snap.get("cat")
        if not price or price <= 0 or not name or not cat or cat not in valid:
            continue
        o = ET.SubElement(offers_el, "offer", id=sid, available="false")
        ET.SubElement(o, "name_ua").text = ge._truncate(ge._normalize_trailing_color_case(ge._limit_punctuation(ge._denoise_caps(ge._clean_text(name)))), ge.EVA_NAME_MAX_LEN) or f"Товар {sid}"
        ET.SubElement(o, "price").text = f"{round(price):.2f}"
        ET.SubElement(o, "currencyId").text = "UAH"
        ET.SubElement(o, "stock_quantity").text = "0"
        ET.SubElement(o, "categoryId").text = cat
        if cat not in have_cats:
            ET.SubElement(cats_el, "category", id=cat).text = ge._clean_text(snap.get("catname") or cat)
            have_cats.add(cat)
        for p in (rt_eva._pictures(r, ge.EVA_MAX_PICTURES) if r else []) or snap.get("pics") or []:
            ET.SubElement(o, "picture").text = p
        vend = (r.get("vendor") or snap.get("vendor") or "").strip()
        if vend:
            ET.SubElement(o, "vendor").text = ge._clean_text(ge.normalize_vendor(vend)) if r else vend
        d = snap.get("desc") or ""
        if len(d) >= ge.EVA_DESCRIPTION_MIN_LEN:
            ET.SubElement(o, "description_ua").text = d
        stats["deactivated"] += 1
    try:
        rt_eva._atomic_write(LISTED_FILE, {"v": 1, "listed": new_prev, "saved_at": datetime.now().isoformat(timespec="seconds")})
    except OSError as e:
        print(f"[dropt_eva] не вдалось зберегти {LISTED_FILE.name}: {e}", file=sys.stderr)
    return dict(stats)


def coverage_report(sup_items: dict) -> dict:
    """Скільки позицій файла має правило (без перевірок опису/розміру/фото) — для огляду перед відкриттям брами."""
    c, by = Counter(), Counter()
    for r in sup_items.values():
        e = classify(r.get("name") or "")
        c["rule" if e else "no_rule"] += 1
        if e:
            by[e[1]] += 1
    return {"n": len(sup_items), **c, "by_leaf": dict(by.most_common())}


if __name__ == "__main__":
    items = load_supplier()
    print(json.dumps(coverage_report(items), ensure_ascii=False, indent=1))
