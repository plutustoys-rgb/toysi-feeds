"""Опис товару для ВСІХ фідів — спільна точка, щоб КОЖЕН товар у КОЖНОМУ фіді (Prom/EVA/ALLO/Rozetka)
мав нормальний опис, включно з НОВИМИ товарами, що постійно підтягуються з Toysi.

Пріоритет:
1. Затверджений override (`desc_override["description"]`) — рукописний золотий пілот або ручний
   акційний набір (`description_overrides.json`). Найвищий пріоритет.
2. Інакше — згенерований НА ЛЬОТУ `seo_content_generator.build_seo(item)` (шаблонний генератор,
   СТИЛЬ затверджено власником 2026-08-13). Будується з живих даних товару (назва+категорія+
   користь+бренд+країна+розмір) — тому автоматично покриває будь-який SKU будь-якого фіду й
   свіжі товари, БЕЗ статичного per-каталог батч-файлу (який був би прив'язаний до Prom-відбору
   й застарівав би на нових товарах — пряме зауваження власника 2026-08-31).
3. Фолбек на сирий опис Toysi лише якщо build_seo впав/порожній (best-effort, фід не валиться).

`build_seo` імпортується ЛІНИВО (у функції), щоб не тягти module-level залежності
`seo_content_generator` (select_top_items тощо) у фід-генератори й не ризикувати циклічним імпортом.
"""


def description_for(item: dict, desc_override: dict = None) -> str:
    """Сирий опис (override / згенерований / Toysi) — санітизацію/обрізку робить сам генератор фіду."""
    if desc_override and (desc_override.get("description") or "").strip():
        return desc_override["description"]
    try:
        from seo_content_generator import build_seo
        generated = (build_seo(item).get("seo_long_html") or "").strip()
    except Exception:  # noqa: BLE001 — опис best-effort, збій генератора не валить фід
        generated = ""
    return generated or (item.get("description", "") or "")
