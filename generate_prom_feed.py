import html
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from parser import fetch_toysi_catalog

SHOP_NAME          = "PlutusToys"
SHOP_COMPANY       = "ФОП Чечетенко Олександр Юрійович"
SHOP_URL           = "https://prom.ua"
OUTPUT_FILE        = "feeds/prom_feed.xml"
MIN_SUPPLIER_PRICE = 20  # товари дешевше цієї ціни постачальника пропускаємо

# Товари категорії "Уцінка"/"Уценка" (обидва написання зустрічаються в каталозі
# Toysi) — власник свідомо лишає їх у головних фідах (не виключає), але кожен
# такий товар має отримати попередження про можливий дефект у описі. Перевірка
# на реальних даних (2026-07-06): конкретний дефект від Toysi присутній у
# назві/описі приблизно в 76-90%+ позицій (формат вкрай непослідовний — після
# тире, у дужках, простим реченням, іноді лише в описі, а не в назві), тож
# автоматично й надійно розрізнити "дефект є" від "дефекту немає" неможливо
# без ризику пропустити реальний дефект через невдалий патерн. Тому дописуємо
# застереження ЗАВЖДИ, для кожного товару категорії "Уцінка" — воно доповнює,
# а не замінює власний текст Toysi.
#
# Перевірка на реальних даних (2026-07-06, PR review flagged startswith-only
# as a possible false-negative risk): 0 товарів мають маркер десь у середині
# назви чи лише в описі без нього ж на початку назви — але 94 товари мають
# написання, яке не ловить startswith() узагалі ("Уціка" — без "н", "Уцiнка" —
# з латинською "i" замість кириличної "і"), хоча Toysi сам відносить їх до
# категорії "Уцінка" (categoryId=51995). Тому перевірка тепер додатково
# звіряється з ID категорії від постачальника (стабільний числовий ідентифікатор,
# на відміну від тексту назви — той сам може містити таку саму помилку друку,
# яку ми якраз і виправляємо) — це надійне джерело істини незалежно від
# помилок друку в назві товару. Назву категорії лишаємо як запасний варіант
# (якщо Toysi колись перевикористає цей ID під іншою категорією).
CLEARANCE_PREFIXES = ("уцінка", "уценка")
CLEARANCE_CATEGORY_IDS = {"51995"}
CLEARANCE_CATEGORY_NAMES = {"уцінка", "уценка"}
CLEARANCE_NOTICE = (
    "<b>⚠️ Товар категорії «Уцінка».</b> Постачальник не завжди "
    "деталізує конкретний дефект для кожної позиції — можливі: пошкодження "
    "чи потертості упаковки, косметичні дефекти виробу, відсутність дрібних "
    "елементів комплектації. Перед замовленням рекомендуємо уточнити стан "
    "товару в чаті."
)


def append_clearance_notice(
    description: str, name: str, category_name: str = "", category_id: str = ""
) -> str:
    """Дописує CLEARANCE_NOTICE до опису товару категорії "Уцінка"/"Уценка",
    без зайвого відступу на початку, якщо базовий опис порожній."""
    if not is_clearance_item(name, category_name, category_id):
        return description
    separator = "<br/><br/>" if description else ""
    return description + separator + CLEARANCE_NOTICE


def is_clearance_item(name: str, category_name: str = "", category_id: str = "") -> bool:
    if (name or "").strip().lower().startswith(CLEARANCE_PREFIXES):
        return True
    if (category_id or "").strip() in CLEARANCE_CATEGORY_IDS:
        return True
    return (category_name or "").strip().lower() in CLEARANCE_CATEGORY_NAMES


# ---------------------------------------------------------------------------
# SEO-пошукові запити (<keywords>/<keywords_ua>) — автогенерація з наявних
# даних фіда (назва, категорія, бренд), без ручного введення на кожен SKU.
# За документацією Prom (support.prom.ua/hc/uk/articles/360004963538):
# розділювач — кома, ліміт 1024 символи в рядку; keywords_ua застосується
# ЛИШЕ якщо в тому самому <offer> одночасно заповнені name_ua і
# description_ua (див. _build_xml).
# ---------------------------------------------------------------------------
KEYWORDS_MAX_LEN     = 1024
KEYWORDS_TARGET_COUNT = 10  # ціль 8-10 унікальних запитів на мову

