import re

from parser import fetch_toysi_catalog
from generate_prom_feed import calc_price, generate_feed, is_clearance_item, MIN_SUPPLIER_PRICE

OUTPUT_FILE     = "feeds/prom_feed_top.xml"
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


def _margin(item: dict) -> float:
    """Розрахункова маржа (retail - cost). -1, якщо товар не має валідної/прийнятної ціни
    або це уцінений/пошкоджений товар (не належить у "топ" незалежно від маржі)."""
    if is_clearance_item(item.get("name"), item.get("category_name")):
        return -1
    try:
        cost = float(item.get("price") or 0)
    except (TypeError, ValueError):
        return -1
    if cost < MIN_SUPPLIER_PRICE:
        return -1
    return calc_price(cost) - cost


def select_top_items(catalog: dict, target: int = SELECT_COUNT) -> dict:
    """
    1. Спочатку — товари з категорій-лідерів (LEADER_KEYWORD_GROUPS).
       Якщо їх більше за target — сортуємо за маржею і беремо top `target`.
    2. Якщо лідерів менше за target — доповнюємо рештою каталогу,
       теж за спаданням маржі, поки не набереться `target`.
    """
    eligible = {pid: item for pid, item in catalog.items() if _margin(item) >= 0}

    leaders = {pid: item for pid, item in eligible.items() if is_leader_category(item)}
    rest    = {pid: item for pid, item in eligible.items() if pid not in leaders}

    leaders_sorted = sorted(leaders.items(), key=lambda kv: _margin(kv[1]), reverse=True)
    rest_sorted    = sorted(rest.items(), key=lambda kv: _margin(kv[1]), reverse=True)

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

    generate_feed(output_file=output_file, catalog=top_catalog)


if __name__ == "__main__":
    generate_top_feed()
