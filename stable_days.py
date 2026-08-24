"""stable_days.py — прапорець «стабільності» товару з денних знімків каталогу (спільна заявка SEO+SMM 2026-08-23).

ЗАДАЧА (обидва агенти незалежно вийшли на одне): і SMM («що НЕ зникає»), і SEO («ядро × запас»)
обирають, куди вкладати роботу (рілси / описи / кандидати Rozetka), за стабільністю позиції.
Рахували вручну по шести файлах щоразу. Цей модуль рахує ОДИН раз у денному прогоні.

ВИЗНАЧЕННЯ (узгоджене в каналі): `stable_days` = ПОТОЧНА серія поспіль (від НАЙСВІЖІШОГО знімка
назад), протягом якої товар БУВ ПРИСУТНІЙ І `presence=="avail"`. Саме поточна серія, не max за всю
історію — щоб один випадковий out-день обнуляв рахунок (товар зник → серія почнеться заново). Товар,
якого нема / не-avail у найсвіжішому знімку → `stable_days=0`.
Одиниця = денний знімок (`reports/prom_catalog_history/YYYY-MM-DD.json`, один файл на добу), тож
`stable_days` ≈ «днів поспіль у наявності». Максимум обмежений глибиною історії (скільки знімків є).

Читає ЛИШЕ знімки історії — жодних живих джерел, тож безпечно кликати в кінці денного прогону
(`prom_cabinet_catalog.summary`). Пише `reports/stable_days.csv`, який SEO/SMM фільтрують
(їхнє спільне правило добору: `stable_days >= K AND quantity_in_stock > 2`).
"""
import csv
import glob
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(BASE_DIR, "reports", "prom_catalog_history")
OUT_CSV = os.path.join(BASE_DIR, "reports", "stable_days.csv")


def _snapshot_files(history_dir: str = HISTORY_DIR) -> list:
    """Шляхи знімків, відсортовані за днем ЗРОСТАННЯ (найстаріший → найновіший).
    Сортування за іменем YYYY-MM-DD — лексикографічне = хронологічне для ISO-дат."""
    return sorted(glob.glob(os.path.join(history_dir, "*.json")))


def _load_items(path: str):
    """`items`-словник одного знімка або None (битий/без items). Не тримаємо весь ряд у пам'яті —
    викликач вантажить по ОДНОМУ файлу за раз (пік ≈ один знімок, незалежно від глибини історії)."""
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    items = d.get("items")
    return items if isinstance(items, dict) else None


def _is_avail(item) -> bool:
    return isinstance(item, dict) and item.get("presence") == "avail"


def compute_stable_days(history_dir: str = HISTORY_DIR, newest_items: dict = None,
                        files: list = None) -> dict:
    """{sku: поточна_серія_поспіль_present+avail_від_найсвіжішого_знімка}.

    Лише SKU, present+avail у НАЙСВІЖІШОМУ знімку, мають значення ≥1; решта відсутні у результаті
    (тобто stable_days=0). СТРІМІНГ: старіші знімки вантажаться по одному й одразу звільняються —
    пік пам'яті ≈ один знімок, а не вся історія (важливо, бо історія росте без обмежень).
    `newest_items`/`files` можна передати ззовні (напр. з `refresh`), щоб не читати найсвіжіший двічі."""
    if files is None:
        files = _snapshot_files(history_dir)
    if not files:
        return {}
    if newest_items is None:
        newest_items = _load_items(files[-1]) or {}
    stable = {}
    active = set()
    for sku, item in newest_items.items():
        if _is_avail(item):
            stable[sku] = 1
            active.add(sku)
    # рух назад по старіших знімках, ПО ОДНОМУ, поки серія тримається
    for path in reversed(files[:-1]):
        if not active:
            break
        items = _load_items(path) or {}
        still = set()
        for sku in active:
            if _is_avail(items.get(sku)):
                stable[sku] += 1
                still.add(sku)
        active = still
    return stable


def refresh(history_dir: str = HISTORY_DIR, out_csv: str = OUT_CSV) -> str:
    """Рахує stable_days і пише CSV (sku, stable_days, presence_now, quantity_in_stock_now,
    price_now, view_catalog_url) по товарах НАЙСВІЖІШОГО знімка, сорт. за stable_days↓, потім запас↓.
    Повертає шлях до CSV. Порожня історія → CSV лише із заголовком (не падає)."""
    files = _snapshot_files(history_dir)
    newest_items = (_load_items(files[-1]) or {}) if files else {}
    # передаємо вже завантажений найсвіжіший + список файлів — щоб не читати його вдруге
    stable = compute_stable_days(history_dir, newest_items=newest_items, files=files)
    rows = []
    for sku, item in newest_items.items():
        if not isinstance(item, dict):
            continue
        rows.append({
            "sku": sku,
            "stable_days": stable.get(sku, 0),
            "presence_now": item.get("presence", ""),
            "quantity_in_stock_now": item.get("quantity_in_stock", ""),
            "price_now": item.get("price", ""),
            "view_catalog_url": item.get("view_catalog_url", ""),
        })
    rows.sort(key=lambda r: (-r["stable_days"],
                             -(r["quantity_in_stock_now"] if isinstance(r["quantity_in_stock_now"], (int, float)) else 0)))
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["sku", "stable_days", "presence_now",
                                          "quantity_in_stock_now", "price_now", "view_catalog_url"])
        w.writeheader()
        w.writerows(rows)
    return out_csv


if __name__ == "__main__":
    path = refresh()
    files = _snapshot_files()
    print(f"[stable_days] знімків в історії: {len(files)} -> {path}")
    if files:
        st = compute_stable_days()
        depth = len(files)
        full = sum(1 for v in st.values() if v == depth)
        print(f"[stable_days] present+avail у найсвіжішому: {len(st)}; "
              f"стабільних усю глибину ({depth} знім.): {full}")