# За проханням Prom-менеджера — без "замовити"/назв регіону.
_GENERIC_MODIFIERS_UA = ["дитячий", "подарунок дитині", "купити"]
_GENERIC_MODIFIERS_RU = ["детский", "подарок ребенку", "купить"]

_KEYWORD_STOPWORDS_UA = {
    "з", "та", "і", "в", "на", "від", "до", "по", "для", "як", "що", "це", "із",
}

# Невеликий словник найпоширеніших товарних слів з каталогу Toysi — не повний
# машинний переклад (для цього немає надійного джерела), а точковий переклад
# типових термінів. Слова поза словником лишаються як є: чимало іграшкових
# термінів — спільні корені чи однакові слова в обох мовах ("конструктор",
# "кубик", "слайм", "антистрес", бренди на кшталт "Corso"/"MIC" тощо).
_UA_RU_DICT = {
    "дитячий": "детский", "дитяча": "детская", "дитяче": "детское", "дитячі": "детские",
    "дитині": "ребенку", "дитина": "ребенок", "дітей": "детей",
    "хлопчику": "мальчику", "хлопчика": "мальчика",
    "дівчинці": "девочке", "дівчинки": "девочки",
    "купити": "купить", "подарунок": "подарок",
    "іграшка": "игрушка", "іграшки": "игрушки", "іграшок": "игрушек",
    "набір": "набор", "набори": "наборы",
    "лялька": "кукла", "ляльки": "куклы",
    "гра": "игра", "ігри": "игры", "ігор": "игр",
    "настільна": "настольная", "настільні": "настольные",
    "посуд": "посуда", "кухня": "кухня", "кухні": "кухни",
    "пазл": "пазл", "пазли": "пазлы", "пазлів": "пазлов",
    "поїзд": "поезд", "вагон": "вагон", "вагоном": "вагоном",
    "пластиковий": "пластиковый", "пластикова": "пластиковая", "пластикові": "пластиковые",
    "бокс": "бокс", "боксу": "бокса",
    # Ключі без апострофа — _tokenize_name прибирає його з токенів (див. нижче)
    "мякий": "мягкий", "мяка": "мягкая", "мяке": "мягкое", "мякі": "мягкие",
    "деревяний": "деревянный", "деревяна": "деревянная", "деревяне": "деревянное",
    "деревяні": "деревянные",
    "надувний": "надувной", "надувне": "надувное", "надувна": "надувная",
    "коло": "круг",
    "фігурка": "фигурка", "фігурки": "фигурки",
    "малий": "маленький", "мала": "маленькая", "мале": "маленькое",
    "великий": "большой", "велика": "большая",
    "конструктор": "конструктор", "конструктори": "конструкторы",
}


def _tokenize_name(name: str) -> list:
    """Розбиває назву товару на змістовні слова (нижній регістр): без
    розділових знаків, без коротких/стоп-слів, без дублів.

    Апостроф (', ’ чи, зрідка, " всередині слова — напр. 'М"яка', де Toysi
    використав пряму лапку замість апострофа) у назвах — не роздільник слів,
    а орфографічний знак усередині слова ("дерев'яний", "сім'я", "м'який" —
    усі дуже поширені в каталозі Toysi). Якщо трактувати його як пробіл,
    слова розпадаються на сміттєві фрагменти ("сім'я" -> "сім"+"я" -> "сім"
    лишається самостійним словом, хоча насправді це лише частина "сім'я";
    "м'який" -> "м"+"який", де "м" відкидається як коротке, а "який"
    лишається безглуздим самостійним "словом"). Тому апостроф просто
    прибираємо (без пробілу): "м'який" -> "мякий" — суцільне слово, до речі
    ближче до того, як реальні користувачі вводять пошукові запити (без
    апострофів). Пряму лапку в цій ролі відрізняємо від лапок навколо назви
    товару за відсутністю пробілу з обох боків (стоїть між двома літерами,
    а не оточена пробілами, як звичайні лапки навколо назви)."""
    cleaned = name.lower().replace("'", "").replace("’", "")
    cleaned = re.sub(r'(?<=\w)"(?=\w)', "", cleaned)
    cleaned = re.sub(r"[«»\"()\[\],.:;\-–—/]", " ", cleaned)
    words = []
    seen = set()
    for w in cleaned.split():
        # Короткі слова відкидаємо завжди; для чисел лишаємо лише від 2 цифр —
        # окремі "1"/"2" (частина артикулу) марні як пошуковий запит, а
        # "80"/"64" (кількість елементів) — цілком реальний пошуковий термін.
        if len(w) <= 2 and not (w.isdigit() and len(w) >= 2):
            continue
        if w in _KEYWORD_STOPWORDS_UA:
            continue
        if w in seen:
            continue
        seen.add(w)
        words.append(w)
    return words


