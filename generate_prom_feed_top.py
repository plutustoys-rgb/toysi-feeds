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
# ЗМІНЕНО (2026-07-24, пряме рішення власниці): підписка Prom і денний
# ліміт створення товарів обидва підняті до 6000 — без поетапності,
# одразу на повну ціль (власниця свідомо відхилила поетапний план
# 970->2000->4000->6000). Репрайсер (prom_competitor_pricer.py) обробляє
# SELECT_COUNT ротаційними партіями по ROTATION_BATCH_SIZE щочотири
# години (не все одразу раз на добу) — див. коментар там же щодо чому.
#
# ВИПРАВЛЕНО (2026-07-24, пряма поправка власниці, живий скріншот
# кабінету Prom — "Додано 711/6000 товарів... 0/30000 різновидів"):
# колишній SAFETY_BUFFER=30 ґрунтувався на хибному припущенні, що Prom
# рахує різновиди в той самий ліміт, що й самі товари. На підписці 6000
# це ДВА окремі, незалежні ліміти (6000 товарів, 30000 різновидів) —
# буфер, зроблений з цієї причини, тут не потрібен. SELECT_COUNT = TARGET_
# COUNT напряму, без віднімання.
TARGET_COUNT    = 6000
SELECT_COUNT    = TARGET_COUNT

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
# ДОДАНО (2026-08-12, «виключи товар» — рішення власника): «Термометри та аспіратори»
# — Toysi-категорія-мішанка (термометри + аспіратори + стопоміри, дитяча гігієна/здоров'я,
# 31 SKU), яка НЕ має однієї чистої відповідності в дереві категорій Prom, тому єдиний її
# товар у кабінеті падав у заглушку «Товари, загальне» (id 29, 25% — найвища комісія). Це
# був ОСТАННІЙ товар у заглушці (1 з 5864, живий зріз кабінету 2026-08-12). Виключаємо всю
# категорію (як «велосипеди») замість форсувати неточне зіставлення грабаг-категорії →
# заглушка тепер порожня. Прибирає ~30 наявних SKU дитячої гігієни (не-іграшки, маргінальні
# для іграшкового магазину) — за потреби повернути: прибрати рядок і додати куровану Prom-
# категорію в generate_prom_feed.TOYSI_TO_PROM_CATEGORY.
EXCLUDED_CATEGORIES = {"велосипеди", "термометри та аспіратори"}


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


FEED_PATH = Path(__file__).parent / OUTPUT_FILE


def load_feed_membership() -> set:
    """external_id-и з поточного prom_feed_top.xml = хто ВЖЕ у фіді (і, з лагом, на Prom).
    Це основа СТІЙКОГО membership (стоп-churn). Відсутній/порожній файл → порожня множина:
    ПЕРШИЙ прогін (чи чистий чекаут) = звичайний топ-відбір за маржею, з якого membership і
    починається. Читаємо ДО того, як generate_feed() перезапише файл цим же прогоном."""
    try:
        xml = FEED_PATH.read_text(encoding="utf-8")
    except OSError:
        return set()
    return set(re.findall(r'<offer id="([^"]+)"', xml))


def _rank_pool(pool: dict, margins: dict, scan_state: dict) -> list:
    """Ранжує кошик кандидатів існуючою логікою: спершу категорії-лідери (за маржею),
    тоді ПРОСКАНОВАНІ (підтверджена конкурентна маржа, за маржею), тоді непросканові
    (наївна оцінка, за маржею). Див. довгий коментар в історії select_top_items щодо
    ЧОМУ просканований прибуток має стояти вище непідтвердженого здогаду."""
    leaders = {pid: it for pid, it in pool.items() if is_leader_category(it)}
    rest    = {pid: it for pid, it in pool.items() if pid not in leaders}
    leaders_sorted = sorted(leaders.items(), key=lambda kv: margins[kv[0]], reverse=True)
    rest_scanned   = sorted(((p, i) for p, i in rest.items() if p in scan_state),
                            key=lambda kv: margins[kv[0]], reverse=True)
    rest_unscanned = sorted(((p, i) for p, i in rest.items() if p not in scan_state),
                            key=lambda kv: margins[kv[0]], reverse=True)
    return leaders_sorted + rest_scanned + rest_unscanned


