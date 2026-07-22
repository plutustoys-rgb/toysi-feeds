import json
import re
from pathlib import Path

from parser import fetch_toysi_catalog
from generate_prom_feed import default_retail_price, generate_feed, is_clearance_item, MIN_SUPPLIER_PRICE
from competitor_pricing import decide_price_for_platform, load_delisted_pids, load_fresh_prom_price_overrides, real_toysi_cost

# ВИПРАВЛЕНО (2026-07-16, задача власниці — full_catalog_competitor_scan.py
# не мав лишатись окремим інформаційним скриптом): щоночі
# full_catalog_competitor_scan.py (VPS-таймер, 01:00 Kyiv) поповнює цей
# файл реальними конкурентними даними (competitor_price/competitor_alive/
# decide_price_for_platform()) для дедалі більшої частки каталогу (3015/
# 17836 на момент цього фіксу). Раніше ці дані просто лежали мертвим
# вантажем — select_top_items()/_margin() ранжував УСІ товари наївною
# формулою (собівартість + категорійна комісія), яка НЕ враховує, чи є
# конкурент і за якою ціною — SKU, що виглядає прибутковим наївно, може
# насправді впиратись у 3%-поріг floor, щойно відомий реальний конкурент;
# і навпаки, SKU без конкурента може витримати вищу ціну, ніж наївна
# формула йому дає. Тепер: для вже просканованих SKU _margin() рахує
# РЕАЛЬНУ, конкурентно-обізнану маржу (тим самим decide_price_for_platform(),
# що й сам скан), а не наївну оцінку — і бере участь у тій самій сортовій
# ротації топ-970/1000, що й решта каталогу, автоматично, без окремого
# запуску чи ручного втручання. Для ще НЕ просканованих SKU (переважна
# більшість, поки скан не завершений) поведінка не змінюється.
FULL_CATALOG_SCAN_STATE_FILE = Path(__file__).parent / "full_catalog_scan_state.json"


def load_scan_state() -> dict:
    """Читає стан full_catalog_competitor_scan.py, якщо він є (VPS-таймер
    пише його локально; на GH Actions runner'і файл підтягується окремим
    кроком workflow — див. update-feeds.yml). Відсутність файлу чи
    помилка читання — НЕ помилка: просто ще немає накопичених
    конкурентних даних, select_top_items() працює на наївній оцінці, як і
    раніше до цього фіксу."""
    if not FULL_CATALOG_SCAN_STATE_FILE.exists():
        return {}
    try:
        return json.loads(FULL_CATALOG_SCAN_STATE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}

OUTPUT_FILE     = "feeds/prom_feed_top.xml"
# Одна константа платформи на файл — див. коментар біля PLATFORM у
# generate_prom_feed.py (той самий фікс, 2026-07-21, привід — плутанина
# комісій між феєдами).
PLATFORM        = "prom"
TARGET_COUNT    = 1000
SAFETY_BUFFER   = 30  # тримаємось нижче ліміту Prom, бо він рахує ще й "різновиди"
SELECT_COUNT    = TARGET_COUNT - SAFETY_BUFFER

# Категорії-лідери продажів (узгоджено з власником) — ключові слова шукаються
# в назві товару + назві категорії, регістронезалежно.
# Кожен запис: (потрібні_підрядки, заборонені_підрядки).
# Усі "потрібні" мають бути присутні разом (для уточнення на кшталт "водний пістолет"
# замість самого "водний"); якщо присутній хоч один "заборонений" — група не рахується
# збігом, навіть якщо всі потрібні підрядки на місці.
LEADER_KEYWORD_GROUPS = [
    (("антистрес",), ()),
    (("лід",), ("рубик", "лідер")),       # "лід" — але не "кубик Рубика" чи конструктор "Лідер"
    (("льод",), ("рубик",)),             # льодяний / льодинка / кубик льоду (спільний корінь "льод")
    (("кубик", "льод"), ("рубик",)),     # явно "кубик льоду" (дублює "льод" вище, але лишаємо для наочності)
    (("doubling", "dumpling"), ()),
    (("пельмень",), ()),
    (("dumpling",), ()),
    (("сквіш",), ()),
    (("брелок", "клікер"), ()),
    (("водн", "пістолет"), ()),
    (("paw patrol",), ()),
    (("надувн", "коло"), ()),
]


