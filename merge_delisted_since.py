"""merge_delisted_since.py — об'єднує _delisted_since з двох незалежних
джерел реального видалення в Prom (prom_competitor_pricer.py::delist(),
GH Actions, і prom_catalog_sync.py::deactivate(), VPS-таймер, окремий
локальний файл стану) в один prom_competitor_price_state.json ПЕРЕД
select_top_items(). Викликається з update-feeds.yml.

ДОДАНО (2026-07-24, живий root-cause: Prom import падав на "Поле status:
Позиція недоступна для оновлення" для 179 SKU — реально й назавжди
видалені через prom_catalog_sync.py::deactivate(), окремий шлях, який
НІКОЛИ не писав _delisted_since, тож select_top_items() тихо пропонував
їх знову)."""
import json
import sys

MAIN_FILE = "prom_competitor_price_state.json"
SYNC_FILE = "/tmp/catalog_sync_delisted.json"

with open(MAIN_FILE, encoding="utf-8") as f:
    main_state = json.load(f)
with open(SYNC_FILE, encoding="utf-8") as f:
    sync_state = json.load(f)

merged = dict(main_state.get("_delisted_since", {}))
merged.update(sync_state.get("_delisted_since", {}))
main_state["_delisted_since"] = merged

with open(MAIN_FILE, "w", encoding="utf-8") as f:
    json.dump(main_state, f, ensure_ascii=False, indent=1)

print(f"[Merge] _delisted_since: {len(merged)} записів після злиття.", file=sys.stderr)
