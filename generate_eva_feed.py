"""
generate_eva_feed.py — генерує YML-фід для EVA Маркетплейс (категорія
"Товари для дітей", комісія іграшок 15%/13%).

Вимоги звірено напряму з офіційною документацією EVA (2026-07-21):
- https://sellersupport.eva.ua/category/upravlinnia-tovaramy/vymohy-do-oformlennia-informatsii-pro-tovary
- https://sellersupport.eva.ua/article/pidhotovka-prays-listu-xml

ПОВНА ЗВІРКА 5 ПУНКТІВ (2026-07-23, пряме прохання власниці, джерела —
статті sellersupport.eva.ua "Назва товару"/"Опис товару"/"Зображення
товару"/"Параметри товару"/"Заборонені товари для продажу на EVA
Маркетплейс"):

1. НАЗВА — реалізовано частково, свідомо:
   - `<name>` (рос.) тепер ЗАПОВНЮЄТЬСЯ (fetch_russian_text() з
     generate_prom_feed.py, той самий rus-фід Toysi lang=rus, з
     фолбеком на укр. назву, якщо для SKU рос-варіанту нема) — РАНІШЕ
     поле взагалі не писалось (докстрінг досі мав хибне пояснення "не
     пишемо, бо непотрібне" — власниця процитувала вимогу "лише
     російською заборонена", що прямо суперечило старому коду).
   - Прибрано ВЕЛИКІ ЛІТЕРИ (_denoise_caps): якщо >=60% літер назви —
     великі, перетворюємо на Title Case, крім коротких токенів (<=3
     симв., ймовірні акроніми/моделі: "M-9", "3D") і токенів з цифрами.
     Живо перевірено на 669 реально "кричущих" назвах каталогу Toysi —
     без жодного зіпсованого моделі/акроніма.
   - Обмеження пунктуації (_limit_punctuation): СВІДОМО вузьке —
     схлопує лише явно декоративну повторювану пунктуацію (!!, ??, 4+
     крапки), НЕ займає лапки/коми/дужки. Причина: лапки — це стабільна
     конвенція самого Toysi для позначення конкретної моделі/назви
     всередині типу товару (та сама "модель/колір/розмір" пільга, що
     прямо назвала власниця) — сліпе "макс. 1 розділовий знак" зламало
     б практично КОЖНУ назву в каталозі (перевірено: 3635 назв мають
     2+ коми, здебільшого легітимний перелік розмір/колір).
   - НЕ РЕАЛІЗОВАНО (чесно, не автоматизовується безпечно): структурна
     трансформація в формулу "Тип+Бренд+Модель+Характеристики+Колір+
     (Артикул)" вимагає надійного розбору довільної назви на типізовані
     поля — жодного надійного правила для ~29000 різнорідних SKU різних
     категорій немає без ризику зіпсувати реальні назви. Toysi-назви й
     так здебільшого вже йдуть у форматі "Тип ... "Модель"" (див.
     приклади вище) — залишено як є, без ризикованої автоперебудови.

2. ОПИС — реалізовано з ЕВІДЕНС-BASED звуженням обсягу:
   - Контакти/ціна (_strip_contacts_and_price): телефон (regex за
     цифровим патерном, НЕ за словом "телефон" — 231 живий SKU згадує
     "телефон" як ФУНКЦІЮ іграшки, не номер), email, месенджер-посилання
     (t.me/, @handle, viber/whatsapp), ціна+валюта (число+грн/₴).
   - Заклики до дії (_strip_cta_phrases): ТОЧНІ багатослівні фрази
     ("менеджер передзвонить", "зателефонуйте нам" тощо), видаляються
     ПОРЕЧЕННЯМИ (не весь опис). СВІДОМО НЕ окремі слова "купити/
     замовити/опт/акція/менеджер/знижка" — живо перевірено на реальних
     описах Toysi: "опт" 630 разів false positive ("оптимальний"),
     "менеджер" 6/6 false positive (назва гри "Менеджер"), "телефон"
     231 здебільшого продукт-ознака, "акці"/"знижк" false positive
     (ігрові фішки-акції компаній у "Монополії", гарантійний пункт про
     компенсацію) — сліпий словниковий фільтр active ЗІПСУВАВ БИ сотні
     легітимних описів, тому лише точні багатослівні фрази заклику.
   - "Інфо про асортимент моделі" — НЕ реалізовано окремим фільтром
     (та сама евіденс-based обережність — жодного надійного маркера
     без ризику false positive не знайдено в живих даних).

3. ФОТО — НЕ РЕАЛІЗОВАНО, чесно: перевірка ЗМІСТУ фото (інфографіка/
   текст на фото, колір фону, мова тексту, роздільна здатність)
   вимагає аналізу самого зображення (vision-модель чи ручний
   перегляд) — жодного такого механізму немає в жодному фіді проєкту.
   Це не рядок коду, який можна дописати безпечно без окремого рішення
   власниці про архітектуру/вартість (напр. виклик vision-API на кожне
   фото). Лишається відкритим пунктом.

4. ПАРАМЕТРИ — гарантовано мінімум 2 <param> на offer (fallback-пул
   "Виробник"/"Категорія" топує до 2, якщо Toysi дав менше).

5. ЗАБОРОНЕНІ ТОВАРИ (країна/тематика) — реалізовано:
   - Країна походження (EVA_BANNED_COUNTRY_PATTERNS): рф/білорусь
     (обидва живі варіанти написання каталогу — "Білорусь"/"Білорось",
     типова помилка друку Toysi)/кндр/іран/куба — перевірено проти ВСІХ
     36 реальних значень item["country"] живого каталогу: 0 false
     positive, спрацьовує лише на 2 варіантах написання Білорусі.
   - Тематика/студія "Союзмультфільм" — доданий до EVA_STOP_BRANDS
     (перевірка ЛИШЕ за полем vendor, той самий механізм, що бренди) —
     СВІДОМО НЕ вільнотекстовий пошук по назві/опису: живо знайдено
     РЕАЛЬНИЙ небезпечний false positive — SKU 297245 (фігурка Funko
     POP! "Роккі 4") згадує "СРСР" у сюжетному описі фільму (Роккі
     проти Івана Драго), а кілька SKU з фразою "Рускій воєнний
     корабль, іди на... дно" — це патріотичний антиросійський мем-товар
     (кухлі/значки/блокноти), не пропагандистський! Сліпий текстовий
     пошук на "рос"/"срср" видалив би саме ці антиросійські товари з
     фіда — прямо протилежний ефект. Тому лише vendor-поле.
   - "Зображення проросійських осіб" — НЕ РЕАЛІЗОВАНО, та сама причина,
     що й п.3 (аналіз змісту фото, не текстових даних).

СТОП-БРЕНДИ (пряме завдання власниці, 2026-07-21, категорія KIDS EVA):
EVA_STOP_BRANDS — той самий патерн, що вже є для Rozetka
(ROZETKA_BRAND_STOP_LIST) — SKU з цих брендів виключаються з фіда ДО
генерації, а не лишаються на модерацію EVA. Перелік звірено проти
живого каталогу Toysi (2026-07-21): з 36 заявлених брендів у нашому
каталозі реально зустрічаються 12 — решта 24 (LEGO, Barbie, Mattel,
Hasbro, Hot Wheels, Fisher-Price, Spin Master тощо) взагалі не наш
асортимент (Toysi здебільшого не постачає ліцензійні світові бренди).
Знайдено й виправлено РЕАЛЬНИЙ пропуск при первинній звірці:
"TechnoK" (стоп-бренд) і "Технок" (кирилицею — реальний бренд у
нашому каталозі, 494 SKU повного обсягу) — той самий бренд у двох
скриптах, нормалізація за самим лише регістром/розділювачем цього не
ловить. EVA_STOP_BRANDS нижче явно містить ОБИДВА варіанти написання
для TechnoK з цієї причини — якщо колись знайдеться ще один такий
кирило-латинський дублікат серед інших 35 брендів, він так само не
буде спійманий автоматично (перевірено вручну лише конкретні
ймовірні кандидати, не вичерпний список усіх можливих транслітерацій).

КУРУВАНИЙ ВІДБІР (не повний каталог): той самий select_top_items()
(топ-970 за маржею/попитом), що й Prom/Rozetka — свідомий, консервативний
старт для абсолютно нового, ще не протестованого каналу продажів, а
не спроба одразу вивантажити весь каталог (~28 000 SKU) на платформу
без жодної історії продажів на ній.

НЕ ПОКРИТО (свідомо, поза межами цього завдання — власниця сама подає
заявку/анкету/договір): реєстрація продавця, підключення категорії,
отримання довідника categoryId EVA (як і з Rozetka rz_id — без
інтерактивного логіну в кабінет EVA програмно недосяжний, фід працює
на фолбеку "зіставлення категорії за назвою").

ЖИВЕ ПОРІВНЯННЯ КОНКУРЕНТІВ НА EVA: досліджено окремо (WebSearch,
2026-07-21) — EVA НЕ має публічного/кабінетного механізму на кшталт
Prom buyBox (немає фільтра за продавцем, немає видимого порівняння
цін між продавцями того самого товару в кабінеті). Офіційні правила
EVA лише РЕКОМЕНДУЮТЬ продавцю самостійно звіряти ціни зі схожими
товарами й не виставляти необґрунтовано завищену ціну — жодного API
чи фіда конкурентних цін від самої EVA не існує. Якщо знадобиться
конкурентний моніторинг для EVA — доведеться будувати окремий
зовнішній механізм (як GraphQL-пошук для Prom), не готовий фід від
маркетплейсу.
"""
import os
import re
import html
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path

