import html
import math
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from competitor_pricing import (decide_price_for_platform, load_fresh_prom_price_overrides,
                                 load_description_overrides, real_toysi_cost,
                                 compute_floor, compute_total_commission, MIN_PROFIT_COMPETITOR_FLOOR)
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

# ДОДАНО (2026-07-21, пряме питання власниці: "не заплутаєтесь в
# комісіях? різні фіди різні комісії різний контроль" — прямий привід:
# generate_eva_feed.py спершу викликав decide_price_for_platform(...,
# "prom", ...) замість "eva", копіпаст-помилка з шаблону, знайдена
# лише аудитом, не структурою коду). Кожен генератор фіду тепер має
# ОДНУ, локальну константу PLATFORM — усі виклики decide_price_for_
# platform()/compute_total_commission() у цьому файлі посилаються на
# НЕЇ, не на окремі рядкові літерали в кожному місці виклику. Це не
# усуває ризик повністю (можна помилитись і в самій константі), але
# зводить точку можливої помилки до ОДНОГО рядка на файл замість
# кількох розкиданих — копіпаст усього файлу як шаблону для нового
# майданчика тепер вимагає змінити ЧИТАБЕЛЬНО ОДНЕ місце, а не шукати
# кожен виклик окремо.
PLATFORM           = "prom"
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

# 2026-07-25: Toysi сама називає деякі категорії за чужим товарним знаком
# ("конструктори типу лего") — якщо сліпо копіювати таку назву категорії в
# keywords для товару іншого виробника (vendor != LEGO), Prom блокує імпорт
# як порушення Правил розміщення інформації (живо підтверджено: 26/26 SKU з
# "лего"/"lego" в keywords мали інший vendor). Перевіряємо це тут, а не лише
# для категорії "конструктори", щоб покрити будь-яку майбутню назву категорії
# з тим самим паттерном.
_TRADEMARK_CATEGORY_TERMS = ("лего", "lego")


def _category_name_is_safe_keyword(category_name: str, vendor: str) -> bool:
    """category_name — вже .lower()-нута (див. виклик нижче). Кожен elif
    у TRADEMARK_CATEGORY_TERMS відповідає своєму бренду — тут лише лего,
    але список легко розширити такою ж парою (термін, бренд-виняток)."""
    vendor_lower = (vendor or "").strip().lower()
    if any(term in category_name for term in _TRADEMARK_CATEGORY_TERMS):
        return "lego" in vendor_lower or "лего" in vendor_lower
    return True

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
    category_name_safe = _category_name_is_safe_keyword(category_name, vendor)

    name_words = _tokenize_name(name)

    phrases_ua = list(name_words[:5])
    if category_name and category_name_safe:
        phrases_ua.append(category_name)
    if vendor:
        phrases_ua.append(vendor.lower())
        if name_words:
            phrases_ua.append(f"{name_words[0]} {vendor.lower()}")
    phrases_ua.extend(_GENERIC_MODIFIERS_UA)

    phrases_ua = _dedupe_preserve_order(phrases_ua)[:KEYWORDS_TARGET_COUNT]
    keywords_ua = _join_within_limit(phrases_ua)

    phrases_ru = [_translate_word_ua_ru(w) for w in name_words[:5]]
    if category_name and category_name_safe:
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
    return decide_price_for_platform(cost, None, PLATFORM, category_name)["price"]


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


# Заглушка Prom «Товари, загальне» (id 29, 25% комісія — НАЙВИЩА в каталозі,
# competitor_pricing.PROM_CATEGORY_ID_COMMISSION[29]). Prom кидає сюди КОЖЕН товар,
# для якого не зміг розпізнати точнішу категорію — тобто це не «правильна категорія»,
# а «не розпізнано». Тому і per-SKU кеш-хіт у 29, і виведений fallback у 29 ми
# трактуємо як «ще не категоризовано» й МОЖЕМО покращити куруваною мапою нижче.
PROM_STUB_CATEGORY_ID = "29"

