"""
audit_prom_characteristics.py — перевіряє, скільки SKU з відбору "топ-970"
(той самий відбір, що generate_prom_feed_top.py) не мають даних для
характеристик, які Prom може вимагати обов'язково для показу товару в
каталозі (Виробник/Країна/Тип/Вікова група тощо).

КОНТЕКСТ: на 4 тестових товарах 2 з 4 не з'явились у каталозі Prom попри
статус "Опубліковано" — відома поведінка Prom: товар БЕЗ обов'язкових
характеристик категорії приймається фідом, але виключається з перегляду й
пошуку категорії, поки характеристики не заповнені. generate_prom_feed.py
передає лише те, що вже є у сирому фіді Toysi (vendor/country з тегів,
params — довільний список без жодної мапи на конкретні Prom-характеристики)
— нічого не синтезує і не валідує.

Перевірено на повному каталозі Toysi (29192 SKU, 2026-07-09): УСЬОГО 6
унікальних назв <param> в усьому фіді, і всі 6 — розміри упаковки/товару
(см). Тобто "Тип"/"Вікова група" ніде в фіді Toysi взагалі не існують — це
не помилка мапінгу в нашому коді, це відсутність даних у джерела. Виробник
є завжди (0% пропусків на повному каталозі), Країна відсутня приблизно на
30% каталогу.

Цей скрипт НЕ виправляє проблему (вигадувати "Вікова група" для товару без
джерела даних — гірше, ніж лишити порожнім), а рахує масштаб по категоріях
на КОНКРЕТНО тих SKU, які підуть у Prom — щоб рішення "які категорії
донаповнити вручну в кабінеті Prom перед імпортом" приймалось не наосліп.

Запуск:
    python audit_prom_characteristics.py
"""

import sys
from collections import defaultdict

from generate_prom_feed_top import select_top_items
from parser import fetch_toysi_catalog

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Єдині param-и, які реально трапляються у фіді Toysi (виміряно на повному
# каталозі, 2026-07-09) — суто розміри, не характеристики товару. Усе, що
# НЕ входить сюди, вважаємо змістовною характеристикою (на випадок, якщо
# Toysi колись додасть щось на кшталт "Вікова група" в param).
_DIMENSION_PARAM_NAMES = {
    "довжина в упаковці (см)", "ширина в упаковці (см)", "висота в упаковці (см)",
    "довжина без упаковки (см)", "ширина без упаковки (см)", "висота без упаковки (см)",
}


def _has_non_dimension_param(item: dict) -> bool:
    return any(
        name.strip().lower() not in _DIMENSION_PARAM_NAMES
        for name, _ in item.get("params", [])
    )


def audit(catalog: dict) -> dict:
    """{category_name: {"total", "missing_country", "no_characteristics"}}"""
    by_category = defaultdict(lambda: {"total": 0, "missing_country": 0, "no_characteristics": 0})
    for item in catalog.values():
        cat = item.get("category_name") or item.get("category_id") or "(без категорії)"
        row = by_category[cat]
        row["total"] += 1
        if not (item.get("country") or "").strip():
            row["missing_country"] += 1
        if not _has_non_dimension_param(item):
            row["no_characteristics"] += 1
    return dict(by_category)


def main() -> None:
    print("[Audit] Завантажуємо каталог Toysi...")
    catalog = fetch_toysi_catalog()
    if not catalog:
        print("[Audit] Каталог порожній — перевірку не виконано.")
        return

    top_catalog = select_top_items(catalog)
    print(f"[Audit] Перевіряємо {len(top_catalog)} SKU (той самий відбір, що generate_prom_feed_top.py)\n")

    by_category = audit(top_catalog)
    total_missing_country = sum(r["missing_country"] for r in by_category.values())
    total_no_char = sum(r["no_characteristics"] for r in by_category.values())

    print(f"Усього: {len(top_catalog)} SKU")
    print(f"  без країни походження: {total_missing_country} ({total_missing_country / len(top_catalog) * 100:.1f}%)")
    print(
        "  без ЖОДНОЇ змістовної характеристики (лише розміри або нічого): "
        f"{total_no_char} ({total_no_char / len(top_catalog) * 100:.1f}%)"
    )

    print("\nПо категоріях (відсортовано за кількістю SKU без країни, спадаюче):")
    rows = sorted(by_category.items(), key=lambda kv: kv[1]["missing_country"], reverse=True)
    for cat, r in rows:
        if r["missing_country"] == 0 and r["no_characteristics"] == 0:
            continue
        print(
            f"  {cat}: {r['total']} SKU, без країни {r['missing_country']}, "
            f"без характеристик {r['no_characteristics']}"
        )

    print(
        "\n⚠️ 'Тип'/'Вікова група' (та будь-яка НЕ-розмірна характеристика) відсутні у "
        "фіді Toysi для ВСІХ SKU (перевірено на повному каталозі) — генератор фіду не "
        "може їх заповнити, бо джерело даних просто їх не надає. Якщо категорія Prom "
        "вимагає ці поля обов'язково для показу в каталозі — товари цієї категорії "
        "потрібно донаповнити вручну в кабінеті Prom (або уточнити в Toysi, чи є ці дані)."
    )


if __name__ == "__main__":
    main()