def select_top_items(catalog: dict, target: int = SELECT_COUNT, sticky: bool = True) -> dict:
    """Курований набір ~`target` (6000) SKU для Prom (і, через спільний виклик, для
    репрайсера, catalog_sync та рекламних фідів — усі бачать ОДИН набір).

    СТІЙКИЙ MEMBERSHIP (2026-08-17, жива діагностика: фід churn'ився ~46%/прогін —
    топ-6000 за маржею перетасовувався щоразу, бо скан прогресивно міняв маржу товару
    з наївної на реальну → міняв ранг. Prom не встигав: 2758 товарів фіду не створені,
    2268 живих, що випали, catalog_sync гасив у OOS → стабільно живими лишалось лише 2620
    з 6000). Лік — той самий membership-принцип, що вже прийнято для Rozetka: хто ВЖЕ у
    фіді Й ДОСІ якісний (в наявності, прибутковий, не delisted, не виключена категорія) —
    ЛИШАЄТЬСЯ; вільні до `target` слоти добираються найкращими НОВИМИ кандидатами. Так
    набір перестає перетасовуватись, catalog_sync не гасить те, що фід тримає, і каталог
    Prom сходиться до 6000. Товар ЛИШАЄ набір лише коли РЕАЛЬНО перестав бути якісним
    (OOS/збитковий/delisted → _margin()=-1 → випадає з eligible), не через дрібну різницю
    маржі. `sticky=False` — чистий топ-відбір без пам'яті (для аналітики/першого засіву).

    Ранжування кандидатів (і членів, і нових) — існуюче двоетапне: лідери → просканований
    прибуток → непросканований здогад (див. _rank_pool + історичний коментар нижче). Маржа
    рахується ОДИН раз на товар; враховує scan_state (реальна конкурентна маржа для
    просканованих) і delisted_pids (підтверджено видалені репрайсером — _margin()=-1).

    ІСТОРІЯ (двоетапне ранжування, ВИПРАВЛЕНО 2026-07-17 pt18): пряме сортування всього
    "решта"-кошика за грошовою маржею ставило непідтверджені здогадки (наївна формула,
    множник 1.75×) вище доведеного прибутку (просканована маржа, обмежена ~3% floor) —
    2925 із 3016 просканованих ≥3% витіснялись здогадами. Тому просканований прибуток
    ранжується ПЕРЕД непросканованим, а не програє йому за номіналом.
    """
    scan_state = load_scan_state()
    delisted_pids = load_delisted_pids()
    margins = {pid: _margin(item, pid, scan_state, delisted_pids) for pid, item in catalog.items()}

    eligible = {
        pid: item for pid, item in catalog.items()
        if margins[pid] >= 0 and not is_excluded_category(item)
    }

    # СТІЙКИЙ membership: члени = ті, хто вже у фіді Й досі eligible. Тримаємо їх; вільні
    # слоти добираємо найкращими новими. sticky=False → member_ids порожні → чистий топ.
    member_ids = (load_feed_membership() & set(eligible)) if sticky else set()
    members_pool = {pid: eligible[pid] for pid in member_ids}
    new_pool     = {pid: it for pid, it in eligible.items() if pid not in member_ids}

    members_ranked = _rank_pool(members_pool, margins, scan_state)
    new_ranked     = _rank_pool(new_pool, margins, scan_state)

    if len(members_ranked) >= target:
        # Усі слоти зайняті чинними членами — евіктимо лише найнижчий надлишок (рідко:
        # лише коли ВСІ ~6000 попередніх членів досі якісні). Стабільність збережено.
        selected = members_ranked[:target]
    else:
        selected = members_ranked + new_ranked[:target - len(members_ranked)]

    if sticky:
        kept = len(members_ranked[:target]) if len(members_ranked) >= target else len(members_ranked)
        backfilled = max(0, len(selected) - kept)
        print(f"[Prom Top] СТІЙКИЙ membership: лишено з попереднього фіду {kept}, "
              f"добрано нових {backfilled} (з {len(eligible)} придатних; ціль {target}). "
              f"Churn стабілізовано — catalog_sync не гаситиме утриманих.")

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

    # Журнал «штовхнутих у Prom» (prom_pushed_ledger.py, 2026-08-11): фіксуємо КОЖЕН
    # external_id, який іде в prom_feed_top.xml, щоб prom_catalog_sync міг АВТОМАТИЧНО
    # знаходити й чистити OOS-мотлох у «невидимих групах» (яких /groups/list не віддає),
    # без ручної сесії кабінету (Phase 3). Товари, що випадуть з топ-6000 наступними
    # прогонами, лишаться в журналі як кандидати на звірку/деактивацію. Best-effort:
    # збій запису журналу не має ламати генерацію самого фіду.
    try:
        from prom_pushed_ledger import record_pushed
        size = record_pushed(top_catalog.keys())
        print(f"[Prom Top] Журнал штовхнутих у Prom: {size} external_id (для авто-чистки невидимих OOS).")
    except Exception as e:  # noqa: BLE001 — журнал допоміжний, ніколи не блокує фід
        print(f"[Prom Top] Попередження: не вдалось оновити журнал штовхнутих ({e}).", file=__import__("sys").stderr)

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
    # ВИПРАВЛЕНО (2026-07-25, живий root-cause "не росте кабінет"): лінивий
    # імпорт (той самий принцип, що вже є для resolve_own_product_links у
    # prom_competitor_pricer.py) — на рівні модуля утворив би циклічний
    # імпорт, бо prom_competitor_pricer.py вже імпортує з цього файлу
    # (select_top_items/load_scan_state).
    from prom_competitor_pricer import _load_prom_category_cache
    # SEO-описи (задача Cowork найновіше-18/20, пілот 30 затверджено власником): approved
    # SEO-опис ПОВНІСТЮ замінює сирий опис Toysi для свого SKU (той самий desc_override-механізм
    # _build_xml, Vis-9), фолбек на Toysi для решти → поступова безпечна розкатка по SKU.
    # Prom першим (за домовленістю). Порожньо/помилка → сирі описи (нічого не ламає).
    from seo_content_db import load_approved_prom_overrides
    generate_feed(
        output_file=output_file,
        catalog=top_catalog,
        price_overrides=load_fresh_prom_price_overrides(),
        prom_category_cache=_load_prom_category_cache(),
        description_overrides=load_approved_prom_overrides(),
        # ПОВНИЙ каталог (не лише топ-6000) для виведення Toysi→Prom категорій:
        # кеш містить Prom-категорії всіх ~5836 імпортованих SKU, більшість з яких
        # через ротацію зараз поза топ-6000 — derive по повному каталогу дає їм усім
        # шанс стати fallback-джерелом і різко зменшує «Товари, общее» 25% (~3190).
        full_catalog=catalog,
    )


if __name__ == "__main__":
    generate_top_feed()