# Курована мапа Toysi-категорія (назва, як у фіді) → реальний Prom portal category_id
# (2026-08-11, перекатегоризація «Товари, загальне»). Джерело й перехресна валідація:
# кожен Prom-id узято з competitor_pricing.PROM_CATEGORY_ID_COMMISSION (живий 90-
# категорійний експорт кабінету Prom, з РОС-назвами), а пара «Toysi-назва ↔ Prom-id»
# підтверджена ОДНОЧАСНО двома незалежними сигналами: (1) семантичний збіг назв і
# (2) ІДЕНТИЧНА ставка комісії між competitor_pricing.PROM_CATEGORY_COMMISSION (ключ —
# ця Toysi-назва) і PROM_CATEGORY_ID_COMMISSION (ключ — цей Prom-id). Свідомо включені
# ЛИШЕ однозначні пари (той самий принцип, що й PROM_CATEGORY_COMMISSION: не мапимо
# категорію, яка охоплює кілька Prom-категорій з різними ставками). Довгий хвіст
# Toysi-категорій без однозначної відповідності лишається на виведеному fallback/кеші.
# Це закриває саме той клас, який виведений fallback НЕ може: Toysi-категорії, які Prom
# сам стабільно кидає в заглушку 29 (тоді кеш/derive дають 29 → нічого не покращують).
TOYSI_TO_PROM_CATEGORY: dict[str, int] = {
    # Игрушки-антистресс — 23.73% (Prom 2656)
    "іграшки антистрес": 2656,
    "сквіші": 2656,
    "тягучки та стретчі": 2656,
    "лизуни, слайми та жуйки для рук": 2656,
    # Настольные игры — 22.58% (Prom 180607)
    "настільні ігри": 180607,
    # Пазлы и головоломки — 23.28% (Prom 180613)
    "пазли підлогові": 180613,
    "пазли g-toys": 180613,
    "пазли castorland": 180613,
    "пазли dankotoys": 180613,
    "пазли trefl": 180613,
    "пазли для малюків": 180613,
    "інші пазли": 180613,
    "пазли і вкладиші": 180613,
    "головоломки": 180613,
    # Конструкторы — 19.67% (Prom 2614)
    "конструктори": 2614,
    "пластикові конструктори": 2614,
    "конструктори типу лего": 2614,
    "дерев'яні конструктори": 2614,
    "металеві конструктори": 2614,
    "магнітні конструктори": 2614,
    "незвичайні конструктори": 2614,
    # Куклы, пупсы — 19.81% (Prom 2605)
    "ляльки": 2605,
    "пупси": 2605,
    # Мягкие игрушки — 19.93% (Prom 2604)
    "м'які іграшки": 2604,
    "ведмеді": 2604,
    # Игровые фигурки, роботы трансформеры — 19.92% (Prom 2638)
    "трансформери": 2638,
    "роботи": 2638,
    # Игрушечные машинки, самолетики, техника — 15.75% (Prom 2606)
    "пластикові машинки": 2606,
    "машини гіганти": 2606,
    "літаки і вертольоти": 2606,
    "планери": 2606,
    "машинки на батарейках": 2606,
    # Радиоуправляемые игрушки — 15.59% (Prom 2613)
    "машинки ру": 2613,
    # Тематические игровые наборы — 19.59% (Prom 2629)
    "лікарські набори": 2629,
    "перукарські набори": 2629,
    "набори інструментів": 2629,
    "супермаркет": 2629,
    # Игрушки для игр с песком, водой и снегом — 19.52% (Prom 2642)
    "пісочні набори": 2642,
    "лопатки і граблі": 2642,
    "пасочки": 2642,
    "кінетичний пісок": 2642,
    # Развивающие и обучающие игрушки — 10.87% (Prom 2602)
    "розвиваючі килимки": 2602,
    "набори для навчання": 2602,
    # Интерактивные детские игрушки — 19.8% (Prom 2608)
    "розважальні інтерактивні іграшки": 2608,
    # Детские игрушки-каталки — 15.86% (Prom 2627)
    "іграшки - каталки": 2627,
    # Надувные матрасы — 14.24% (Prom 2010)
    "надувні матраси": 2010,
}


def _map_curated_category(category_name: str):
    """Реальний Prom category_id для Toysi-категорії за куруваною мапою, або None.
    Нормалізація ключа — та сама, що в PROM_CATEGORY_COMMISSION (strip+lower)."""
    return TOYSI_TO_PROM_CATEGORY.get((category_name or "").strip().lower())