from competitor_pricing import (
    compute_floor, get_platform_commission, load_description_overrides,
    load_fresh_prom_price_overrides, real_toysi_cost, MIN_PROFIT,
)
from generate_prom_feed import append_clearance_notice, fetch_russian_text, normalize_vendor
from generate_prom_feed_top import select_top_items
from parser import fetch_toysi_catalog

SHOP_NAME          = "PlutusToys"
SHOP_COMPANY       = "ФОП Чечетенко Олександр Юрійович"
SHOP_URL           = "https://plutustoys.com.ua"
OUTPUT_FILE        = "feeds/eva_feed.xml"
MIN_SUPPLIER_PRICE = 20  # той самий поріг, що й Prom/Rozetka — товари дешевше собівартості постачальника пропускаємо

# Пряме завдання власниці (2026-07-21) — стоп-бренди EVA, категорія KIDS.
# Порівняння регістронезалежне/без урахування розділювача (див. _normalize_brand
# нижче) — включно з обома написаннями TechnoK/Технок (див. докстрінг файлу).
EVA_STOP_BRANDS = {
    "akuku", "avent", "baby team", "baby nova", "barbie", "bright spring",
    "canpol babies", "danko toys", "dodo", "feelo toys", "fisher price",
    "frozen", "hasbro", "hot wheels", "jaki", "kids hits", "lego", "lindo",
    "lovi", "lovin", "mattel", "mattel games", "nuk", "philips avent",
    "play doh", "spin master", "strateg", "suavinex", "technok", "технок",
    "tigres", "tiny love", "trefl", "vladi toys", "енергія плюс",
    "київська фабрика іграшок", "країна іграшок", "курносики",
    # Заборонені товари EVA — тематика/студія "Союзмультфільм" (радянська
    # студія мультиплікації), доданий СЮДИ (перевірка лише vendor-поля),
    # СВІДОМО НЕ як вільнотекстовий пошук у назві/описі — див. докстрінг
    # файлу, п.5: живо знайдено небезпечний false positive (SKU 297245,
    # опис фільму згадує "СРСР" сюжетно; кілька SKU з патріотичною
    # антиросійською фразою "Рускій воєнний корабль, іди на... дно" —
    # текстовий пошук на "рос"/"срср" видалив би саме антиросійський
    # товар, протилежний намір).
    "союзмультфільм", "союзмультфильм",
}