def _normalize(text: str) -> str:
    return (text or "").lower().replace("’", "").replace("'", "")


_WORD_CHAR = re.compile(r"[a-zа-яіїєґ0-9]", re.IGNORECASE)


def _contains_keyword(text: str, keyword: str) -> bool:
    """Підрядок має починатися на межі слова, а не бути хвостом іншого слова —
    інакше короткий корінь на кшталт "лід" збігається всередині "дослід"/"дослідів"."""
    idx = text.find(keyword)
    while idx != -1:
        if idx == 0 or not _WORD_CHAR.match(text[idx - 1]):
            return True
        idx = text.find(keyword, idx + 1)
    return False


def is_leader_category(item: dict) -> bool:
    text = _normalize(f"{item.get('name', '')} {item.get('category_name', '')}")
    for required, excluded in LEADER_KEYWORD_GROUPS:
        if all(_contains_keyword(text, kw) for kw in required) and not any(
            _contains_keyword(text, kw) for kw in excluded
        ):
            return True
    return False


# Категорії, виключені з першої хвилі імпорту в Prom (рішення власника,
# 2026-07-09) — audit_prom_characteristics.py виявив масову відсутність
# характеристик з боку Toysi: "Велосипеди" — 450 з 970 SKU топ-фіда (46%
# імпорту), 270 без країни походження, 450 без ЖОДНОЇ змістовної
# характеристики (Toysi для цієї категорії не надає нічого, крім розмірів
# упаковки). Перевір audit_prom_characteristics.py перед тим, як повертати
# категорію назад — рішення діє, поки дані від Toysi не покращаться або
# характеристики не буде донаповнено вручну в кабінеті Prom.
EXCLUDED_CATEGORIES = {"велосипеди"}


def is_excluded_category(item: dict) -> bool:
    return (item.get("category_name") or "").strip().lower() in EXCLUDED_CATEGORIES