def _derive_toysi_to_prom_category(catalog: dict, prom_category_cache: dict) -> dict:
    """Виводить {toysi_category_id: Prom_category_id} з товарів, яких Prom УЖЕ
    категоризував (prom_category_cache), згруповано за Toysi-категорією: для
    кожної Toysi-категорії — НАЙЧАСТІШИЙ реальний Prom category_id серед її
    вже-категоризованих SKU.

    НАВІЩО (2026-08-01, живий root-cause після фіксу вітрини 6000): <categoryId>
    досі проставлявся ЛИШЕ для SKU, що вже є в prom_category_cache (той
    наповнюється тільки з товарів, яких Prom сам категоризував) — класичний
    "категорійний круг". Після відновлення ~2750 нових SKU (ever_live-фікс) 3800
    із 6000 не мали categoryId → Prom позначав їх "помилки в даних". Ця мапа дає
    новому SKU fallback-категорію ТІЄЇ Ж Toysi-категорії (те, що Prom сам
    призначив схожим товарам) замість порожнечі. Ризик низький — це власна
    категоризація Prom, узагальнена в межах однієї Toysi-категорії; Prom і так
    авто-визначав би категорію за назвою, лише гірше. Само-покращується: кеш
    росте → покриття мапи росте. Нічого не вигадує: Toysi-категорія без жодного
    вже-категоризованого прикладу лишається без fallback (як і раніше)."""
    if not prom_category_cache:
        return {}
    from collections import Counter, defaultdict
    tally = defaultdict(Counter)
    for item_id, item in catalog.items():
        pcat = (prom_category_cache.get(item_id) or {}).get("category_id")
        tcat = (item.get("category_id") or "").strip()
        if pcat and tcat:
            tally[tcat][str(pcat)] += 1
    return {tcat: c.most_common(1)[0][0] for tcat, c in tally.items()}