def _translate_word_ua_ru(word: str) -> str:
    return _UA_RU_DICT.get(word, word)


def _dedupe_preserve_order(phrases: list) -> list:
    """Прибирає дублі (без урахування регістру) і коми всередині фрази —
    кома в Prom є роздільником запитів (напр. деякі назви категорій Toysi
    самі містять кому: "Лизуни, слайми та жуйки для рук"), тож лишати її
    в самій фразі неоднозначно."""
    seen = set()
    result = []
    for p in phrases:
        key = p.strip().lower().replace(",", "")
        key = re.sub(r"\s+", " ", key).strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


_LATIN_OR_DIGIT_RE = re.compile(r"^[a-z0-9]+$")


def _translate_phrase_if_complete(phrase: str) -> str:
    """Перекладає багатослівну фразу (напр. назву категорії) слово-в-слово,
    але повертає "" (пропустити фразу), якщо бодай одне змістовне слово не
    знайдено в словнику. Категорія одна на десятки товарів — некоректний
    переклад тут "розмножується" на всі товари цієї категорії, тож краще
    пропустити фразу для RU повністю (лишиться лише в UA, де вона рідна й
    коректна), ніж дати мішанину мов в один пошуковий запит."""
    words = []
    for w in phrase.split():
        wl = w.lower()
        if wl in _KEYWORD_STOPWORDS_UA:
            continue
        if _LATIN_OR_DIGIT_RE.match(wl):
            words.append(wl)
            continue
        translated = _UA_RU_DICT.get(wl)
        if translated is None:
            return ""
        words.append(translated)
    return " ".join(words)


def _join_within_limit(phrases: list, limit: int = KEYWORDS_MAX_LEN) -> str:
    kept = []
    total = 0
    for p in phrases:
        add_len = len(p) + (2 if kept else 0)  # ", "
        if total + add_len > limit:
            break
        kept.append(p)
        total += add_len
    return ", ".join(kept)


def generate_keywords(item: dict) -> tuple:
    """Генерує пошукові запити <keywords_ua>/<keywords> з наявних даних
    фіда: слова з назви товару, категорія, бренд (vendor), + загальні
    модифікатори за змістом категорії іграшок. Ціль — 8-10 унікальних
    запитів на мову, в межах ліміту 1024 символи."""
    name = item.get("name", "") or ""
    category_name = (item.get("category_name", "") or "").strip().lower()
    vendor = (item.get("vendor", "") or "").strip()

    name_words = _tokenize_name(name)

    phrases_ua = list(name_words[:5])
    if category_name:
        phrases_ua.append(category_name)
    if vendor:
        phrases_ua.append(vendor.lower())
        if name_words:
            phrases_ua.append(f"{name_words[0]} {vendor.lower()}")
    phrases_ua.extend(_GENERIC_MODIFIERS_UA)

    phrases_ua = _dedupe_preserve_order(phrases_ua)[:KEYWORDS_TARGET_COUNT]
    keywords_ua = _join_within_limit(phrases_ua)

    phrases_ru = [_translate_word_ua_ru(w) for w in name_words[:5]]
    if category_name:
        translated_cat = _translate_phrase_if_complete(category_name)
        if translated_cat:
            phrases_ru.append(translated_cat)
    if vendor:
        phrases_ru.append(vendor.lower())
        if name_words:
            phrases_ru.append(f"{_translate_word_ua_ru(name_words[0])} {vendor.lower()}")
    phrases_ru.extend(_GENERIC_MODIFIERS_RU)

    phrases_ru = _dedupe_preserve_order(phrases_ru)[:KEYWORDS_TARGET_COUNT]
    keywords_ru = _join_within_limit(phrases_ru)

    return keywords_ua, keywords_ru


