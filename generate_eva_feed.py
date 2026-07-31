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

РОЗМОРОЖЕНО 2026-07-30 (пряме рішення власниці — товар пройшов модерацію
EVA): EVA тепер ведеться ЯК Prom. generate_feed() рахує відбір ЩОПРОГОНУ
через _build_eva_live_selection() на ЖИВОМУ каталозі Toysi і ЖИВИХ цінах
Prom — наявність/склад (available/stock_quantity), ціна й сам склад списку
актуальні, а НЕ з замороженого знімка. Це прямо усуває ризик «проданий/
відсутній товар лишається available», який давала заморозка (stock фіксувався
на момент знімка).

ІСТОРІЯ — ЗАМОРОЗКА НА МОДЕРАЦІЮ (2026-07-29 … завершено 2026-07-30): на
період модерації EVA застосовувався той самий патерн заморозки, що й Rozetka
(_build_eva_static_selection() → eva_static_selection.json, round-trip через
feed-data), щоб каталог не змінювався під час розгляду. Функцію
_build_eva_static_selection() ЗБЕРЕЖЕНО (більше не викликається) — щоб швидко
ПОВЕРНУТИ заморозку за потреби: перемкнути виклик у generate_feed() назад на
неї + відновити eva_static_selection round-trip у run_/publish_feed_pipeline_vps.sh.
"""
import json
import os
import re
import html
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path

from competitor_pricing import (
    load_description_overrides, real_toysi_cost,
)
from generate_prom_feed import append_clearance_notice, normalize_vendor
from parser import fetch_toysi_catalog

SHOP_NAME          = "PlutusToys"
SHOP_COMPANY       = "ФОП Чечетенко Олександр Юрійович"
SHOP_URL           = "https://plutustoys.com.ua"
OUTPUT_FILE        = "feeds/eva_feed.xml"
MIN_SUPPLIER_PRICE = 20  # той самий поріг, що й Prom/Rozetka — товари дешевше собівартості постачальника пропускаємо

# Заморозка на модерацію EVA (2026-07-29, пряме рішення власниці, той
# самий патерн, що й ROZETKA_STATIC_SELECTION_FILE) — див. докстрінг файлу.
EVA_STATIC_SELECTION_FILE = Path(__file__).parent / "eva_static_selection.json"

# Порядок відбору EVA (2026-07-30, пряме рішення власника): у фід беремо лише товари з
# наявністю СТРОГО більше EVA_SELECTION_MIN_STOCK шт (ostatok Toysi — діапазон, парсер
# бере мінімум; менший ризик оверселу), впорядковані найновіші-першими за тегом <date>
# Toysi (дата надходження новинки; порожня = не новинка). Крок "топ Тойсі" (пріоритетний)
# у фіді Toysi ВІДСУТНІЙ (лише в кабінеті) — стане першим ключем сортування, щойно з'явиться
# машинне джерело топа (Toysi-API/ручний експорт). Наразі порядок: наявність>2 → найновіші.
EVA_SELECTION_MIN_STOCK = 2

# EVA РОЗВ'ЯЗАНА З PROM (2026-07-30, пряме рішення власника, дослівно: «я відміняю
# спайку фіда єви з промом ... вартість = вартість тойсі з нашою знижкою × 1,45.
# поки така формула, товар повинен бути в наявності, кількість у фіді поки 2000»):
# ціна EVA — ПРЯМА ФІКСОВАНА НАЦІНКА real_toysi_cost × EVA_PRICE_MULTIPLIER, без
# жодного розрахунку/даних Prom (без decide_price_for_platform, без compute_floor,
# без get_platform_commission). У фід беруться ВСІ валідні товари в наявності,
# впорядковані найновіші-першими (<date> Toysi), обрізані до EVA_TARGET_SIZE.
#
# ТИМЧАСОВА формула («поки така» — власник) — легко замінити, коли дасть постійну.
EVA_PRICE_MULTIPLIER = 1.45
# Ціль фіда: УСІ валідні товари (пряме рішення власника 2026-07-31 «піднімай», після
# підтвердження виміром — генерація 8879 товарів ~54с, файл ~20МБ; EVA не обмежує
# кількість, ліміт файла 300МБ не досягається). 15000 — запобіжник за розміром файла,
# свідомо ВИЩИЙ за максимально можливий валідний пул при stock>2 (~11 098 з живого
# розподілу залишків), тож на практиці НЕ ріже — усі валідні потрапляють у фід, а кеп
# лишається backstop'ом далеко під лімітом EVA (15000×~2.2КБ ≈ 33МБ « 300МБ). Раніше
# було 2000 («поки 2000», знято тим самим рішенням).
EVA_TARGET_SIZE = 15000

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
    короткі акроніми/моделі на кшталт "USB"/"M-9", які природно займають
    малу частку довгої назви), перетворює слова на Title Case. Короткі
    токени (<=3 симв. core, окрім службових слів вище) і токени з
    цифрами (моделі "M-9", розміри "3D") лишаються недоторканими.
    ВИПРАВЛЕНО (аудит, pt15): попередній приклад "LEGO" тут був
    НЕТОЧНИМ — "LEGO" (4 літери, без цифр) НЕ підпадає під захист
    "<=3 символи" і перетворюється на "Lego" (перевірено незалежно) —
    це не пошкодження даних (читабельна, коректно написана назва бренду),
    але коментар раніше стверджував протилежне. Живо перевірено на 669
    реально "кричущих" назвах живого каталогу Toysi —
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
# ВИПРАВЛЕНО (аудит, pt15): без межі проти сусідніх цифр regex матчив
# БУДЬ-ЯКУ 10-значну підпослідовність УСЕРЕДИНІ довшого числового рядка
# — живо відтворено: "Штрихкод 4820172542016" -> "Штрихкод 482",
# "Артикул 0123456789012" -> "Артикул012". (?<!\d)/(?!\d) навколо
# патерну гарантують, що збіг НЕ є частиною довшої цифрової послідовності
# (штрихкод/артикул), лишаючи реальні окремі номери телефонів незайманими.
_PHONE_RE = re.compile(r"(?<!\d)(\+?38)?\s*\(?0\d{2}\)?[\s.\-]?\d{3}[\s.\-]?\d{2}[\s.\-]?\d{2}(?!\d)")
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


# Прибирає АТРИБУТИ з HTML-тегів, лишаючи самі теги: <p data-start="8"
# class="PDq2pG_selectionAnchorContainer"> -> <p>; <br data-start=...> -> <br>;
# <span aria-hidden="true" class="..."> -> <span>; </p> -> </p>; <b> -> <b>.
_HTML_TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)(?:\s[^>]*?)?(/?)\s*>")


def _strip_html_attributes(text: str) -> str:
    """Описи Toysi/Vis-9 містять сміттєві атрибути веб-редактора (data-start,
    data-end, data-section-id, class="PDq2pG_...", aria-hidden) — EVA це не
    потрібно, засмічує картку й підвищує ризик модерації. Лишаємо структуру
    тегів (<p>/<br>/<b>/<ul>/<li>/<strong>), прибираємо всі атрибути."""
    return _HTML_TAG_RE.sub(lambda m: f"<{m.group(1)}{m.group(2)}{m.group(3)}>", text)


def _sanitize_eva_description(text: str) -> str:
    return _strip_cta_phrases(_strip_contacts_and_price(_strip_html_attributes(text)))


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    last_space = cut.rfind(" ")
    if last_space > max_len * 0.6:
        cut = cut[:last_space]
    return cut.rstrip(" ,.-")


def _final_eva_description(item: dict, desc_override: dict = None) -> str:
    """Фінальний санітизований опис EVA — рівно той рядок, що піде в <description_ua>
    (append_clearance_notice → _strip_urls → _sanitize_eva_description → _clean_text →
    _truncate). ЄДИНЕ джерело правди і для відбору (строгий гейт мін. довжини EVA), і для
    _build_xml — щоб довжина, за якою відбираємо, точно збігалася з тією, що публікується."""
    raw = item.get("description", "")
    if desc_override:
        raw = desc_override.get("description") or raw
    desc = append_clearance_notice(
        raw,
        item.get("name", ""),
        item.get("category_name", ""),
        item.get("category_id", ""),
    )
    desc = _strip_urls(desc)
    desc = _sanitize_eva_description(desc)
    desc = _truncate(_clean_text(desc), EVA_DESCRIPTION_MAX_LEN)
    return desc


def _qualifies_for_feed(item: dict, excluded: set = None, prom_price_overrides: dict = None) -> bool:
    """Чи товар ВАЛІДНИЙ для EVA-фіда — БЕЗ вимоги ціни (роздрібну ціну рахує
    окремо _eva_price() власною формулою EVA). Дзеркало валідаційної частини
    _build_xml() нижче — винесено для підрахунку дублікатів <name_ua> ЛИШЕ
    серед товарів, що реально потраплять у фід (той самий принцип, що й Rozetka).

    EVA РОЗВ'ЯЗАНА З PROM (2026-07-31, пряме рішення власника «EVA більше ніяк
    не пов'язана з Prom, є формула»): вимога наявності свіжого prom_price_overrides
    запису ПРИБРАНА — беремо будь-який валідний товар незалежно від того, чи
    торкнувся його репрайсер Prom, бо ціну EVA тепер рахуємо власною формулою
    (_eva_price), а не копіюємо з Prom. Параметр prom_price_overrides збережено
    в сигнатурі лише для сумісності зі старим замороженим (більше не викликаним)
    _build_eva_static_selection() — тут ІГНОРУЄТЬСЯ.

    Валідність: реальна собівартість >= MIN_SUPPLIER_PRICE, є vendor, не стоп-бренд
    EVA, не заборонена країна походження, є хоч одне https-фото."""
    excluded = excluded or set()
    try:
        cost = real_toysi_cost(item)
    except (ValueError, TypeError):
        return False
    if cost <= 0 or cost < MIN_SUPPLIER_PRICE:
        return False
    if str(item["id"]) in excluded:
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


def _eva_price(item: dict):
    """Роздрібна ціна EVA = real_toysi_cost(item) × EVA_PRICE_MULTIPLIER (1.45),
    округлено до копійки. ТИМЧАСОВА формула — пряма фіксована націнка (пряме
    рішення власника 2026-07-30, дослівно «вартість тойсі з нашою знижкою × 1,45.
    поки така формула»). real_toysi_cost вже враховує нашу знижку Toysi + збірку.
    БЕЗ конкурентного розрахунку, БЕЗ compute_floor / get_platform_commission /
    decide_price_for_platform — жодної залежності від Prom. Повертає None, якщо
    собівартість невалідна (тоді товар не потрапляє у фід — не публікуємо без ціни).
    Профіт безпечний і без floor: 1.45 × (1 − 0.15 комісія EVA) = 1.2325 → +23% нетто
    навіть на найвищій комісії, тож збиток неможливий (floor свідомо не потрібен)."""
    try:
        cost = real_toysi_cost(item)
    except (ValueError, TypeError):
        return None
    if not cost or cost < MIN_SUPPLIER_PRICE:
        return None
    return round(cost * EVA_PRICE_MULTIPLIER, 2)


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
    described_count = 0

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

        # ЦІНА EVA — ПРЯМА ФІКСОВАНА НАЦІНКА ×1.45 (2026-07-30, рішення власника):
        # price_overrides тут — це вже пораховані _eva_price() ціни (real_toysi_cost
        # × EVA_PRICE_MULTIPLIER), передані з _build_eva_live_selection(). Кожен
        # відібраний SKU має ціну; item без запису сюди не доходить (відсіяний ще у
        # відборі) — цей guard лишається як страхувальний від None.
        # СВІДОМО НЕМАЄ floor-запобіжника (compute_floor/get_platform_commission):
        # власник задав пряму націнку без floor, а ×1.45 і так завжди прибуткова
        # (нетто +23% навіть на 15% комісії). Floor тут ХИБНО відкинув би ВЕСЬ фід —
        # cost×1.45 < compute_floor(cost,0.15,0.25)=cost×1.47.
        retail = overrides.get(item_id)
        if retail is None:
            skipped_no_prom_price += 1
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

        # <name> (рос.) СВІДОМО НЕ емітиться (2026-07-31): авто-модерація EVA
        # відхиляла ВСІ товари з «Виявлено російську мову у невідповідному полі» —
        # російське поле <name> було тригером (живо знайдено в кабінеті продавця:
        # 0 активних, усі у «Відхилені»). За специфікацією EVA (sellersupport
        # pidhotovka-prays-listu-xml) <name> ОПЦІЙНЕ (внутрішнє, не показується),
        # тож прибираємо його зовсім — лишається лише обов'язкове укр. <name_ua>.
        # Раніше <name> заповнювався рос-фідом Toysi — це було помилкою (EVA не
        # ВИМАГАЄ рос. назву, а НАВПАКИ ловить російську авто-модерацією).

        ET.SubElement(offer, "price").text          = f"{retail:.2f}"
        ET.SubElement(offer, "currencyId").text     = "UAH"
        ET.SubElement(offer, "stock_quantity").text = str(stock)

        if item.get("category_id"):
            ET.SubElement(offer, "categoryId").text = item["category_id"]

        for pic_url in pictures:
            ET.SubElement(offer, "picture").text = pic_url

        ET.SubElement(offer, "vendor").text = _clean_text(normalize_vendor(vendor))

        desc_override = desc_overrides.get(item_id)
        country = item.get("country")
        if desc_override:
            described_count += 1
            country = desc_override.get("country") or country

        if country:
            ET.SubElement(offer, "country_of_origin").text = _clean_text(country)

        if item.get("barcode"):
            ET.SubElement(offer, "barcode").text = _clean_text(item["barcode"])

        # Опис — через спільний _final_eva_description() (той самий рядок, за яким
        # відбір строго гейтить довжину). Виключення короткоописних робить ВІДБІР
        # (_build_eva_live_selection) з backfill'ом — тут лишається лічильник
        # спостережності (за строгим відбором має друкувати 0).
        desc = _final_eva_description(item, desc_override)
        if desc and len(desc) < EVA_DESCRIPTION_MIN_LEN:
            skipped_short_desc += 1
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
          f"без порахованої ціни EVA (страхувальник, має бути 0): {skipped_no_prom_price} | "
          f"виключено вручну (exclude_ids): {skipped_unprof} | без бренду (vendor обов'язковий): {skipped_no_vendor} | "
          f"бренд/студія у стоп-листі EVA: {skipped_stop_brand} | заборонена країна походження: {skipped_banned_country} | "
          f"без валідного фото: {skipped_no_pics} | назв обрізано (>{EVA_NAME_MAX_LEN} симв.): укр={truncated_name_count}")
    if skipped_short_desc:
        print(f"[EVA] ІНВАРІАНТ ПОРУШЕНО: {skipped_short_desc} offer(и) з описом коротшим за мінімум EVA "
              f"({EVA_DESCRIPTION_MIN_LEN} симв.) дійшли до _build_xml, хоча відбір мав їх відсіяти "
              "(строгий гейт _build_eva_live_selection розсинхронізований із _final_eva_description?).")
    print(f"[EVA] Vis-9: {described_count} SKU отримали вручну написаний опис (description_overrides.json)")
    return yml


def _build_eva_live_selection(catalog: dict, russian_text: dict = None,
                              description_overrides: dict = None) -> tuple[dict, dict, dict]:
    """Живий (динамічний) відбір EVA — рахується ЩОПРОГОНУ на ЖИВОМУ каталозі Toysi.
    Використовується generate_feed() після РОЗМОРОЗКИ 2026-07-30 (товар пройшов
    модерацію EVA → вести EVA як Prom, живі залишки замість замороженого знімка).

    EVA РОЗВ'ЯЗАНА З PROM (2026-07-31, пряме рішення власника «EVA більше ніяк не
    пов'язана з Prom, є формула; 2000 усіх, категорії всі валідні»): відбір більше
    НЕ обмежений prom-топом (select_top_items) і НЕ вимагає prom-ціни — беремо ВЕСЬ
    каталог Toysi, лишаємо валідні (_qualifies_for_feed) товари в наявності
    > EVA_SELECTION_MIN_STOCK, рахуємо роздрібну ціну ВЛАСНОЮ формулою EVA
    (_eva_price), впорядковуємо найновіші-першими за <date> Toysi й обрізаємо до
    EVA_TARGET_SIZE. Повертає (items, prices, russian_names)."""
    russian = russian_text or {}
    desc_overrides = description_overrides or {}

    # Кандидати: увесь каталог, наявність > EVA_SELECTION_MIN_STOCK, валідні,
    # з порахованою власною ціною EVA (без ціни — не публікуємо), і — СТРОГО за
    # вимогою EVA — з повноцінним описом (фінальний санітизований опис не коротший
    # за EVA_DESCRIPTION_MIN_LEN). Оскільки каталог надлишковий (валідних набагато
    # більше за ціль), короткоописні відсіюються, а місце добирається рештою пулу
    # (backfill) — фід лишається повним, але лише з товарів, що проходять модерацію EVA.
    candidates = []
    skipped_stock = skipped_invalid = skipped_no_price = skipped_short_desc = 0
    for pid, item in catalog.items():
        if item.get("stock", 0) <= EVA_SELECTION_MIN_STOCK:
            skipped_stock += 1
            continue
        if not _qualifies_for_feed(item):
            skipped_invalid += 1
            continue
        price = _eva_price(item)
        if price is None:
            skipped_no_price += 1
            continue
        if len(_final_eva_description(item, desc_overrides.get(pid))) < EVA_DESCRIPTION_MIN_LEN:
            skipped_short_desc += 1
            continue
        candidates.append((pid, item, price))

    # Порядок (2026-07-30/31, пряме рішення власника): найновіші першими —
    # непорожня <date> desc; тай-брейк — новіший id (Toysi нумерує послідовно).
    # (Крок "топ Тойсі" стане першим ключем, щойно з'явиться машинне джерело топа.)
    candidates.sort(
        key=lambda c: (c[1].get("date") or "", int(c[0]) if str(c[0]).isdigit() else -1),
        reverse=True,
    )

    items: dict = {}
    prices: dict = {}
    russian_names: dict = {}
    for pid, item, price in candidates[:EVA_TARGET_SIZE]:
        items[pid] = item
        prices[pid] = price
        russian_names[pid] = (russian.get(pid) or {}).get("name") or item.get("name", "")

    print(f"[EVA] Відбір (розв'язаний з Prom, власна формула): {len(candidates)} валідних "
          f"кандидатів у наявності>{EVA_SELECTION_MIN_STOCK} (відсіяно: склад={skipped_stock}, "
          f"невалідні={skipped_invalid}, без ціни={skipped_no_price}, "
          f"короткий опис (<{EVA_DESCRIPTION_MIN_LEN} симв., строго за EVA)={skipped_short_desc}); "
          f"у фід {len(items)} (ціль {EVA_TARGET_SIZE}, найновіші першими).")
    return items, prices, russian_names


def _build_eva_static_selection(catalog: dict, russian_text: dict = None,
                                description_overrides: dict = None) -> tuple[dict, dict, dict]:
    """Заморожений знімок EVA-фіда (rollback-шлях, той самий патерн, що й
    Rozetka _build_rozetka_static_selection()). Повертає (items, prices,
    russian_names): сирий каталог-товар / ціна / рос. назва НА МОМЕНТ заморозки —
    надалі НЕ перераховуються, доки файл не видалено.

    ВІДБІР І ЦІНА (2026-07-31, після розв'язки EVA↔Prom): заморожується РЕЗУЛЬТАТ
    того самого живого відбору _build_eva_live_selection() — власна формула ціни EVA,
    повний каталог, наявність>2, строгий гейт опису, найновіші-першими, cap 2000.
    РАНІШЕ заморожувався select_top_items()+ціна Prom; після розв'язки той шлях
    прибрано (він і спирався на гарантію prom-ціни, якої більше нема — inline-
    freeze дав би KeyError). Тепер re-freeze дає рівно те, що й живий фід.

    Перший виклик (файл ще не існує) — рахує через живий відбір і зберігає. Кожен
    наступний — читає збережене й повертає БЕЗ перерахунку."""
    if EVA_STATIC_SELECTION_FILE.exists():
        try:
            saved = json.loads(EVA_STATIC_SELECTION_FILE.read_text(encoding="utf-8"))
            return saved["items"], saved["prices"], saved.get("russian_names", {})
        except (ValueError, OSError, KeyError):
            pass  # пошкоджений/неповний файл — сформувати заново нижче, як при першому запуску

    items, prices, russian_names = _build_eva_live_selection(
        catalog, russian_text=russian_text, description_overrides=description_overrides,
    )

    EVA_STATIC_SELECTION_FILE.write_text(
        json.dumps({"items": items, "prices": prices, "russian_names": russian_names,
                   "built_at": datetime.now().isoformat()},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"[EVA] Статичний список сформовано ВПЕРШЕ (заморозка): {len(items)} товарів "
          f"(з {len(catalog)} повного каталогу Toysi, живий відбір заморожено). Список "
          "ЗАМОРОЖЕНИЙ — наступні прогони використовуватимуть той самий, без перерахунку "
          "(ні ціна, ні наявність, ні сам факт присутності), доки файл не видалено.")
    return items, prices, russian_names


def generate_feed(output_file: str = OUTPUT_FILE,
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

    # rus-фід Toysi (fetch_russian_text) БІЛЬШЕ НЕ потрібен: EVA-фід більше не
    # емітить російське <name> (2026-07-31 — авто-модерація EVA його відхиляла).
    # Прибрано зайвий ~70МБ/~17с фетч rus-каталогу щопрогону.

    # РОЗМОРОЖЕНО 2026-07-30 (пряме рішення власниці — товар пройшов модерацію EVA →
    # вести EVA як Prom: живі залишки/ціни). Відбір рахується ЩОПРОГОНУ через
    # _build_eva_live_selection() (динамічно), а НЕ з замороженого знімка (static_* —
    # історична назва змінних, тепер тримають ЖИВІ дані). Щоб ПОВЕРНУТИ заморозку —
    # замінити виклик назад на _build_eva_static_selection() (функція збережена вище) +
    # відновити eva_static_selection round-trip у run_/publish_feed_pipeline_vps.sh.
    static_items, static_prices, _static_russian_names = _build_eva_live_selection(
        catalog, description_overrides=description_overrides,
    )
    print(f"[EVA] Живий відбір (динамічно, живі залишки/ціни): {len(static_items)} товарів.")

    root = _build_xml(
        static_items, price_overrides=static_prices, exclude_ids=exclude_ids,
        description_overrides=description_overrides,
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
    generate_feed(description_overrides=description_overrides)