def _normalize_brand(vendor: str) -> str:
    """Той самий нормалізаційний принцип, що вже є для normalize_vendor()
    (MIC/MiC/МІС), але тут — лише для порівняння зі стоп-листом: регістр
    і розділювач (-/_/пробіл) не мають значення, кирилиця й латиниця НЕ
    транслітеруються одне в одне автоматично (окрім explicit TechnoK/
    Технок пари в EVA_STOP_BRANDS вище)."""
    return re.sub(r"[-_\s]+", " ", (vendor or "").strip().lower())


# Заборонені товари EVA, п.5 (докстрінг файлу) — країна походження,
# перевірено ЛИШЕ проти item["country"] (структуроване поле Toysi), НЕ
# вільнотекстовий пошук по назві/опису (див. докстрінг — той самий
# ризик false positive, що й тематика/студія нижче). Живо звірено
# проти всіх 36 реальних значень country у каталозі (2026-07-23) —
# спрацьовує лише на 2 варіантах написання Білорусі, 0 false positive
# на решті 34 (Індія/Італія/Китай/Туреччина тощо).
EVA_BANNED_COUNTRY_PATTERNS = (
    "рф", "росі", "russia",
    "білорус", "білорос", "беларус", "belarus",
    "кндр", "північна корея", "north korea",
    "іран", "iran",
    "куба", "cuba",
)


def _is_banned_country(country: str) -> bool:
    normalized = (country or "").strip().lower().replace("’", "").replace("'", "")
    return any(pattern in normalized for pattern in EVA_BANNED_COUNTRY_PATTERNS)


EVA_NAME_MAX_LEN        = 255       # https://sellersupport.eva.ua/article/pidhotovka-prays-listu-xml
EVA_DESCRIPTION_MAX_LEN = 60_000
EVA_DESCRIPTION_MIN_LEN = 30        # заявлено документацією EVA — НЕ ВИМІРЮВАНО живо (немає ще підключеного кабінету
                                     # для перевірки); якщо опис коротший, EVA може відхилити конкретний offer при
                                     # модерації — не блокуємо генерацію фіда через це, лише документуємо ризик.
