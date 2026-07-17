import html
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from competitor_pricing import decide_price_for_platform, load_fresh_prom_price_overrides
from parser import fetch_toysi_catalog
from telegram_notify import send_telegram_message

# Надійність, п.5: truncated_name_count/truncated_name_ua_count вже
# рахувались і друкувались у консоль/GH Actions лог щоразу, але без
# алерту — той самий клас "тихої трансформації", що й invalid_cost_count
# у full_catalog_competitor_scan.py. Поріг — частка фіду, не абсолютне
# число: обрізання поодиноких довгих назв нормальне, різке зростання
# частки — ознака структурної проблеми (напр. PROM_NAME_MAX_LEN
# розсинхронізувався з реальним лімітом Prom, чи Toysi масово надсилає
# аномально довгі назви).
TRUNCATED_NAME_ALERT_FRACTION = 0.10

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
    # Розширення словника (2026-07-08) — найчастіші слова з реальних назв
    # категорій Toysi (291 унікальна категорія), яких словник ще не покривав.
    "аксесуари": "аксессуары", "догляд": "уход",
    "самокати": "самокаты", "самокат": "самокат",
    "розмальовки": "раскраски", "розмальовка": "раскраска",
    "машинки": "машинки", "машини": "машины",
    "інтерактивні": "интерактивные", "інтерактивний": "интерактивный",
    "надувні": "надувные", "зброя": "оружие", "меблі": "мебель",
    "тварини": "животные", "герої": "герои", "зошит": "тетрадь",
    "малюків": "малышей", "малят": "малышей",
    "ігрові": "игровые", "килимки": "коврики", "засоби": "средства",
    "книги": "книги", "книга": "книга", "книжка": "книжка",
    "ножиці": "ножницы", "ножі": "ножи", "товари": "товары",
    "папір": "бумага", "човни": "лодки", "сортери": "сортеры",
    "розпис": "роспись", "круги": "круги", "слайми": "слаймы",
    "мячі": "мячи", "пупси": "пупсы", "металеві": "металлические",
    "ляльок": "кукол", "антистрес": "антистресс", "ванної": "ванной",
    "розважальні": "развлекательные", "каталки": "каталки",
    "спортивні": "спортивные", "незвичайні": "необычные", "інші": "другие",
    "волоссям": "волосами", "тілом": "телом", "роботи": "роботы",
    "столики": "столики", "маски": "маски", "літаки": "самолеты",
    "вертольоти": "вертолеты", "гаджети": "гаджеты", "побутова": "бытовая",
    "лабіринти": "лабиринты", "вкладиші": "вкладыши", "картини": "картины",
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
    cleaned = re.sub(r"[«»\"()\[\],.:;!?%&+\-–—/]", " ", cleaned)
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
        # Категорії часто пишуться через кому ("Пазли, набори") — без
        # відсікання пунктуації токен "пазли," не збігається зі словниковим
        # ключем "пазли", і вся фраза хибно вважається неперекладною. Так само
        # й апостроф ("М'які іграшки") — словник зберігає ключі без апострофа
        # (як і _tokenize_name), тож без цього ж прибирання тут "м'які" ніколи
        # не збіжиться з ключем "мякі".
        wl = w.lower().strip(",.;:").replace("'", "").replace("’", "")
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
    """СТАРА фіксована сходинкова наценка за ціновим діапазоном — БЕЗ жодного
    урахування комісії Prom. Більше НЕ використовується як ціна за замовчуванням
    у _build_xml() (дивись default_retail_price нижче) — залишена лише для
    generate_royaltoys_feed.py / generate_prom_feed_top.py._margin(), які досі
    її імпортують.

    до 100 грн: +60% | 100-300: +50% | 300-700: +40% | 700-2000: +35% | 2000+: +25%
    """
    if cost < 100:    return round(cost * 1.60)
    elif cost < 300:  return round(cost * 1.50)
    elif cost < 700:  return round(cost * 1.40)
    elif cost < 2000: return round(cost * 1.35)
    else:             return round(cost * 1.25)


