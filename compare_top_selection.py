"""
compare_top_selection.py — одноразовий скрипт: порівнює ПОТОЧНИЙ відбір
топ-970 (лише за маржею) з НОВИМ (фільтр за маржею + сортування за
популярністю категорії через Google Trends). Не частина продакшн-пайплайну
— діагностика для звіту власнику перед рішенням, чи вмикати popularity
на постійній основі (select_top_items() лишається opt-in, дефолт без змін).

Запуск:
    python compare_top_selection.py
"""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from parser import fetch_toysi_catalog
from generate_prom_feed_top import select_top_items, SELECT_COUNT, _margin, is_excluded_category
from category_popularity import fetch_category_popularity, get_popularity_score


def main() -> None:
    print("[Compare] Завантажуємо каталог Toysi...")
    catalog = fetch_toysi_catalog()

    eligible_categories = sorted({
        item.get("category_name", "").strip()
        for item in catalog.values()
        if _margin(item) >= 0 and not is_excluded_category(item) and item.get("category_name", "").strip()
    })
    popularity_scores = fetch_category_popularity(eligible_categories)  # використає кеш, якщо свіжий

    old_selection = select_top_items(catalog)  # дефолт -- лише маржа, як зараз у продакшні
    new_selection = select_top_items(catalog, popularity_scores=popularity_scores)

    old_ids = set(old_selection)
    new_ids = set(new_selection)

    removed = old_ids - new_ids
    added   = new_ids - old_ids
    kept    = old_ids & new_ids

    print(f"\n[Compare] Топ-{SELECT_COUNT}: спільних {len(kept)}, "
          f"видалено {len(removed)}, додано {len(added)} "
          f"({len(removed)/SELECT_COUNT*100:.1f}% складу змінилось)")

    # Розподіл категорій у НОВОМУ наборі, які потрапили ЗАВДЯКИ популярності
    # (тобто категорія має score вище медіани і представлена сильніше, ніж
    # у старому наборі, за кількістю позицій)
    def category_counts(selection):
        counts = {}
        for item in selection.values():
            cat = item.get("category_name", "").strip()
            counts[cat] = counts.get(cat, 0) + 1
        return counts

    old_counts = category_counts(old_selection)
    new_counts = category_counts(new_selection)
    all_cats = sorted(set(old_counts) | set(new_counts))

    print("\n[Compare] Категорії з найбільшою зміною представленості (нове - старе):")
    diffs = sorted(
        ((new_counts.get(c, 0) - old_counts.get(c, 0), c) for c in all_cats),
        key=lambda x: -abs(x[0]),
    )
    for delta, cat in diffs[:20]:
        score = popularity_scores.get(cat, get_popularity_score(cat, popularity_scores))
        sign = "+" if delta > 0 else ""
        print(f"  {sign}{delta:4d}  {cat[:40]:40s} (popularity={score:8.2f}, "
              f"було {old_counts.get(cat,0)}, стало {new_counts.get(cat,0)})")

    # Кілька конкретних прикладів товарів, що змінили статус
    print(f"\n[Compare] Приклади видалених (втратили місце) — перші 10 з {len(removed)}:")
    for pid in list(removed)[:10]:
        item = old_selection[pid]
        cat = item.get("category_name", "").strip()
        print(f"  {pid}\t{item.get('name','')[:40]:40s}\tкатегорія={cat[:30]:30s}\tmargin={_margin(item):.0f}\tpopularity={get_popularity_score(cat, popularity_scores):.2f}")

    print(f"\n[Compare] Приклади доданих (нові в топ-970) — перші 10 з {len(added)}:")
    for pid in list(added)[:10]:
        item = new_selection[pid]
        cat = item.get("category_name", "").strip()
        print(f"  {pid}\t{item.get('name','')[:40]:40s}\tкатегорія={cat[:30]:30s}\tmargin={_margin(item):.0f}\tpopularity={get_popularity_score(cat, popularity_scores):.2f}")


if __name__ == "__main__":
    main()