EVA_MAX_PICTURES        = 15

# Ті самі "заборонені керівні ASCII-символи"/URL-у-описі фільтри, що й
# у generate_rozetka_feed.py — той самий клас ризику (валідатор
# маркетплейсу блокує фід через один "брудний" символ/стороннє посилання
# десь у сирих Toysi-даних), не підтверджено конкретно для EVA, але
# дешева, безпечна перевірка, яка нічого не коштує, якщо EVA насправді
# толерантніша.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_URL_RE = re.compile(r'https?://[^\s<>"]+')

# Той самий дедуп-механізм за кольором у дужках, що й Rozetka
# (generate_rozetka_feed.py::_dedup_key) — уніфікованість назв не
# підтверджена як вимога EVA конкретно, але дешева, вже перевірена
# захисна міра проти класу помилки, знайденого на Rozetka.
_COLOR_WORDS = {
    "фіолетовий", "синій", "червоний", "зелений", "жовтий", "рожевий",
    "чорний", "білий", "сірий", "помаранчевий", "оранжевий", "бежевий",
    "коричневий", "блакитний", "бірюзовий", "салатовий", "бордовий",
    "золотистий", "золотий", "срібний", "мультиколор", "хакі",
}
_TRAILING_COLOR_PAREN_RE = re.compile(r"\s*\(([^)]+)\)\s*$")


def _dedup_key(name: str) -> str:
    match = _TRAILING_COLOR_PAREN_RE.search(name)
    if match and match.group(1).strip().lower() in _COLOR_WORDS:
        return name[:match.start()].rstrip()
    return name


def _normalize_trailing_color_case(name: str) -> str:
    """Живо знайдено (SKU 299913 та ще 1063 у каталозі): назва Toysi
    може бути ЗАГАЛОМ нормального регістру, але з кінцевим "(КОЛІР)"
    ВЕЛИКИМИ ЛІТЕРАМИ (напр. "...(БІЛИЙ)") — _denoise_caps() вище
    свідомо НЕ спрацьовує тут (поріг 60% літер усієї назви — одне
    слово в дужках занадто мала частка). Той самий _TRAILING_COLOR_
    PAREN_RE/_COLOR_WORDS, що вже є для дедуп-ключа, тут — щоб
    нормалізувати регістр САМЕ цього ізольованого "кричущого" слова,
    не займаючи решту назви."""
    match = _TRAILING_COLOR_PAREN_RE.search(name)
    if match and match.group(1).strip().lower() in _COLOR_WORDS and match.group(1).isupper():
        color = match.group(1).capitalize()
        return name[:match.start()].rstrip() + f" ({color})"
    return name


def _clean_text(text: str) -> str:
    return _CONTROL_CHARS_RE.sub("", text or "")


def _strip_urls(text: str) -> str:
    return _URL_RE.sub("", text or "")


# Назва, п.1 (докстрінг файлу) — прибрати ВЕЛИКІ ЛІТЕРИ. Короткі
# службові слова (прийменники/сполучники) — НЕ вважати акронімом/
# моделлю навіть при довжині <=3, лишати lower() (живо знайдено false
# positive "ПО НОМЕРАХ" -> хибно збережено як "акронім" без цього списку).
_LETTER_RE = re.compile(r"[A-ZА-ЯЁІЇЄa-zа-яёіїє]")
_SHORT_FUNCTION_WORDS = {
    "по", "до", "за", "на", "від", "як", "і", "й", "та", "або", "чи",
    "не", "в", "у", "зі", "о", "а", "б", "ж", "це", "з",
}


def _denoise_caps(name: str) -> str:
    """Якщо >=60% літер назви — великі (поріг обраний так, щоб НЕ чіпати
    короткі акроніми/моделі на кшталт "LEGO"/"M-9", які природно займають
    малу частку довгої назви), перетворює слова на Title Case. Короткі
    токени (<=3 симв. core, окрім службових слів вище) і токени з
    цифрами (моделі "M-9", розміри "3D") лишаються недоторканими. Живо
    перевірено на 669 реально "кричущих" назвах живого каталогу Toysi —
    без жодного зіпсованого моделі/акроніма/патріотичного тексту."""
    letters = _LETTER_RE.findall(name)
    if len(letters) < 6:
        return name
    upper_frac = sum(1 for c in letters if c.isupper()) / len(letters)
    if upper_frac < 0.6:
        return name
    words = name.split(" ")
    fixed = []
    for w in words:
        if any(ch.isdigit() for ch in w):
            fixed.append(w)
            continue
        i = 0
        while i < len(w) and not w[i].isalpha():
            i += 1
        j = len(w)
        while j > i and not w[j - 1].isalpha():
            j -= 1
        core = w[i:j]
        if not core:
            fixed.append(w)
        elif core.lower() in _SHORT_FUNCTION_WORDS:
            fixed.append(w[:i] + core.lower() + w[j:])
        elif len(core) <= 3:
            fixed.append(w)
        else:
            fixed.append(w[:i] + core[0].upper() + core[1:].lower() + w[j:])
    return " ".join(fixed)