def default_retail_price(cost: float, category_name: str = "") -> float:
    """Ціна для SKU БЕЗ ручного запису ціни конкурента в pricing_results.csv —
    тобто майже всі товари під час першого масового імпорту (competitor_pricing.py
    --record обробляє ~200/день вручну, конкурента поки записано для жменьки SKU).

    Рахує через ту саму формулу, що й ручний конвеєр конкурентів
    (competitor_pricing.py, decide_price_for_platform): нижня межа маржі =
    (cost + cost*MIN_PROFIT) / (1 - комісія_категорії_Prom - комісія_оплати),
    ціна = max(нижня_межа, cost*NO_COMPETITOR_MULT) — а НЕ стара calc_price()
    вище, яка комісію взагалі не віднімала і на частині категорій/діапазонів
    цін давала нульову чи від'ємну маржу після реальної комісії Prom."""
    return decide_price_for_platform(cost, None, "prom", category_name)["price"]


# Toysi записує бренд MIC непослідовно (різний регістр) — підтверджено
# дослідженням повного каталогу (29298 SKU, 2026-07-10): "MIC" явно
# підтверджений власним текстом опису Toysi як реальний бренд у 99.9%
# товарів з vendor="MIC" (5937/5940), тоді як vendor="MiC" НІКОЛИ не
# отримує такого ж явного підтвердження в описі (0/1999) — це механічна
# варіація регістру того самого значення поля vendor, не окрема торгова
# марка. Категорійний профіль теж майже ідентичний (136 спільних
# категорій із 149 у MiC / 220 у MIC), а точних збігів назв товарів між
# ними лише 4 з ~7900 унікальних — забагато для "тієї самої лінійки
# товарів двічі", замало, щоб це щось доводило саме по собі; вирішальний
# доказ — явний бренд-лейбл в описі.
#
# "МІС" (кирилицею) — підтверджено ІНШИЙ, окремий бренд: 0 збігів назв
# товарів з MIC/MiC, інший асортимент (рюкзаки/канцелярія проти
# загальних іграшок), інший діапазон артикулів (100000+ проти
# 10000-30000), власний явний бренд-лейбл "Бренд: МІС" в описах. НЕ
# входить у це нормалізування — інші Unicode-символи (У+041C/0406/0421
# кирилицею проти У+004D/0049/0043 латиницею), колізії неможливі навіть
# випадково через .lower().
_VENDOR_ALIASES = {"mic": "MIC"}  # ключ — vendor.strip().lower(), значення — канонічне написання


def normalize_vendor(vendor: str) -> str:
    stripped = vendor.strip()
    return _VENDOR_ALIASES.get(stripped.lower(), stripped)


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


PROM_NAME_MAX_LEN = 130  # підтверджено буквально з реального звіту імпорту Prom
# ("Поле Назва позиції[_укр]: Максимальна довжина поля: 130, буде обрізано
# до 130 символів") — раніше ми не обрізали самі, тож Prom різав мовчки
# посимвольно, потенційно посеред слова/дужки. Обрізаємо тут САМІ, на межі
# слова, щоб контролювати результат, а не покладатись на чужий hard-cut.


def _truncate_name(text: str, max_len: int = PROM_NAME_MAX_LEN) -> str:
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    last_space = cut.rfind(" ")
    if last_space > max_len * 0.6:  # не обрізати до майже нічого, якщо пробіл дуже рано
        cut = cut[:last_space]
    return cut.rstrip(" ,.-")