def _margin(item: dict, pid: str = None, scan_state: dict = None, delisted_pids: dict = None) -> float:
    """Розрахункова маржа (retail - cost). -1, якщо товар не має валідної/
    прийнятної ціни, немає залишку на складі Toysi (2026-07-10: раніше
    цього фільтра не було взагалі — SKU 267102 потрапив у топ-970 з
    quantity_in_stock=0, зайнявши місце товару, який реально можна
    продати), уцінений/пошкоджений товар (не належить у "топ" незалежно
    від маржі), чи ПІДТВЕРДЖЕНО видалений prom_competitor_pricer.py на
    попередньому прогоні за неконкурентність (delisted_pids —
    competitor_pricing.load_delisted_pids()).

    КРИТИЧНИЙ ФІКС (2026-07-18, реальний інцидент — SKU 266990/265230 та
    ще ~357 інших): без цієї перевірки select_top_items() одразу ж (той
    самий workflow-прогін, наступний крок після repricer'а) знову включав
    щойно ЖИВО видалений SKU в prom_feed_top.xml, бо не мав ЖОДНОГО
    сигналу про сам факт видалення — рахував топ-970 виключно з даних
    Toysi/scan_state. Коли Prom періодично імпортував цей прайс-лист — він
    сам відновлював "видалене" оголошення, повністю скасовуючи ефект
    delist() протягом кількох годин. Запис прибирається з delisted_pids
    автоматично, щойно SKU знову проходить у to_adjust (конкурент
    подешевшав/зник) — див. prom_competitor_pricer.py::main().

    Дві формули для самої величини маржі:
    - Товар ВЖЕ просканований full_catalog_competitor_scan.py (pid є в
      scan_state) — рахуємо РЕАЛЬНУ, конкурентно-обізнану маржу тим самим
      decide_price_for_platform(cost, competitor_price, PLATFORM, category),
      що й сам скан (competitor_price береться лише якщо
      competitor_alive=True — мертвий конкурент трактуємо як "немає
      конкурента", той самий принцип обережності, що й в іншому коді
      проєкту). Це та сама формула, яку generate_prom_feed.py реально
      застосує для ціни, якщо товар потрапить у топ — на відміну від
      наївної оцінки нижче, вона знає, чи є конкурент і за якою ціною.

      ВИПРАВЛЕНО (2026-07-18, пряме рішення власника, той самий фікс, що
      й prom_competitor_pricer.py::decide_action() — code_report_2026-
      07-18_pt3.md): якщо є ЖИВИЙ конкурент і навіть наша нижня межа
      маржі (3%) не дозволяє підрізати його на 1 грн (decision["category"]
      == "floor") — товар НЕ претендує на місце в топ-970/1000 ВЗАГАЛІ
      (return -1, як і немає залишку/уцінка), а не просто нижчим рангом.
      Раніше такий SKU все одно потрапляв у топ і показувався на вітрині
      системно дорожчим за конкурента (підтверджено живо: 10-31% розрив на
      реальних SKU) — тепер його місце звільняється для дійсно
      конкурентного/прибуткового товару.
    - Товар ЩЕ не просканований (переважна більшість, поки скан не
      завершено) — стара наївна оцінка (default_retail_price — комісія
      категорії Prom + нижня межа маржі, БЕЗ обізнаності про конкурента).
      Це свідома, тимчасова відмінність у точності, не помилка — не
      можемо порахувати конкурентно-обізнану маржу для товару, який ще
      не скановано."""
    if is_clearance_item(item.get("name"), item.get("category_name"), item.get("category_id")):
        return -1
    if item.get("stock", 0) <= 0:
        return -1
    if pid is not None and pid in (delisted_pids or {}):
        return -1
    cost = real_toysi_cost(item)  # 2026-07-22: реальна собівартість з урахуванням знижки Toysi, не сира каталожна ціна
    if cost <= 0 or cost < MIN_SUPPLIER_PRICE:
        return -1

    scan_entry = (scan_state or {}).get(pid) if pid is not None else None
    if scan_entry is not None:
        competitor_price = scan_entry.get("competitor_price") if scan_entry.get("competitor_alive") else None
        decision = decide_price_for_platform(cost, competitor_price, PLATFORM, item.get("category_name"))
        if decision["category"] == "floor":
            return -1
        return decision["price"] - cost

    return default_retail_price(cost, item.get("category_name")) - cost