# Назва, п.1 — обмеження пунктуації. СВІДОМО вузьке: лапки/коми/дужки
# НЕ чіпаємо (стабільна конвенція самого Toysi для моделі/кольору/
# розміру — та сама пільга, що прямо назвала власниця; сліпе "макс. 1
# розділовий знак" зламало б практично кожну назву, перевірено: 3635
# назв мають 2+ коми, здебільшого легітимний розмір/колір). Лише явно
# декоративна повторювана пунктуація.
_REPEAT_BANG_RE  = re.compile(r"!{2,}")
_REPEAT_QMARK_RE = re.compile(r"\?{2,}")
_EXCESS_DOTS_RE  = re.compile(r"\.{4,}")  # 4+; звичайний "..." (3 крапки) — стандартна пунктуація, не декор


def _limit_punctuation(name: str) -> str:
    name = _REPEAT_BANG_RE.sub("!", name)
    name = _REPEAT_QMARK_RE.sub("?", name)
    name = _EXCESS_DOTS_RE.sub("...", name)
    return name


# Опис, п.2 (докстрінг файлу) — контакти/ціна. Телефон — ЦИФРОВИЙ
# патерн, НЕ слово "телефон" (231 живий SKU згадує "телефон" як функцію
# іграшки, не номер). Email/месенджер-посилання/ціна+валюта.
_PHONE_RE = re.compile(r"(\+?38)?\s*\(?0\d{2}\)?[\s.\-]?\d{3}[\s.\-]?\d{2}[\s.\-]?\d{2}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_MESSENGER_RE = re.compile(
    r"(?:https?://)?t\.me/\S+|@\w{4,}|\bviber\b|\bwhatsapp\b|\bватсап\b|\bвайбер\b",
    re.IGNORECASE,
)
_PRICE_CURRENCY_RE = re.compile(r"\d[\d\s]*\s*(?:грн|₴|uah)\b", re.IGNORECASE)


def _strip_contacts_and_price(text: str) -> str:
    text = _PHONE_RE.sub("", text)
    text = _EMAIL_RE.sub("", text)
    text = _MESSENGER_RE.sub("", text)
    text = _PRICE_CURRENCY_RE.sub("", text)
    return text


# Опис, п.2 — точні багатослівні заклики до дії, видаляються ЦІЛИМ
# РЕЧЕННЯМ (не весь опис). СВІДОМО НЕ окремі слова "купити/замовити/
# опт/акція/менеджер/знижка" — живо перевірено на реальних описах
# Toysi: "опт" 630/630 false positive ("оптимальний"/"Оптимальні
# розміри"), "менеджер" 6/6 false positive (настільна гра "Менеджер"),
# "телефон" 231 здебільшого продукт-ознака, "акці"/"знижк" false
# positive (ігрові фішки-акції компаній у "Монополії", гарантійний
# пункт про компенсацію/знижку на дефект) — сліпий словниковий фільтр
# зіпсував би сотні легітимних описів. Лише точні фрази, які фізично
# не можуть бути частиною опису товару.
_CTA_PHRASES = [
    "менеджер передзвонить", "менеджер зв'яжеться", "менеджер зв'яжется",
    "зателефонуйте нам", "зателефонуйте за номером", "телефонуйте нам",
    "звертайтесь за номером", "звертайтеся за номером",
    "пишіть в директ", "пишіть в особисті", "пишіть в приват",
    "замовляйте прямо зараз", "замовляйте зараз", "успійте купити",
    "тільки сьогодні знижка", "діє акція", "встигніть придбати",
    "звертайтесь до менеджера", "звертайтеся до менеджера",
]


def _strip_cta_phrases(text: str) -> str:
    low = text.lower()
    if not any(phrase in low for phrase in _CTA_PHRASES):
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = [s for s in sentences if not any(p in s.lower() for p in _CTA_PHRASES)]
    return " ".join(kept)


def _sanitize_eva_description(text: str) -> str:
    return _strip_cta_phrases(_strip_contacts_and_price(text))


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    last_space = cut.rfind(" ")
    if last_space > max_len * 0.6:
        cut = cut[:last_space]
    return cut.rstrip(" ,.-")