def _build_xml(catalog: dict, price_overrides: dict = None, russian_text: dict = None) -> tuple[ET.Element, dict]:
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

    offers_el       = ET.SubElement(shop, "offers")
    overrides       = price_overrides or {}
    russian         = russian_text or {}
    skipped         = 0
    skipped_cheap   = 0
    overridden_count       = 0  # ціна з pricing_results.csv (конкурент перевірений вручну)
    floor_bound_count      = 0  # ціна за замовчуванням, впирається в нижню межу маржі
    multiplier_bound_count = 0  # ціна за замовчуванням, NO_COMPETITOR_MULT вищий за межу
    russian_missing_count  = 0  # немає rus-варіанту з Toysi — впало назад на українську
    truncated_name_count    = 0  # name (рос.) довша за PROM_NAME_MAX_LEN, обрізана на межі слова
    truncated_name_ua_count = 0  # name_ua (укр.) довша за PROM_NAME_MAX_LEN — окремий лічильник,
                                  # бо укр./рос. варіанти різної довжини й можуть обрізатись незалежно

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

        item_id = str(item["id"])
        if item_id in overrides:
            retail = overrides[item_id]
            overridden_count += 1
        else:
            decision = decide_price_for_platform(cost, None, "prom", item.get("category_name"))
            retail = decision["price"]
            if decision["price"] <= decision["floor"] + 0.005:
                floor_bound_count += 1
            else:
                multiplier_bound_count += 1
        stock     = item.get("stock", 0)
        available = "true" if stock > 0 else "false"

        offer = ET.SubElement(offers_el, "offer",
                              id=item_id,
                              available=available)

        # Prom.ua: пріоритет коду товару vendorCode > barcode
        vendor_code = item.get("vendor_code") or item_id
        ET.SubElement(offer, "vendorCode").text        = vendor_code
        # <name>/<description> — "російська" версія за вимогою Prom
        # (окреме поле від _ua). Toysi РЕАЛЬНО надає окремий рос. контент
        # через lang=rus (перевірено 2026-07-11: 92% назв і 95% описів по
        # всьому каталогу відрізняються від lang=ukr — не той самий текст
        # під іншим прапорцем) — раніше цей rus-фід просто не запитувався,
        # і name_ua/description_ua (справді дублікати name/description,
        # бо lang=ukr) помилково писались і в "російські" теги теж. russian
        # тут — lookup з ОКРЕМОГО запиту lang=rus (див. generate_feed);
        # якщо для SKU rus-варіанту немає (рідкість — 2 з ~29386 у
        # повному каталозі) чи russian_text не передано, м'яко падаємо
        # назад на українську, а не лишаємо поле порожнім.
        name    = item.get("name", "")
        name_ru = (russian.get(item_id) or {}).get("name") or name
        if item_id not in russian:
            russian_missing_count += 1
        if len(name_ru) > PROM_NAME_MAX_LEN:
            truncated_name_count += 1
        if len(name) > PROM_NAME_MAX_LEN:
            truncated_name_ua_count += 1
        ET.SubElement(offer, "name").text               = _truncate_name(name_ru)
        ET.SubElement(offer, "name_ua").text             = _truncate_name(name)
        ET.SubElement(offer, "price").text               = f"{retail:.2f}"
        ET.SubElement(offer, "currencyId").text          = "UAH"
        # Prom.ua використовує quantity_in_stock (а не stock_quantity, як Rozetka)
        ET.SubElement(offer, "quantity_in_stock").text   = str(stock)

        if item.get("category_id"):
            ET.SubElement(offer, "categoryId").text = item["category_id"]

        for pic_url in item.get("pictures", [])[:10]:
            ET.SubElement(offer, "picture").text = pic_url

        if item.get("vendor"):
            ET.SubElement(offer, "vendor").text = normalize_vendor(item["vendor"])

        if item.get("country"):
            ET.SubElement(offer, "country").text = item["country"]

        if item.get("barcode"):
            ET.SubElement(offer, "barcode").text = item["barcode"]

        # Prom.ua вимагає наявність <description>, навіть якщо порожній
        description_ua = append_clearance_notice(
            item.get("description", ""),
            item.get("name", ""),
            item.get("category_name", ""),
            item.get("category_id", ""),
        )
        description_ru_raw = (russian.get(item_id) or {}).get("description") or item.get("description", "")
        # CLEARANCE_NOTICE сам лишається українською (немає перекладу тексту
        # попередження) навіть у "російському" описі — прийнятний компроміс,
        # ніж взагалі не попередити покупця про уцінку.
        description_ru = append_clearance_notice(
            description_ru_raw,
            item.get("name", ""),
            item.get("category_name", ""),
            item.get("category_id", ""),
        )
        ET.SubElement(offer, "description").text = description_ru
        ET.SubElement(offer, "description_ua").text = description_ua

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
        "overridden_count": overridden_count,
        "floor_bound_count": floor_bound_count,
        "multiplier_bound_count": multiplier_bound_count,
        "russian_missing_count": russian_missing_count,
        "truncated_name_count": truncated_name_count,
        "truncated_name_ua_count": truncated_name_ua_count,
    }
    return yml, stats