def calc_price(cost: float) -> float:
    """Розраховує роздрібну ціну з наценкою залежно від собівартості.

    до 100 грн: +60% | 100-300: +50% | 300-700: +40% | 700-2000: +35% | 2000+: +25%
    """
    if cost < 100:    return round(cost * 1.60)
    elif cost < 300:  return round(cost * 1.50)
    elif cost < 700:  return round(cost * 1.40)
    elif cost < 2000: return round(cost * 1.35)
    else:             return round(cost * 1.25)


def _wrap_cdata(xml_str: str) -> str:
    """Post-process: wrap <description>/<description_ua> content in CDATA."""
    def make_replacer(tag):
        def replacer(m):
            content = html.unescape(m.group(1))
            content = content.replace("]]>", "]]]]><![CDATA[>")
            return f"<{tag}><![CDATA[{content}]]></{tag}>"
        return replacer
    for tag in ("description", "description_ua"):
        xml_str = re.sub(rf"<{tag}>(.*?)</{tag}>", make_replacer(tag), xml_str, flags=re.DOTALL)
    return xml_str


def _build_xml(catalog: dict, price_overrides: dict = None) -> tuple[ET.Element, dict]:
    now  = datetime.now().strftime("%Y-%m-%d %H:%M")
    yml  = ET.Element("yml_catalog", date=now)
    shop = ET.SubElement(yml, "shop")
    ET.SubElement(shop, "name").text    = SHOP_NAME
    ET.SubElement(shop, "company").text = SHOP_COMPANY
    ET.SubElement(shop, "url").text     = SHOP_URL

    currencies = ET.SubElement(shop, "currencies")
    ET.SubElement(currencies, "currency", id="UAH", rate="1")

    # Collect unique categories from catalog items
    cat_map: dict = {}
    for item in catalog.values():
        cid   = (item.get("category_id") or "").strip()
        cname = (item.get("category_name") or "").strip()
        if cid and cid not in cat_map:
            cat_map[cid] = cname or cid  # fallback: id as name if feed has no names

    categories_el = ET.SubElement(shop, "categories")
    for cid in sorted(cat_map):
        ET.SubElement(categories_el, "category", id=cid).text = cat_map[cid]

    offers_el     = ET.SubElement(shop, "offers")
    overrides     = price_overrides or {}
    skipped       = 0
    skipped_cheap = 0
    tier_counts   = {"<100": 0, "100-300": 0, "300-700": 0, "700-2000": 0, ">2000": 0}

    def tier_of(cost: float) -> str:
        if cost < 100:    return "<100"
        elif cost < 300:  return "100-300"
        elif cost < 700:  return "300-700"
        elif cost < 2000: return "700-2000"
        else:             return ">2000"

    for item in catalog.values():
        try:
            cost = float(item.get("price") or 0)
        except (ValueError, TypeError):
            skipped += 1
            continue
        if cost <= 0:
            skipped += 1
            continue
        if cost < MIN_SUPPLIER_PRICE:
            skipped_cheap += 1
            continue

        item_id   = str(item["id"])
        retail    = overrides.get(item_id, calc_price(cost))
        stock     = item.get("stock", 0)
        available = "true" if stock > 0 else "false"
        tier_counts[tier_of(cost)] += 1

        offer = ET.SubElement(offers_el, "offer",
                              id=item_id,
                              available=available)

        # Prom.ua: пріоритет коду товару vendorCode > barcode
        vendor_code = item.get("vendor_code") or item_id
        ET.SubElement(offer, "vendorCode").text        = vendor_code
        # Фід і так лише українською (parser.py тягне lang=ukr) — тож name_ua/
        # description_ua дублюють name/description. Це не просто формальність:
        # за документацією Prom (support.prom.ua/hc/uk/articles/360004963538),
        # <keywords_ua> підхоплюється на стороні Prom, ЛИШЕ якщо в тому самому
        # <offer> одночасно заповнені name_ua І description_ua — без цього
        # українські пошукові запити нижче просто не застосуються.
        name = item.get("name", "")
        ET.SubElement(offer, "name").text               = name
        ET.SubElement(offer, "name_ua").text             = name
        ET.SubElement(offer, "price").text               = f"{retail:.2f}"
        ET.SubElement(offer, "currencyId").text          = "UAH"
        # Prom.ua використовує quantity_in_stock (а не stock_quantity, як Rozetka)
        ET.SubElement(offer, "quantity_in_stock").text   = str(stock)

        if item.get("category_id"):
            ET.SubElement(offer, "categoryId").text = item["category_id"]

        for pic_url in item.get("pictures", [])[:10]:
            ET.SubElement(offer, "picture").text = pic_url

        if item.get("vendor"):
            ET.SubElement(offer, "vendor").text = item["vendor"]

        if item.get("country"):
            ET.SubElement(offer, "country").text = item["country"]

        if item.get("barcode"):
            ET.SubElement(offer, "barcode").text = item["barcode"]

        # Prom.ua вимагає наявність <description>, навіть якщо порожній
        description = append_clearance_notice(
            item.get("description", ""),
            item.get("name", ""),
            item.get("category_name", ""),
            item.get("category_id", ""),
        )
        ET.SubElement(offer, "description").text = description
        # description_ua дублює description (та сама причина, що й name_ua вище) —
        # обидва потрібні одночасно, щоб Prom підхопив keywords_ua.
        ET.SubElement(offer, "description_ua").text = description

        keywords_ua, keywords_ru = generate_keywords(item)
        if keywords_ua:
            ET.SubElement(offer, "keywords_ua").text = keywords_ua
        if keywords_ru:
            ET.SubElement(offer, "keywords").text = keywords_ru

        for param_name, param_val in item.get("params", []):
            ET.SubElement(offer, "param", name=param_name).text = str(param_val)

    stats = {
        "total_in_feed": len(offers_el),
        "skipped_no_price": skipped,
        "skipped_cheap": skipped_cheap,
        "tier_counts": tier_counts,
    }
    return yml, stats