def _qualifies_for_feed(item: dict, excluded: set, prom_price_overrides: dict) -> bool:
    """Дзеркало основного циклу _build_xml() нижче — винесено окремо для
    підрахунку дублікатів <name_ua> ЛИШЕ серед товарів, що реально
    потраплять у фід (той самий принцип, що й Rozetka).

    ЦІНА ЕВА = ЦІНА PROM (2026-07-23, пряме рішення власниці): EVA більше
    не рахує свою ціну незалежно через decide_price_for_platform() —
    товар без свіжого prom_price_overrides запису (ще не пройшов через
    репрайсер Prom цього ж циклу update-feeds.yml) чи з ціною Prom, що не
    проходить floor рентабельності EVA (реальна комісія EVA, real_toysi_cost),
    просто не потрапляє у фід — див. дзеркальну логіку в _build_xml()."""
    try:
        cost = real_toysi_cost(item)
    except (ValueError, TypeError):
        return False
    if cost <= 0 or cost < MIN_SUPPLIER_PRICE:
        return False
    item_id = str(item["id"])
    if item_id in excluded:
        return False
    prom_price = prom_price_overrides.get(item_id)
    if prom_price is None:
        return False
    eva_floor = compute_floor(cost, get_platform_commission("eva"), MIN_PROFIT)
    if prom_price < eva_floor:
        return False
    vendor = (item.get("vendor") or "").strip()
    if not vendor:
        return False
    if _normalize_brand(vendor) in EVA_STOP_BRANDS:
        return False
    if _is_banned_country(item.get("country")):
        return False
    pictures = [p for p in item.get("pictures", []) if p.startswith("https://")][:EVA_MAX_PICTURES]
    if not pictures:
        return False
    return True


def _wrap_cdata(xml_str: str) -> str:
    def replacer(m):
        content = html.unescape(m.group(1))
        content = content.replace("]]>", "]]]]><![CDATA[>")
        return f"<description_ua><![CDATA[{content}]]></description_ua>"
    return re.sub(r"<description_ua>(.*?)</description_ua>", replacer, xml_str, flags=re.DOTALL)


