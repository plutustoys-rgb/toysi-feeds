"""
competitor_pricing.py — конвеєр порівняння цін з конкурентами на Rozetka.

ВАЖЛИВО ПРО АРХІТЕКТУРУ: Rozetka віддає порожню HTML-заглушку на запити через
requests/urllib (перевірено — сайт розрізняє реальний браузер і бот за
фінгерпринтом, __NEXT_DATA__/ціни в сирому HTML відсутні). Тому автоматичний
пошук конкурентів БЕЗ браузера (як намагався robots.py) не працює.

Цей скрипт НЕ вміє сам відкривати браузер — він лише:
  1) обирає чергу товарів для перевірки (--next-batch),
  2) записує знайдений вручну/через claude-in-chrome результат (--record),
  3) веде checkpoint денного ліміту та pricing_results.csv.

Реальний пошук на Rozetka виконує оператор (Claude Code сесія) через
claude-in-chrome, по одному товару за раз, і викликає --record з результатом.

Логіка рішення (2026-07-09, окремо для кожного майданчика — Prom і Rozetka):
    нижня_межа = (собівартість + собівартість*цільова_маржа) / (1 - комісія_майданчика - комісія_оплати)
    кандидат   = ціна_конкурента - PRICE_STEP (фіксований крок, 1 грн — НЕ відсоток)
    ціна       = max(нижня_межа, кандидат)
    якщо конкурента немає -> ціна = max(нижня_межа, собівартість*NO_COMPETITOR_MULT)

    Комісія майданчика:
    - Prom: категорійна (12-23%+, з кабінету Prom -> Налаштування -> Комісії
      за категоріями) — PROM_CATEGORY_COMMISSION, з fallback-дефолтом, поки
      не всі категорії уточнено.
    - Rozetka: перенесено як орієнтовний дефолт (22%) з попередньої версії
      цієї логіки — Rozetka ще не активна (магазин не зареєстрований), не
      блокує розробку, просто немає живих даних для звірки.

    Комісія оплати (еквайринг/Prom-оплата через RozetkaPay тощо) — ОКРЕМА
    від комісії майданчика, віднімається так само з виручки. Точний % не
    підтверджено — PAYMENT_COMMISSION, за замовчуванням 0.0 (TODO власника).

    Кожен продукт тепер отримує ДВІ незалежні ціни — price_prom, price_rozetka
    (різні нижні межі через різні комісії) — не одну спільну ціну.

ВИПРАВЛЕНО 2026-07-11 (було відкритим питанням з 2026-07-08): раніше "ціна
конкурента" була ОДНИМ спільним числом на товар (min_competitor), знайденим
ВИКЛЮЧНО на Rozetka.ua і застосованим до ОБОХ decide_price_for_platform()
викликів однаково (prom і rozetka) — тобто ціна конкурента з майданчика, де
ми навіть не продаємо (Rozetka неактивна, магазин не зареєстрований),
підмінювала відсутність даних про реальних конкурентів на Prom (де
продаємо). Тепер `--record` і `decide_price()` беруть ДВА окремих,
незалежних значення — `min_competitor_rozetka` (як і раніше, позиційний
аргумент) і `min_competitor_prom` (новий, --prom-competitor, за
замовчуванням None). Не заданий Prom-конкурент і далі означає те саме, що
й раніше для НЕ перевірених товарів: ціна для Prom рахується як
max(нижня_межа, cost*NO_COMPETITOR_MULT) — формульна, без вигаданого
сигналу з іншого майданчика. Джерело для `--prom-competitor` — новий
Prom-репрайсер (див. окремий проєкт/план моніторингу конкурентів на
Prom.ua), поки не побудований — прапорець готовий прийняти дані, коли
з'явиться.

Запуск:
    python competitor_pricing.py --seed-test           # записує вже знайдені 20 тестових товарів
    python competitor_pricing.py --status               # прогрес
    python competitor_pricing.py --next-batch 200        # наступні кандидати для перевірки
    python competitor_pricing.py --record 11070 200      # ціна конкурента на Rozetka
    python competitor_pricing.py --record 11070 200 --prom-competitor 195   # + ціна конкурента на Prom
    python competitor_pricing.py --record 11760 none     # конкурента не знайдено на жодному майданчику
"""

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