def fetch_russian_text() -> dict:
    """Окремий запит lang=rus (~70МБ, той самий обсяг, що й основний
    lang=ukr) — лише для <name>/<description> Prom-фіду. Повертає
    {id: {"name":..., "description":...}}, не повний каталог — не тримаємо
    зайві поля (ціна/фото/характеристики тощо з рос-фіда нам не потрібні,
    вони й так є з lang=ukr)."""
    print("[Prom] Завантажуємо російськомовний варіант каталогу Toysi (lang=rus)...")
    rus_catalog = fetch_toysi_catalog(lang="rus")
    return {
        pid: {"name": item.get("name", ""), "description": item.get("description", "")}
        for pid, item in rus_catalog.items()
    }


def generate_feed(output_file: str = OUTPUT_FILE,
                  price_overrides: dict = None,
                  catalog: dict = None) -> None:
    if catalog is None:
        print("[Prom] Завантажуємо каталог Toysi...")
        catalog = fetch_toysi_catalog()
    if not catalog:
        print("[Prom] Каталог порожній — файл не створено.")
        return

    russian_text = fetch_russian_text()

    print(f"[Prom] Генеруємо XML для {len(catalog)} товарів...")
    root, stats = _build_xml(catalog, price_overrides=price_overrides, russian_text=russian_text)

    ET.indent(root, space="  ")
    xml_str = ET.tostring(root, encoding="unicode")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str
    xml_str = _wrap_cdata(xml_str)

    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(xml_str)

    print(f"[Prom] Готово! Збережено: {output_file}")
    print(f"[Prom] У фіді: {stats['total_in_feed']} товарів | "
          f"пропущено (без ціни): {stats['skipped_no_price']} | "
          f"дешевше {MIN_SUPPLIER_PRICE} грн: {stats['skipped_cheap']}")
    print(
        f"[Prom] Ціноутворення: {stats['overridden_count']} з ручною ціною конкурента "
        f"(pricing_results.csv), {stats['floor_bound_count']} впираються в нижню межу маржі "
        f"(коротка маржа після комісії категорії), {stats['multiplier_bound_count']} за "
        "стандартним множником NO_COMPETITOR_MULT"
    )
    print(
        f"[Prom] Російська назва: {stats['russian_missing_count']} SKU без rus-варіанту в "
        "Toysi (впало назад на українську для <name>/<description>)"
    )
    print(
        f"[Prom] Обрізання назви (>{PROM_NAME_MAX_LEN} символів, на межі слова): "
        f"{stats['truncated_name_count']} SKU у <name>, {stats['truncated_name_ua_count']} SKU у <name_ua>"
    )

    total_in_feed = stats["total_in_feed"] or 1
    worst_truncated_fraction = max(stats["truncated_name_count"], stats["truncated_name_ua_count"]) / total_in_feed
    if worst_truncated_fraction > TRUNCATED_NAME_ALERT_FRACTION:
        send_telegram_message(
            f"⚠️ generate_prom_feed.py: обрізання назви зачепило "
            f"{stats['truncated_name_count']} SKU у <name> і {stats['truncated_name_ua_count']} у "
            f"<name_ua> з {stats['total_in_feed']} ({worst_truncated_fraction * 100:.0f}%) — вище "
            f"порогу {TRUNCATED_NAME_ALERT_FRACTION * 100:.0f}%. Можлива структурна проблема "
            "(PROM_NAME_MAX_LEN розсинхронізувався з лімітом Prom, чи Toysi масово надсилає "
            "аномально довгі назви), перевір вручну."
        )


if __name__ == "__main__":
    # ВИПРАВЛЕНО 2026-07-12: раніше price_overrides тут завжди був порожнім
    # (виклик без аргументів) — ціна для КОЖНОГО SKU рахувалась з нуля через
    # decide_price_for_platform(cost, None, ...), тобто ЗАВЖДИ за формулою
    # "немає конкурента", навіть якщо prom_competitor_pricer.py вже щойно
    # застосував кращу, конкурентну ціну напряму в Prom через API. Через це
    # наступний автоімпорт Prom (кожні 4 год) тихо повертав ціну назад до
    # дефолту. load_fresh_prom_price_overrides() читає спільний стан, який
    # тепер пише prom_competitor_pricer.py, і застосовує лише свіжі (не
    # старіші 30 год) рішення — застаріле повертається до дефолтної формули.
    generate_feed(price_overrides=load_fresh_prom_price_overrides())