def generate_feed(output_file: str = OUTPUT_FILE,
                  price_overrides: dict = None,
                  catalog: dict = None) -> None:
    if catalog is None:
        print("[Prom] Завантажуємо каталог Toysi...")
        catalog = fetch_toysi_catalog()
    if not catalog:
        print("[Prom] Каталог порожній — файл не створено.")
        return

    print(f"[Prom] Генеруємо XML для {len(catalog)} товарів...")
    root, stats = _build_xml(catalog, price_overrides=price_overrides)

    ET.indent(root, space="  ")
    xml_str = ET.tostring(root, encoding="unicode")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str
    xml_str = _wrap_cdata(xml_str)

    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(xml_str)

    tc = stats["tier_counts"]
    print(f"[Prom] Готово! Збережено: {output_file}")
    print(f"[Prom] У фіді: {stats['total_in_feed']} товарів | "
          f"пропущено (без ціни): {stats['skipped_no_price']} | "
          f"дешевше {MIN_SUPPLIER_PRICE} грн: {stats['skipped_cheap']}")
    print("[Prom] Розподіл за націнкою:")
    print(f"    до 100 грн       (+60%): {tc['<100']}")
    print(f"    100-300 грн      (+50%): {tc['100-300']}")
    print(f"    300-700 грн      (+40%): {tc['300-700']}")
    print(f"    700-2000 грн     (+35%): {tc['700-2000']}")
    print(f"    більше 2000 грн  (+25%): {tc['>2000']}")


if __name__ == "__main__":
    generate_feed()