def _build_xml(
    catalog: dict,
    prom_category_cache: dict = None,
    price_overrides: dict = None,
    russian_text: dict = None,
    description_overrides: dict = None,
    full_catalog: dict = None,
) -> tuple[ET.Element, dict]:
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
    desc_overrides  = description_overrides or {}
    described_count = 0  # Vis-9: SKU, що отримали вручну написаний опис замість сирого Toysi
    skipped         = 0
    skipped_cheap   = 0
    overridden_count       = 0  # ціна з pricing_results.csv (конкурент перевірений вручну)
    floor_clamped_count    = 0  # override-ціна БУЛА нижче свіжого floor → підняли до floor (суцільний гард)
    floor_bound_count      = 0  # ціна за замовчуванням, впирається в нижню межу маржі
    multiplier_bound_count = 0  # ціна за замовчуванням, NO_COMPETITOR_MULT вищий за межу
    russian_missing_count  = 0  # немає rus-варіанту з Toysi — впало назад на українську
    truncated_name_count    = 0  # name (рос.) довша за PROM_NAME_MAX_LEN, обрізана на межі слова
    truncated_name_ua_count = 0  # name_ua (укр.) довша за PROM_NAME_MAX_LEN — окремий лічильник,
                                  # бо укр./рос. варіанти різної довжини й можуть обрізатись незалежно
    resolved_category_count = 0  # SKU з РЕАЛЬНИМ Prom category_id з кешу (не Toysi-ID, не порожньо)
    fallback_category_count = 0  # SKU без кеш-категорії, але з виведеною fallback-Prom-категорією (розрив "категорійного круга")
    curated_category_count  = 0  # SKU, категоризовані куруваною мапою TOYSI_TO_PROM_CATEGORY (проти заглушки «Товари, загальне»)

    # Мапа Toysi-категорія → найчастіший Prom category_id (з уже-категоризованих
    # SKU) — fallback для SKU, яких ще нема в кеші.
    # ВИПРАВЛЕНО (2026-08-11, перекатегоризація ~3190 у «Товари, общее» 25%): виводимо
    # мапу з ПОВНОГО каталогу Toysi (full_catalog), а не лише з поточного топ-6000
    # (catalog). Prom-категорії в кеші є для всіх ~5836 імпортованих SKU, але через
    # ротацію більшість із них ЗАРАЗ поза топ-6000 — тож derive лише по топ-6000
    # «не бачив» їхню Toysi→Prom відповідність, і SKU з тих самих Toysi-категорій
    # лишались без <categoryId> (Prom → «Товари, общее»). Derive по повному каталогу
    # захоплює всі кеш-приклади → набагато ширше покриття fallback. Монотонно:
    # додає лише коректний fallback тієї Ж Toysi-категорії, нічого не перезаписує.
    derived_prom_category = _derive_toysi_to_prom_category(full_catalog or catalog, prom_category_cache)

    for item in catalog.values():
        cost = real_toysi_cost(item)  # 2026-07-22: реальна собівартість з урахуванням знижки Toysi, не сира каталожна ціна
        if cost <= 0:
            skipped += 1
            continue
        if cost < MIN_SUPPLIER_PRICE:
            skipped_cheap += 1
            continue

        item_id = str(item["id"])
        if item_id in overrides:
            retail = overrides[item_id]
            # СУЦІЛЬНИЙ floor-гард (усі ~6000 SKU, на КОЖНІЙ генерації фіду). Override — це
            # конкурентна ціна репрайсера, яку могло занизити зростання собівартості Toysi ПІСЛЯ
            # ціноутворення (лаг ротації: репрайсер обходить каталог за кілька діб). Тут, у момент
            # публікації, зі СВІЖОЮ собівартістю (cost = real_toysi_cost, кеш ≤1 год) не даємо
            # опублікувати нижче 3%-floor: піднімаємо ЛИШЕ вгору до floor (ніколи не опускаємо й
            # ніколи не нижче конкурента). Формульна гілка (else) floor уже поважає через
            # decide_price_for_platform. Read-only по стану репрайсера — жодної гонки за файл.
            floor = compute_floor(
                cost,
                compute_total_commission(PLATFORM, item.get("category_name"), retail),
                MIN_PROFIT_COMPETITOR_FLOOR,
            )
            if retail < floor:
                # ceil до копійки (не round) — гард ніколи не має публікувати навіть на пів-копійки
                # НИЖЧЕ floor; -1e-6 гасить float-шум, щоб рівно-копійчаний floor не стрибав угору.
                retail = math.ceil(floor * 100 - 1e-6) / 100
                floor_clamped_count += 1
            overridden_count += 1
        else:
            decision = decide_price_for_platform(cost, None, PLATFORM, item.get("category_name"))
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

        # ВИПРАВЛЕНО (2026-07-25, живий root-cause "не росте кабінет"):
        # раніше сюди йшло item["category_id"] — це TOYSI-власний ID
        # категорії (з їхньої XML, parser.py), не Prom-категорія. Prom
        # очікує ID зі СВОЄЇ таксономії; чужий/невідповідний ID Prom
        # мовчки ігнорує й авто-визначає категорію сам (живо підтверджено
        # 25.07: звіт імпорту показав "Для 662 товарів автоматично
        # визначена категорія" — 72% партії). prom_category_cache.json
        # (build_prom_category_cache(), generate_google_feed.py) уже дає
        # РЕАЛЬНИЙ Prom category_id для SKU, які вже імпортовані — той
        # самий кеш, що prom_competitor_pricer.py давно використовує для
        # точної комісії, просто не був підключений сюди, до самого фіда.
        # Пріоритет визначення Prom-категорії (2026-08-11, курована мапа проти
        # заглушки «Товари, загальне» 25%):
        #   1) реальна категорія з кешу (Prom сам категоризував цей SKU) — але ЛИШЕ
        #      якщо це не заглушка 29 (29 = «не розпізнано», а не правильна категорія);
        #   2) курована мапа Toysi-назва→Prom-id (звірена з таблицями комісій) — точний
        #      намір для найбільших категорій; ПЕРЕКРИВАЄ й заглушку-кеш, і derive-29;
        #   3) виведений fallback (найчастіший Prom-id тієї ж Toysi-категорії) — теж лише
        #      якщо не заглушка;
        #   4) якщо кращого нема — лишаємо реальну з кешу (навіть заглушку 29); інакше
        #      порожньо (Prom однак кине в 29 — той самий результат).
        real_str    = str(((prom_category_cache or {}).get(item_id) or {}).get("category_id") or "")
        curated_cat = _map_curated_category(item.get("category_name"))
        derived_cat = derived_prom_category.get((item.get("category_id") or "").strip())

        if real_str and real_str != PROM_STUB_CATEGORY_ID:
            ET.SubElement(offer, "categoryId").text = real_str
            resolved_category_count += 1
        elif curated_cat:
            ET.SubElement(offer, "categoryId").text = str(curated_cat)
            curated_category_count += 1
        elif derived_cat and str(derived_cat) != PROM_STUB_CATEGORY_ID:
            ET.SubElement(offer, "categoryId").text = str(derived_cat)
            fallback_category_count += 1
        elif real_str:  # == заглушка 29, кращого джерела нема — лишаємо як є
            ET.SubElement(offer, "categoryId").text = real_str
            resolved_category_count += 1

        for pic_url in item.get("pictures", [])[:10]:
            ET.SubElement(offer, "picture").text = pic_url

        if item.get("vendor"):
            ET.SubElement(offer, "vendor").text = normalize_vendor(item["vendor"])

        # Vis-9: override — коли Toysi дає лише мінімальний "Бренд+Країна"
        # boilerplate (93/970 SKU), вручну написаний текст (desc_override)
        # ЗАМІНЮЄ сирий опис Toysi ПОВНІСТЮ, для ОБОХ мовних варіантів
        # нижче (один написаний текст на товар, а не окремі переклади) —
        # той самий override перекриває й <country>, якщо в записі є
        # "country" (реальне походження часом відрізняється від того, що
        # (можливо помилково) вказано в Toysi).
        desc_override = desc_overrides.get(item_id)
        raw_description = item.get("description", "")
        country = item.get("country")
        if desc_override:
            described_count += 1
            raw_description = desc_override.get("description") or raw_description
            country = desc_override.get("country") or country

        if country:
            ET.SubElement(offer, "country").text = country

        if item.get("barcode"):
            ET.SubElement(offer, "barcode").text = item["barcode"]

        # Prom.ua вимагає наявність <description>, навіть якщо порожній
        description_ua = append_clearance_notice(
            raw_description,
            item.get("name", ""),
            item.get("category_name", ""),
            item.get("category_id", ""),
        )
        description_ru_raw = (
            raw_description if desc_override
            else (russian.get(item_id) or {}).get("description") or raw_description
        )
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
        "floor_clamped_count": floor_clamped_count,
        "floor_bound_count": floor_bound_count,
        "multiplier_bound_count": multiplier_bound_count,
        "russian_missing_count": russian_missing_count,
        "truncated_name_count": truncated_name_count,
        "truncated_name_ua_count": truncated_name_ua_count,
        "described_count": described_count,
        "resolved_category_count": resolved_category_count,
        "fallback_category_count": fallback_category_count,
        "curated_category_count": curated_category_count,
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
                  catalog: dict = None,
                  description_overrides: dict = None,
                  prom_category_cache: dict = None,
                  full_catalog: dict = None) -> None:
    if catalog is None:
        print("[Prom] Завантажуємо каталог Toysi...")
        catalog = fetch_toysi_catalog()
    if not catalog:
        print("[Prom] Каталог порожній — файл не створено.")
        return

    russian_text = fetch_russian_text()

    print(f"[Prom] Генеруємо XML для {len(catalog)} товарів...")
    root, stats = _build_xml(
        catalog, prom_category_cache=prom_category_cache, price_overrides=price_overrides,
        russian_text=russian_text, description_overrides=description_overrides,
        # Повний каталог для виведення Toysi→Prom категорійної мапи (ширше покриття
        # fallback-категорій, ніж лише топ-6000) — див. _build_xml/_derive.
        full_catalog=full_catalog,
    )

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
        f"[Prom] Суцільний floor-гард: {stats['floor_clamped_count']} override-цін були нижче "
        f"свіжого floor і піднято до нього (захист маржі між ротаціями репрайсера)."
    )
    print(
        f"[Prom] Російська назва: {stats['russian_missing_count']} SKU без rus-варіанту в "
        "Toysi (впало назад на українську для <name>/<description>)"
    )
    print(
        f"[Prom] Обрізання назви (>{PROM_NAME_MAX_LEN} символів, на межі слова): "
        f"{stats['truncated_name_count']} SKU у <name>, {stats['truncated_name_ua_count']} SKU у <name_ua>"
    )
    print(f"[Prom] Vis-9: {stats['described_count']} SKU отримали вручну написаний опис "
          "(description_overrides.json) замість сирого Toysi")
    cache_total = len(prom_category_cache) if prom_category_cache else 0
    print(
        f"[Prom] Категорії: {stats['resolved_category_count']} SKU з реальним Prom category_id "
        f"(кеш: {cache_total} SKU відомо) + {stats['curated_category_count']} SKU з куруваної мапи "
        f"(проти заглушки «Товари, загальне») + {stats['fallback_category_count']} SKU з fallback-категорією "
        f"(найчастіший Prom-id тієї ж Toysi-категорії) — решта без <categoryId>, Prom визначає сам за назвою."
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


# ВИДАЛЕНО (2026-07-25, рішення власниці): standalone-запуск цього файлу
# (генерація повного каталогу prom_feed.xml, 29000+ SKU) прибрано разом із
# кроками workflow "Generate Prom feed (full catalog)"/"Upload prom_feed.xml
# to VPS" — історичний підхід ще з першого коміту проєкту, до появи
# куруваного топ-N (generate_prom_feed_top.py). Жодного реального споживача
# не мав: нічний сканер конкурентів тягне повний каталог напряму з Toysi
# (fetch_toysi_catalog()), а Prom імпортує лише prom_feed_top.xml. Функції
# generate_feed()/_build_xml() у цьому файлі лишаються — їх продовжує
# використовувати generate_prom_feed_top.py.