import time

from parser import fetch_toysi_catalog

BASE_DIR         = Path(__file__).parent
RESULTS_FILE     = BASE_DIR / "pricing_results.csv"
CHECKPOINT_FILE  = BASE_DIR / "competitor_pricing_checkpoint.json"
CATALOG_CACHE_FILE = BASE_DIR / "toysi_catalog_cache.json"
CATALOG_CACHE_TTL  = 3600  # секунд; --record викликається ~200 разів на день поспіль,
                           # кеш рятує від 200 зайвих запитів до фіду Toysi за один день

# ---------------------------------------------------------------------------
# Місток стійкості між prom_competitor_pricer.py (пише) і generate_prom_feed.py
# (читає) — виявлено 2026-07-12: prom_competitor_pricer.py застосовує ціну з
# урахуванням реального конкурента НАПРЯМУ через API, але generate_prom_feed.py
# рахує ціну з нуля щоразу, коли генерує фід (кожні 4 год через GitHub
# Actions) — без жодного знання про щойно застосоване рішення репрайсера.
# Наступний автоімпорт Prom (кожні 4 год) читає цей фід і тихо повертає ціну
# до дефолтної формули "немає конкурента" (cost * NO_COMPETITOR_MULT),
# перекреслюючи коригування репрайсера за кілька годин. Цей файл — спільне
# джерело правди для обох скриптів, замість двох незалежних розрахунків.
#
# НЕ pricing_results.csv (той файл — знімок РУЧНОГО процесу
# competitor_pricing.py --record, без timestamp, з іншою структурою колонок
# під обидва майданчики одразу) — окремий, спеціально для цього мосту.
PROM_PRICE_STATE_FILE = BASE_DIR / "prom_competitor_price_state.json"

# Довше за цикл автоімпорту Prom (4 год) і довше за добовий цикл репрайсера
# (24 год) — із запасом на випадок затримки таймера. Старіше цього —
# вважаємо застарілим, generate_prom_feed.py повертається до дефолтної
# формули "немає конкурента", а не покладається на давнє рішення.
PROM_PRICE_STATE_MAX_AGE_HOURS = 30