def _build_xml(
    catalog: dict,
    price_overrides: dict = None,
    exclude_ids: set = None,
    description_overrides: dict = None,
    russian_text: dict = None,
) -> ET.Element:
    now  = datetime.now().strftime("%Y-%m-%d %H:%M")
    yml  = ET.Element("yml_catalog", date=now)
    shop = ET.SubElement(yml, "shop")
    ET.SubElement(shop, "name").text    = SHOP_NAME
    ET.SubElement(shop, "company").text = SHOP_COMPANY
    ET.SubElement(shop, "url").text     = SHOP_URL

    currencies = ET.SubElement(shop, "currencies")
    ET.SubElement(currencies, "currency", id="UAH", rate="1")

    cat_map: dict = {}
    for item in catalog.values():
        cid   = (item.get("category_id") or "").strip()
        cname = (item.get("category_name") or "").strip()
        if cid and cid not in cat_map:
            cat_map[cid] = cname or cid

    categories_el = ET.SubElement(shop, "categories")
    for cid in sorted(cat_map):
        ET.SubElement(categories_el, "category", id=cid).text = _clean_text(cat_map[cid])

    offers_el      = ET.SubElement(shop, "offers")
    overrides      = price_overrides or {}
    excluded       = exclude_ids or set()
    desc_overrides = description_overrides or {}
    russian        = russian_text or {}
    described_count = 0
    russian_missing_count = 0

    name_counts = Counter(
        _dedup_key(_normalize_trailing_color_case(_limit_punctuation(_denoise_caps(_clean_text(item.get("name", ""))))))
        for item in catalog.values()
        if _qualifies_for_feed(item, excluded, overrides)
    )

    skipped_no_price      = 0
    skipped_cheap         = 0
    skipped_unprof        = 0
    skipped_no_prom_price = 0
    skipped_no_vendor     = 0
    skipped_stop_brand    = 0
    skipped_banned_country = 0
    skipped_no_pics       = 0
    skipped_short_desc    = 0
    truncated_name_count  = 0
    truncated_name_ru_count = 0

    for item in catalog.values():
        try:
            cost = real_toysi_cost(item)
        except (ValueError, TypeError):
            skipped_no_price += 1
            continue
        if cost <= 0:
            skipped_no_price += 1
            continue
        if cost < MIN_SUPPLIER_PRICE:
            skipped_cheap += 1
            continue

        item_id = str(item["id"])
        if item_id in excluded:
            skipped_unprof += 1
            continue

        # ЦІНА EVA = ЦІНА PROM (2026-07-23, пряме рішення власниці): EVA
        # більше НЕ рахує ціну незалежно (decide_price_for_platform) —
        # копіює свіжу ціну, яку репрайсер щойно застосував на Prom у
        # ТОМУ Ж прогоні update-feeds.yml (load_fresh_prom_price_overrides,
        # <=PROM_PRICE_STATE_MAX_AGE_HOURS годин). Товар без свіжого запису
        # (ще не торкнутий репрайсером) просто не потрапляє в фід EVA —
        # немає окремої "ціни EVA" без Prom як джерела істини.
        retail = overrides.get(item_id)
        if retail is None:
            skipped_no_prom_price += 1
            continue

        # Профіт-запобіжник: якщо ціна Prom під РЕАЛЬНОЮ комісією EVA
        # (get_platform_commission("eva"), 15%) з реальною собівартістю
        # (real_toysi_cost) не проходить той самий floor рентабельності,
        # що й Prom "без конкурента" (MIN_PROFIT=25%) —
        # виключаємо з фіда EVA, а не публікуємо збитковим/на межі.
        eva_floor = compute_floor(cost, get_platform_commission("eva"), MIN_PROFIT)
        if retail < eva_floor:
            skipped_unprof += 1
            continue

        vendor = (item.get("vendor") or "").strip()
        if not vendor:
            skipped_no_vendor += 1
            continue

        if _normalize_brand(vendor) in EVA_STOP_BRANDS:
            skipped_stop_brand += 1
            continue

        if _is_banned_country(item.get("country")):
            skipped_banned_country += 1
            continue

        pictures = [
            p for p in item.get("pictures", [])
            if p.startswith("https://")
        ][:EVA_MAX_PICTURES]
        if not pictures:
            skipped_no_pics += 1
            continue

        stock     = item.get("stock", 0)
        available = "true" if stock > 0 else "false"

        offer = ET.SubElement(offers_el, "offer", id=item_id, available=available)

        name = _normalize_trailing_color_case(_limit_punctuation(_denoise_caps(_clean_text(item.get("name", "")))))
        if name_counts.get(_dedup_key(name), 0) > 1:
            color_val = None
            for param_name, param_val in item.get("params", []):
                if "колір" in param_name.lower() or "цвет" in param_name.lower():
                    color_val = str(param_val).strip()
                    break
            # color_val — сире значення параметра Toysi, може бути
            # написане ВЕЛИКИМИ ЛІТЕРАМИ (живо знайдено: "(БІЛИЙ)",
            # "(ПОМАРАНЧЕВИЙ)") — _denoise_caps() тут НЕ підходить (поріг
            # >=6 літер розрахований на повні назви, "білий"/"білий" — 5
            # букв, ніколи не спрацював би), тому окреме, просте правило:
            # single-слово colir_val, повністю великими літерами -> Title
            # Case (тут ризику зіпсувати акронім/модель немає — це відоме
            # значення параметра "Колір", не довільний текст назви).
            if color_val and color_val.isupper():
                disambiguator = color_val.capitalize()
            else:
                disambiguator = color_val or item_id
            suffix = f" ({disambiguator})"
            if len(name) + len(suffix) > EVA_NAME_MAX_LEN:
                truncated_name_count += 1
            name = _truncate(name, EVA_NAME_MAX_LEN - len(suffix)) + suffix
        elif len(name) > EVA_NAME_MAX_LEN:
            truncated_name_count += 1
            name = _truncate(name, EVA_NAME_MAX_LEN)
        ET.SubElement(offer, "name_ua").text = name

        # <name> (рос.) — ВИМОГА EVA "лише російською заборонена" мала на
        # увазі, що поле НЕ МОЖЕ бути відсутнім/лише-укр.: раніше цей тег
        # взагалі не писався (див. докстрінг файлу). fetch_russian_text()
        # — той самий rus-фід Toysi (lang=rus), що вже використовує Prom;
        # м'який фолбек на укр. назву, якщо для SKU рос-варіанту нема
        # (рідкість — 2/29386 у повному каталозі, перевірено раніше для Prom).
        name_ru_raw = (russian.get(item_id) or {}).get("name") or item.get("name", "")
        if item_id not in russian:
            russian_missing_count += 1
        name_ru = _normalize_trailing_color_case(_limit_punctuation(_denoise_caps(_clean_text(name_ru_raw))))
        if len(name_ru) > EVA_NAME_MAX_LEN:
            truncated_name_ru_count += 1
            name_ru = _truncate(name_ru, EVA_NAME_MAX_LEN)
        ET.SubElement(offer, "name").text = name_ru

        ET.SubElement(offer, "price").text          = f"{retail:.2f}"
        ET.SubElement(offer, "currencyId").text     = "UAH"
        ET.SubElement(offer, "stock_quantity").text = str(stock)

        if item.get("category_id"):
            ET.SubElement(offer, "categoryId").text = item["category_id"]

        for pic_url in pictures:
            ET.SubElement(offer, "picture").text = pic_url

        ET.SubElement(offer, "vendor").text = _clean_text(normalize_vendor(vendor))

        desc_override = desc_overrides.get(item_id)
        raw_description = item.get("description", "")
        country = item.get("country")
        if desc_override:
            described_count += 1
            raw_description = desc_override.get("description") or raw_description
            country = desc_override.get("country") or country

        if country:
            ET.SubElement(offer, "country_of_origin").text = _clean_text(country)

        if item.get("barcode"):
            ET.SubElement(offer, "barcode").text = _clean_text(item["barcode"])

        desc = append_clearance_notice(
            raw_description,
            item.get("name", ""),
            item.get("category_name", ""),
            item.get("category_id", ""),
        )
        desc = _strip_urls(desc)
        desc = _sanitize_eva_description(desc)
        desc = _truncate(_clean_text(desc), EVA_DESCRIPTION_MAX_LEN)
        if desc and len(desc) < EVA_DESCRIPTION_MIN_LEN:
            skipped_short_desc += 1  # лише лічильник — НЕ виключаємо offer, документована, не підтверджена вимога
        if desc:
            ET.SubElement(offer, "description_ua").text = desc

        # Параметри, п.4 (докстрінг файлу) — EVA вимагає МІНІМУМ 2
        # <param>. Toysi реально дає 0 чи 1 характеристику для частини
        # SKU (старий код гарантував лише 1 fallback "Виробник"). Пул
        # fallback-параметрів топує до 2, уникаючи дублювання назви,
        # яку Toysi вже надав.
        params = item.get("params", [])
        existing_param_names = {(pn or "").strip().lower() for pn, _ in params}
        for param_name, param_val in params:
            ET.SubElement(offer, "param", name=_clean_text(param_name)).text = _clean_text(str(param_val))
        written_params = len(params)
        for fallback_name, fallback_val in (
            ("Виробник", vendor),
            ("Категорія", item.get("category_name") or "Дитячі товари"),
        ):
            if written_params >= 2:
                break
            if fallback_name.lower() in existing_param_names:
                continue
            ET.SubElement(offer, "param", name=fallback_name).text = _clean_text(str(fallback_val))
            existing_param_names.add(fallback_name.lower())
            written_params += 1

    print(f"[EVA] У фіді: {len(offers_el)} товарів | "
          f"без ціни: {skipped_no_price} | дешевше {MIN_SUPPLIER_PRICE} грн: {skipped_cheap} | "
          f"немає свіжої ціни Prom (ще не торкнуто репрайсером цього циклу): {skipped_no_prom_price} | "
          f"виключено вручну/нерентабельно під комісією EVA: {skipped_unprof} | без бренду (vendor обов'язковий): {skipped_no_vendor} | "
          f"бренд/студія у стоп-листі EVA: {skipped_stop_brand} | заборонена країна походження: {skipped_banned_country} | "
          f"без валідного фото: {skipped_no_pics} | назв обрізано (>{EVA_NAME_MAX_LEN} симв.): укр={truncated_name_count}, рос={truncated_name_ru_count}")
    if skipped_short_desc:
        print(f"[EVA] УВАГА: {skipped_short_desc} offer(и) мають опис коротший за задокументований мінімум EVA "
              f"({EVA_DESCRIPTION_MIN_LEN} симв.) — НЕ виключено з фіда, ризик відхилення при модерації EVA.")
    print(f"[EVA] Vis-9: {described_count} SKU отримали вручну написаний опис (description_overrides.json)")
    print(f"[EVA] Рос. назва (<name>): {russian_missing_count} SKU без rus-варіанту Toysi, використано фолбек на укр. назву")
    return yml


def generate_feed(output_file: str = OUTPUT_FILE,
                  price_overrides: dict = None,
                  catalog: dict = None,
                  exclude_ids: set = None,
                  description_overrides: dict = None,
                  russian_text: dict = None) -> None:
    if catalog is None:
        print("[EVA] Завантажуємо каталог Toysi...")
        catalog = fetch_toysi_catalog()
    if not catalog:
        print("[EVA] Каталог порожній — файл не створено.")
        return

    top_catalog = select_top_items(catalog)
    print(f"[EVA] Куруваний відбір: {len(top_catalog)} з {len(catalog)} товарів повного каталогу.")

    if russian_text is None:
        russian_text = fetch_russian_text()

    root = _build_xml(
        top_catalog, price_overrides=price_overrides, exclude_ids=exclude_ids,
        description_overrides=description_overrides, russian_text=russian_text,
    )

    ET.indent(root, space="  ")
    xml_str = ET.tostring(root, encoding="unicode")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str
    xml_str = _wrap_cdata(xml_str)

    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(xml_str)

    print(f"[EVA] Готово! Збережено: {output_file}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    description_overrides = load_description_overrides()
    generate_feed(description_overrides=description_overrides, price_overrides=load_fresh_prom_price_overrides())