def select_top_items(catalog: dict, target: int = SELECT_COUNT) -> dict:
    """
    1. Спочатку — товари з категорій-лідерів (LEADER_KEYWORD_GROUPS).
       Якщо їх більше за target — сортуємо за маржею і беремо top `target`.
    2. Якщо лідерів менше за target — доповнюємо "решта"-кошиком, поки не
       набереться `target`. Усередині "решта" — ДВОЕТАПНЕ ранжування
       (ВИПРАВЛЕНО 2026-07-17, code_report pt18, Варіант А): спершу ВСІ
       вже ПРОСКАНОВАНІ (підтверджена конкурентна маржа) SKU, відсортовані
       за грошовою маржею між собою; лише якщо після них лишились вільні
       місця — непросканові SKU за наївною формулою, теж за спаданням.

    Чому саме так: пряме сортування ВСЬОГО "решта"-кошика за грошовою
    маржею (стара поведінка) систематично ставило непідтверджені здогадки
    ВИЩЕ за доведений прибуток — наївна формула (default_retail_price)
    ЗАВЖДИ припускає "конкурента немає" (множник 1.75×), тож дає велику
    номінальну маржу незалежно від реальності, тоді як підтверджена,
    просканована маржа обмежена тонкою ціллю ~3% (Шлях 2, коли конкурент
    реально знайдений) — набагато менша в грошах, НАВІТЬ коли товар
    дійсно прибутковий і конкурентоздатний. Живо підтверджено pt18: 2925
    із 3016 просканованих SKU з підтвердженою маржею ≥3% були виключені
    з топ-970 на користь непідтверджених здогадок, 570 із 970 позицій
    "решта"-кошика були чистими здогадками. Двоетапне ранжування нижче
    гарантує, що ЖОДЕН підтверджений прибутковий SKU не програє
    непідтвердженому лише через різницю номінальних формул.

    Маржа рахується ОДИН раз на товар (не при кожному сортуванні) і бере
    до уваги накопичені дані full_catalog_competitor_scan.py, якщо вони
    є (load_scan_state()), а також SKU, підтверджено видалені
    prom_competitor_pricer.py на попередньому прогоні (load_delisted_pids())
    — обидва повністю виключають товар з відбору (return -1 у _margin()),
    див. докстрінги обох функцій.
    """
    scan_state = load_scan_state()
    delisted_pids = load_delisted_pids()
    margins = {pid: _margin(item, pid, scan_state, delisted_pids) for pid, item in catalog.items()}

    eligible = {
        pid: item for pid, item in catalog.items()
        if margins[pid] >= 0 and not is_excluded_category(item)
    }

    leaders = {pid: item for pid, item in eligible.items() if is_leader_category(item)}
    rest    = {pid: item for pid, item in eligible.items() if pid not in leaders}

    leaders_sorted = sorted(leaders.items(), key=lambda kv: margins[kv[0]], reverse=True)

    rest_scanned   = {pid: item for pid, item in rest.items() if pid in scan_state}
    rest_unscanned = {pid: item for pid, item in rest.items() if pid not in scan_state}
    rest_scanned_sorted   = sorted(rest_scanned.items(), key=lambda kv: margins[kv[0]], reverse=True)
    rest_unscanned_sorted = sorted(rest_unscanned.items(), key=lambda kv: margins[kv[0]], reverse=True)
    rest_sorted = rest_scanned_sorted + rest_unscanned_sorted

    if len(leaders_sorted) >= target:
        selected = leaders_sorted[:target]
    else:
        remaining = target - len(leaders_sorted)
        selected = leaders_sorted + rest_sorted[:remaining]

    return dict(selected)


def generate_top_feed(output_file: str = OUTPUT_FILE) -> None:
    print("[Prom Top] Завантажуємо каталог Toysi...")
    catalog = fetch_toysi_catalog()
    if not catalog:
        print("[Prom Top] Каталог порожній — файл не створено.")
        return

    top_catalog   = select_top_items(catalog)
    leaders_count = sum(1 for item in top_catalog.values() if is_leader_category(item))

    print(
        f"[Prom Top] Відібрано {len(top_catalog)} товарів "
        f"(з категорій-лідерів: {leaders_count}, доповнено рештою каталогу: {len(top_catalog) - leaders_count})"
    )

    # ВИПРАВЛЕНО 2026-07-14: той самий бага, що й у generate_prom_feed.py
    # (виправлено там 2026-07-12), лишався тут неторкнутим — виклик без
    # price_overrides означав, що КОЖЕН SKU топ-970 рахувався з нуля за
    # формулою "немає конкурента", ігноруючи щойно застосовану
    # prom_competitor_pricer.py конкурентну ціну. Оскільки prom_feed.xml
    # (повний каталог) з 2026-07-13 стабільно не публікується через ліміт
    # GitHub 100 МБ, саме цей файл (prom_feed_top.xml) — єдиний, що зараз
    # реально й регулярно доходить до Prom, тож без цього фіксу коригування
    # репрайсера для ~940 SKU топ-970 стиралися щоразу на наступному
    # автоімпорті (~кожні 4 год).
    generate_feed(
        output_file=output_file,
        catalog=top_catalog,
        price_overrides=load_fresh_prom_price_overrides(),
    )


if __name__ == "__main__":
    generate_top_feed()