def load_prom_price_state() -> dict:
    if not PROM_PRICE_STATE_FILE.exists():
        return {}
    try:
        return json.loads(PROM_PRICE_STATE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def save_prom_price_state(state: dict) -> None:
    PROM_PRICE_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def load_fresh_prom_price_overrides(max_age_hours: float = PROM_PRICE_STATE_MAX_AGE_HOURS) -> dict:
    """{external_id: price} лише для записів не старіших за max_age_hours —
    generate_prom_feed.py використовує це як price_overrides, щоб не
    перезаписувати щойно застосовану репрайсером ціну дефолтною формулою
    "немає конкурента" на наступному автоімпорті Prom."""
    state = load_prom_price_state()
    now = datetime.now()
    overrides = {}
    for pid, entry in state.items():
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            price = float(entry["price"])
        except (KeyError, ValueError, TypeError):
            continue
        age_hours = (now - ts).total_seconds() / 3600
        if age_hours <= max_age_hours:
            overrides[pid] = price
    return overrides


MIN_PROFIT       = 0.25   # цільовий мінімум прибутку від собівартості (як частка cost)
PRICE_STEP       = 1.0    # фіксований крок нижче конкурента, У ГРН — не відсоток
NO_COMPETITOR_MULT = 1.75
DAILY_LIMIT      = 200
MIN_SUPPLIER_PRICE = 20

PLATFORMS = ("prom", "rozetka")

# ---------------------------------------------------------------------------
# Комісії за майданчиком — окремо від комісії оплати (обидві віднімаються
# з виручки, тому floor враховує суму двох).
# ---------------------------------------------------------------------------

# Prom.ua: комісія КАТЕГОРІЙНА (12-23%+) — дивитись у кабінеті Prom ->
# Показники роботи компанії -> Комісія за замовлення (дашборд "Категорії з
# комісією за замовлення", колонка "Єдина комісія" — підтверджено 2026-07-10,
# саме цей режим активний для PlutusToys, не "Економ"/"Турбо"). Ключ — назва
# категорії В НИЖНЬОМУ РЕГІСТРІ (get_platform_commission сам нормалізує вхідну
# category_name через .strip().lower(), тож сира назва з фіда Toysi, у
# будь-якому регістрі, все одно знайде відповідний ключ тут).
#
# 2026-07-10: заповнено для топ-6 категорій топ-970 (охоплюють ~48% SKU
# першої хвилі імпорту) — Toysi-категорія -> найближча категорія Prom:
#   - "пазли підлогові" -> "пазли і головоломки" (23.28%) — ТОЧНИЙ збіг з
#     раніше спостереженим реальним замовленням №414634349 (cpa_commission
#     9.08 грн / 39 грн = 23.28%), підтверджує коректність джерела.
#   - "іграшки антистрес" -> "іграшки-антистрес" (23.73%) — точна назва.
#   - "самокати двоколісні"/"самокати триколісні" -> "самокати" (11.19%) —
#     єдина відповідна категорія Prom, обидва варіанти Toysi туди ведуть.
#   - "басейни" -> "надувні басейни" (11.75%) — НЕ 100% впевнено: у Prom є
#     окремо "каркасні басейни" (11.56%) і "басейни, загальне" (25.00%),
#     обрано "надувні" як найімовірніший варіант для дитячого магазину
#     іграшок, але не звірено з фактичним асортиментом.
# 2026-07-13 (задача #58): повна таблиця ProSale (~90 категорій, живі дані
# з кабінету my.prom.ua/cms/prosale, максимум по всьому каталогу — 25%,
# джерело: prosale_commissions_full.md) дозволила зіставити решту реальних
# категорій Toysi (291 унікальна назва в повному каталозі) з категоріями
# Prom. Критерій включення — ТОЙ САМИЙ, що й вище: додаю лише там, де назва
# Toysi-категорії і Prom-категорії мають чіткий, недвозначний збіг по суті
# (спільний корінь/термін, як-от "пазли" в обох, чи "стрибуни" явно входить
# у "іграшки-гойдалки, іграшки-стрибуни") — НЕ додаю, де Toysi-категорія
# фактично охоплює кілька Prom-категорій з різними ставками одночасно
# (той самий ризик, що й з "рюкзаки" вище). Приклади свідомо НЕ заповнених
# через таку двозначність: "рюкзаки" (все ще, як і було), "Вентилятори і
# вітрячки" (Prom розділяє "вентилятори" 10.35% і "повітряні змії, вітрячки"
# 18.79% — велика розбіжність), "Мозаїки" (загальна, не плутати з окремою
# "Алмазна мозаїка" нижче — Prom ділить на "алмазна" 15.7% і "дитяча" 19.29%),
# "М'ячики і стрибунці" (дитячі м'ячі 16.08% проти іграшки-стрибуни 10.9%),
# "Дитячі меблі", "Лялькові набори", "Ляльки м'які", "Інтерактивні та
# навчальні іграшки" (тут різниця найбільша — 19.8% проти 10.87%).
# "надувні басейни" (11.75%) в ProSale-таблиці ТОЧНО збігається з уже
# внесеним значенням для "басейни" нижче — попередня непевність знята.
PROM_CATEGORY_COMMISSION: dict[str, float] = {
    "пазли підлогові": 0.2328,
    "іграшки антистрес": 0.2373,
    "самокати двоколісні": 0.1119,
    "самокати триколісні": 0.1119,
    "басейни": 0.1175,  # підтверджено 2026-07-13 повною таблицею ProSale

    # пазли і головоломки — 23.28%
    "пазли g-toys": 0.2328,
    "пазли castorland": 0.2328,
    "пазли dankotoys": 0.2328,
    "пазли trefl": 0.2328,
    "пазли для малюків": 0.2328,
    "інші пазли": 0.2328,
    "пазли і вкладиші": 0.2328,
    "головоломки": 0.2328,

    # ляльки, пупси — 19.81%
    "ляльки": 0.1981,
    "пупси": 0.1981,

    # м'які іграшки — 19.93%
    "м'які іграшки": 0.1993,
    "ведмеді": 0.1993,

    # конструктори — 19.67%
    "пластикові конструктори": 0.1967,
    "конструктори типу лего": 0.1967,
    "дерев'яні конструктори": 0.1967,
    "металеві конструктори": 0.1967,
    "магнітні конструктори": 0.1967,
    "незвичайні конструктори": 0.1967,
    "конструктори": 0.1967,

    # розмальовки — 11.86%
    "класичні розмальовки": 0.1186,
    "водні розмальовки": 0.1186,
    "розмальовки та розпис": 0.1186,
    "незвичайні розмальовки": 0.1186,

    "скейти і пеніборди": 0.1091,        # скейтборди та ролерсерфи
    "наукові ігри": 0.1962,              # наукові ігри, набори для дослідів
    "іграшки для ванної": 0.154,         # іграшки для ванної (точний збіг)
    "іграшки - каталки": 0.1586,         # дитячі іграшки-каталки
    "каталки-толокар": 0.1073,           # дитячі машинки-каталки

    # ігрові фігурки, роботи трансформери — 19.92%
    "трансформери": 0.1992,
    "роботи": 0.1992,

    "біговели": 0.1121,                  # точний збіг

    # самокати — 11.19%
    "самокати чотириколісні": 0.1119,
    "самокати": 0.1119,

    # дзиги та спінери — 19.45%
    "дзиги": 0.1945,
    "бейблейд": 0.1945,

    "стрибуни": 0.109,                   # іграшки-гойдалки, іграшки-стрибуни
    "коляски для ляльок": 0.1232,        # коляски для ляльок та пупсів
    "залізниці і треки": 0.1936,         # дитячі залізниці, автотреки

    # іграшки-антистрес — 23.73%
    "сквіші": 0.2373,
    "тягучки та стретчі": 0.2373,
    "лизуни, слайми та жуйки для рук": 0.2373,

    "настільні ігри": 0.2258,            # точний збіг
    "надувні матраси": 0.1424,           # надувні матраци (варіант написання)

    # іграшкові пістолети, арбалети та шаблі — 19.96%
    "мечі, ножі та шаблі": 0.1996,
    "автомати і пістолети": 0.1996,
    "водна зброя": 0.1996,
    "зброя": 0.1996,
    "набори зі зброєю": 0.1996,
    "арбалети і луки": 0.1996,

    # іграшки для ігор з піском, водою та снігом — 19.52%
    "пісочні набори": 0.1952,
    "лопатки і граблі": 0.1952,
    "пасочки": 0.1952,
    "кінетичний пісок": 0.1952,

    "машинки ру": 0.1559,                # радіокеровані іграшки (РУ = р/к)
    "повітряні змії": 0.1879,            # повітряні змії, вітрячки

    # іграшкові машинки, літачки, техніка — 15.75%
    "пластикові машинки": 0.1575,
    "машини гіганти": 0.1575,
    "літаки і вертольоти": 0.1575,
    "планери": 0.1575,
    "машинки на батарейках": 0.1575,

    "кухні і посуд": 0.1417,             # дитячі ігрові кухні
    "музичні інструменти": 0.1924,       # музичні іграшки
    "нічники": 0.15,                     # настільні лампи і нічники
    "каремати і килимки": 0.1599,        # килимки для йоги та фітнесу

    # надувні круги, платформи — 14.94%
    "круги і жилети": 0.1494,
    "круги для купання": 0.1494,

    # розвиваючі та навчальні іграшки — 10.87%
    "розвиваючі килимки": 0.1087,
    "набори для навчання": 0.1087,

    "розважальні інтерактивні іграшки": 0.198,  # інтерактивні дитячі іграшки

    # тематичні ігрові набори — 19.59%
    "лікарські набори": 0.1959,
    "перукарські набори": 0.1959,
    "набори інструментів": 0.1959,
    "супермаркет": 0.1959,

    "меблі для ляльок": 0.1939,          # аксесуари для ляльок та пупсів
    "тісто для ліплення і пластилін": 0.1577,  # пластилін та маса для ліплення
    "мильні бульбашки": 0.1663,          # точний збіг
    "брелоки": 0.1969,                   # точний збіг
    "алмазна мозаїка": 0.157,            # точний збіг
    "велосипеди": 0.131,                 # точний збіг
    "картини за номерами": 0.1564,       # точний збіг
    "ходунки": 0.1099,                   # дитячі ходунки
    "бокс": 0.1494,                      # боксерські груші та снаряди
}
PROM_COMMISSION_DEFAULT = 0.20  # орієнтовний fallback, ПОКИ категорія не уточнена в кабінеті

# Rozetka: перенесено як орієнтовний дефолт з попередньої версії цієї логіки
# (комісія 22%, категорія "Дитячі іграшки") — Rozetka ще не активна (магазин
# не зареєстрований), тому немає живих даних для звірки. Не блокує розробку.
ROZETKA_COMMISSION_DEFAULT = 0.22

# Комісія оплати (еквайринг/Prom-оплата через RozetkaPay, банківський
# еквайринг Rozetka тощо) — ОКРЕМА статті від комісії майданчика вище.
# Точний % не підтверджено в жодному з наявних документів проєкту.
# TODO (власник): уточнити в кабінеті RozetkaPay / банку-еквайєра.
PAYMENT_COMMISSION: dict[str, float] = {
    "prom": 0.0,
    "rozetka": 0.0,
}

FIELDNAMES = [
    "id", "name", "cost", "min_competitor_prom", "min_competitor_rozetka",
    "floor_prom", "price_prom", "margin_pct_prom", "category_prom",
    "floor_rozetka", "price_rozetka", "margin_pct_rozetka", "category_rozetka",
    "match_confidence",
]

# Товари, які завідомо погано підходять для пошуку конкурента (уцінка/брак)
_BAD_NAME_MARKERS = ("уцінка", "пошкодж", "не працює", "немає", "брак")


# ---------------------------------------------------------------------------
# Кеш каталогу Toysi (щоб --record, викликаний ~200x/день, не бив по мережі щоразу)
# ---------------------------------------------------------------------------

def get_catalog(force_refresh: bool = False) -> dict:
    if not force_refresh and CATALOG_CACHE_FILE.exists():
        age = time.time() - CATALOG_CACHE_FILE.stat().st_mtime
        if age < CATALOG_CACHE_TTL:
            return json.loads(CATALOG_CACHE_FILE.read_text(encoding="utf-8"))

    catalog = fetch_toysi_catalog()
    CATALOG_CACHE_FILE.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    return catalog


# ---------------------------------------------------------------------------
# Логіка ціноутворення
# ---------------------------------------------------------------------------

def get_platform_commission(platform: str, category_name: str | None = None) -> float:
    """Комісія майданчика (БЕЗ комісії оплати) для конкретної категорії.
    Для Prom — категорійний пошук у PROM_CATEGORY_COMMISSION з fallback на
    PROM_COMMISSION_DEFAULT; для Rozetka — один орієнтовний дефолт (категорія
    поки не впливає, немає живих даних).

    category_name нормалізується (strip + lower) перед пошуком — сира назва
    категорії з фіда Toysi (parser.py) зберігає оригінальний регістр, а ключі
    PROM_CATEGORY_COMMISSION заповнюються вручну з кабінету Prom (приклад у
    коментарі нижче — лише нижній регістр), тож без нормалізації збіг ніколи
    не спрацював би для реальних категорій."""
    if platform == "prom":
        key = category_name.strip().lower() if category_name else None
        if key and key in PROM_CATEGORY_COMMISSION:
            return PROM_CATEGORY_COMMISSION[key]
        return PROM_COMMISSION_DEFAULT
    if platform == "rozetka":
        return ROZETKA_COMMISSION_DEFAULT
    raise ValueError(f"Невідомий майданчик: {platform!r}, очікую один з {PLATFORMS}")


def decide_price_for_platform(
    cost: float, min_competitor: float | None, platform: str, category_name: str | None = None
) -> dict:
    """
    Рішення про ціну для ОДНОГО майданчика (Prom або Rozetka):
        нижня_межа = (cost + cost*MIN_PROFIT) / (1 - комісія_майданчика - комісія_оплати)
        кандидат   = min_competitor - PRICE_STEP (фіксований крок, грн — не %)
        ціна       = max(нижня_межа, кандидат)

    Завжди повертає ціну — товари з "нерентабельним" конкурентом більше НЕ
    пропускаються (як було в старій категорії C): якщо кандидат нижчий за
    нижню межу, ціна просто піднімається до межі (може вийти вищою за
    конкурента — так і задумано формулою, це не помилка).
    """
    platform_commission = get_platform_commission(platform, category_name)
    payment_commission = PAYMENT_COMMISSION.get(platform, 0.0)
    total_commission = platform_commission + payment_commission
    if total_commission >= 1:
        raise ValueError(
            f"[{platform}] сумарна комісія {total_commission:.0%} >= 100% — перевір "
            "PROM_CATEGORY_COMMISSION/ROZETKA_COMMISSION_DEFAULT/PAYMENT_COMMISSION"
        )

    floor = (cost + cost * MIN_PROFIT) / (1 - total_commission)

    if min_competitor is None:
        price = round(max(cost * NO_COMPETITOR_MULT, floor), 2)
        result_category = "no_competitor"
    else:
        candidate = min_competitor - PRICE_STEP
        price = round(max(floor, candidate), 2)
        result_category = "floor" if floor >= candidate else "undercut"

    net_revenue = price * (1 - total_commission)
    margin_pct = round((net_revenue - cost) / cost * 100, 1) if cost else 0.0

    return {
        "platform": platform,
        "floor": round(floor, 2),
        "price": price,
        "category": result_category,
        "margin_pct": margin_pct,
    }


def decide_price(
    cost: float,
    min_competitor_rozetka: float | None,
    min_competitor_prom: float | None = None,
    category_name: str | None = None,
) -> dict:
    """
    Рахує рішення ОКРЕМО для кожного майданчика (Prom, Rozetka) — не лише
    різні комісії (категорійна для Prom, орієнтовний дефолт для Rozetka),
    а й ОКРЕМІ, незалежні ціни конкурента для кожного майданчика (виправлено
    2026-07-11 — раніше одна спільна ціна конкурента з Rozetka помилково
    підставлялась і в Prom-розрахунок теж, див. докстрінг файлу). Повертає
    обидва результати під ключами "prom"/"rozetka".
    """
    return {
        "prom": decide_price_for_platform(cost, min_competitor_prom, "prom", category_name),
        "rozetka": decide_price_for_platform(cost, min_competitor_rozetka, "rozetka"),
    }


# ---------------------------------------------------------------------------
# Checkpoint (лише денний ліміт/лічильники; самі результати живуть у CSV)
# ---------------------------------------------------------------------------

def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
    return {"last_run_date": None, "processed_today": 0}


def save_checkpoint(cp: dict) -> None:
    CHECKPOINT_FILE.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")


def _bump_checkpoint_for_today() -> dict:
    cp = load_checkpoint()
    today = date.today().isoformat()
    if cp.get("last_run_date") != today:
        cp["last_run_date"] = today
        cp["processed_today"] = 0
    return cp


# ---------------------------------------------------------------------------
# pricing_results.csv
# ---------------------------------------------------------------------------

def load_processed_ids() -> set:
    if not RESULTS_FILE.exists():
        return set()
    with open(RESULTS_FILE, newline="", encoding="utf-8-sig") as f:
        return {row["id"] for row in csv.DictReader(f)}


def append_result(row: dict) -> None:
    is_new = not RESULTS_FILE.exists()
    with open(RESULTS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Вибір черги
# ---------------------------------------------------------------------------

def select_batch(limit: int = DAILY_LIMIT) -> list:
    """Обирає наступні до `limit` товарів для перевірки на Rozetka.

    Пріоритет — дешевші товари: тест на 20 позиціях показав, що товари
    собівартістю <300 грн частіше дають "undercut" (перебиваємо конкурента
    й лишаємось прибутковими), тоді як товари >700 грн частіше впираються в
    "floor" (конкурент занадто дешевий, ціну доводиться піднімати до межі
    маржі). Уцінені/пошкоджені товари виключаються одразу (немає сенсу
    шукати конкурента на брак).
    """
    catalog = get_catalog()
    processed = load_processed_ids()

    candidates = []
    for item in catalog.values():
        pid = str(item["id"])
        if pid in processed:
            continue
        name = (item.get("name") or "").strip()
        if any(marker in name.lower() for marker in _BAD_NAME_MARKERS):
            continue
        try:
            cost = float(item.get("price") or 0)
        except (ValueError, TypeError):
            continue
        if cost < MIN_SUPPLIER_PRICE:
            continue
        candidates.append((cost, pid, name))

    candidates.sort(key=lambda c: c[0])  # дешевші — першими
    return candidates[:limit]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_status() -> None:
    processed = load_processed_ids()
    cp = load_checkpoint()
    counts = {"prom": {"undercut": 0, "floor": 0, "no_competitor": 0}, "rozetka": {"undercut": 0, "floor": 0, "no_competitor": 0}}
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                for platform in PLATFORMS:
                    cat = row.get(f"category_{platform}")
                    if cat in counts[platform]:
                        counts[platform][cat] += 1
    print(f"Оброблено всього: {len(processed)}")
    for platform in PLATFORMS:
        c = counts[platform]
        print(f"  {platform}: undercut={c['undercut']} floor={c['floor']} no_competitor={c['no_competitor']}")
    print(f"Останній запуск: {cp.get('last_run_date')}, оброблено сьогодні: {cp.get('processed_today', 0)}/{DAILY_LIMIT}")


def cmd_next_batch(limit: int) -> None:
    cp = _bump_checkpoint_for_today()
    remaining_today = max(0, DAILY_LIMIT - cp.get("processed_today", 0))
    limit = min(limit, remaining_today) if remaining_today else 0
    if limit == 0:
        print(f"Денний ліміт {DAILY_LIMIT} товарів вже вичерпано на сьогодні ({cp['last_run_date']}).")
        return
    batch = select_batch(limit)
    print(f"Наступні {len(batch)} товарів для перевірки на Rozetka (дешевші — першими):")
    for cost, pid, name in batch:
        print(f"{pid}\t{cost:.2f}\t{name}")


def cmd_record(
    pid: str,
    min_competitor_raw: str,
    match_confidence: str,
    category_name: str | None = None,
    prom_competitor_raw: str | None = None,
) -> None:
    catalog = get_catalog()
    item = catalog.get(pid)
    if item is None:
        print(f"Товар {pid} не знайдено в каталозі Toysi (можливо, зник з живого фіда постачальника).")
        sys.exit(1)

    cost = float(item.get("price") or 0)
    if cost <= 0:
        print(f"Товар {pid} має нульову/відсутню ціну постачальника Toysi (cost=0) — пропускаємо, ціну не рахуємо.")
        return
    min_competitor_rozetka = None if min_competitor_raw.lower() == "none" else float(min_competitor_raw)
    min_competitor_prom = None
    if prom_competitor_raw is not None and prom_competitor_raw.lower() != "none":
        min_competitor_prom = float(prom_competitor_raw)

    decision = decide_price(cost, min_competitor_rozetka, min_competitor_prom, category_name)
    prom, rozetka = decision["prom"], decision["rozetka"]
    row = {
        "id": pid,
        "name": item.get("name", ""),
        "cost": f"{cost:.2f}",
        "min_competitor_prom": f"{min_competitor_prom:.2f}" if min_competitor_prom is not None else "",
        "min_competitor_rozetka": f"{min_competitor_rozetka:.2f}" if min_competitor_rozetka is not None else "",
        "floor_prom": f"{prom['floor']:.2f}",
        "price_prom": f"{prom['price']:.2f}",
        "margin_pct_prom": f"{prom['margin_pct']:.1f}",
        "category_prom": prom["category"],
        "floor_rozetka": f"{rozetka['floor']:.2f}",
        "price_rozetka": f"{rozetka['price']:.2f}",
        "margin_pct_rozetka": f"{rozetka['margin_pct']:.1f}",
        "category_rozetka": rozetka["category"],
        "match_confidence": match_confidence,
    }
    append_result(row)

    cp = _bump_checkpoint_for_today()
    cp["processed_today"] = cp.get("processed_today", 0) + 1
    save_checkpoint(cp)

    print(
        f"{item.get('name','')[:50]} -> "
        f"Prom: [{prom['category']}] {prom['price']:.2f} грн ({prom['margin_pct']}%) | "
        f"Rozetka: [{rozetka['category']}] {rozetka['price']:.2f} грн ({rozetka['margin_pct']}%)"
    )


def cmd_seed_test() -> None:
    """Записує 20 товарів, вже вручну перевірених через claude-in-chrome (сесія аналізу конкурентів)."""
    seed_data = [
        # (id, min_competitor або None, впевненість збігу)
        ("11070", 200,  "приблизний (варіант «Маринка 2»)"),
        ("11127", 76,   "точний"),
        ("11128", 77,   "точний"),
        ("11130", 129,  "точний"),
        ("10038", 250,  "приблизний (загальна категорія)"),
        ("10159", 244,  "точний"),
        ("11107", 392,  "точний"),
        ("11108", 276,  "точний"),
        ("10991", 639,  "точний"),
        ("10992", 500,  "точний (той самий SKU)"),
        ("10993", 484,  "точний"),
        ("11088", 744,  "точний"),
        ("11940", 775,  "приблизний"),
        ("13776", 2010, "точний"),
        ("16727", 1008, "точний (той самий SKU)"),
        ("18204", 1357, "приблизний (варіант без картону)"),
        ("11760", None, "не знайдено відповідника"),
        ("18207", 2505, "точний (той самий SKU)"),
        ("34594", 3575, "приблизний"),
        ("37007", 5085, "точний (той самий SKU)"),
    ]
    already = load_processed_ids()
    added = 0
    for pid, comp, conf in seed_data:
        if pid in already:
            print(f"{pid} вже є в pricing_results.csv — пропущено")
            continue
        cmd_record(pid, "none" if comp is None else str(comp), conf)
        added += 1
    print(f"\nЗасіяно {added} товарів з ручного тесту.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("seed-test")

    p_batch = sub.add_parser("next-batch")
    p_batch.add_argument("limit", nargs="?", type=int, default=DAILY_LIMIT)

    p_record = sub.add_parser("record")
    p_record.add_argument("id")
    p_record.add_argument("min_competitor", help='ціна конкурента на Rozetka або "none"')
    p_record.add_argument("confidence", nargs="?", default="точний")
    p_record.add_argument(
        "--category", default=None,
        help="Категорія Prom для категорійної комісії (ключ у PROM_CATEGORY_COMMISSION); "
             "якщо не задано — використовується PROM_COMMISSION_DEFAULT",
    )
    p_record.add_argument(
        "--prom-competitor", default=None,
        help='Ціна конкурента, знайдена САМЕ на Prom.ua, або "none" (за замовчуванням: не задано — '
             "не плутати з відсутністю конкурента; None тут означає, що Prom-конкурента взагалі не "
             "перевіряли, тому ціна для Prom рахується формульно, без живого сигналу)",
    )

    args = ap.parse_args()

    if args.cmd == "status":
        cmd_status()
    elif args.cmd == "seed-test":
        cmd_seed_test()
    elif args.cmd == "next-batch":
        cmd_next_batch(args.limit)
    elif args.cmd == "record":
        cmd_record(args.id, args.min_competitor, args.confidence, args.category, args.prom_competitor)


if __name__ == "__main__":
    main()
