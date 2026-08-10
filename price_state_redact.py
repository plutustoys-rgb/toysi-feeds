"""price_state_redact.py — вирізає чутливі фінполя з
prom_competitor_price_state.json ПЕРЕД публікацією в ПУБЛІЧНУ гілку feed-data.

ЧОМУ (витік 2026-08-10): `feed-data` — гілка публічного репо (мусить лишатись
публічною, бо Google/Meta тягнуть фіди без токена). Тож `cost` (наша
собівартість), `margin_pct` (наша маржа) і `_meta.last_avg_margin_pct` (середня
маржа) для ~9308 SKU читались будь-ким за raw-URL — той самий клас, що KODV
(PR #240) і видалені гілки scan-state-data / catalog-sync-delisted-data (PR #241).
Просте видалення файлу неможливе: споживачі feed-data-копії
(`generate_prom_feed_top.py` у CI, `apply_live_dumping_fix.py`) читають її назад.
Але вони беруть ЛИШЕ `_delisted_since` + `price`/`competitor_price`/`competitor_alive`
(наша ціна й ціна конкурента й так публічні — видно на Prom); `cost`/`margin_pct`
їм не потрібні (`cost` завжди рахується заново через `real_toysi_cost`). Тож
вирізаємо саме ці поля, решту лишаємо — пайплайн не змінює поведінки.

ВАЖЛИВО: редагується ЛИШЕ копія, що публікується. Локальний
`/opt/plutustoys/prom_competitor_price_state.json` (джерело правди репрайсера)
лишається ПОВНИМ — redact_price_state() не мутує вхід, повертає нову копію.
"""
import json
import sys

# Поля запису одного SKU, які НЕ публікуємо (наша фінансова таємниця).
SENSITIVE_ENTRY_FIELDS = ("cost", "margin_pct")
# Поля службового _meta, які НЕ публікуємо.
SENSITIVE_META_FIELDS = ("last_avg_margin_pct",)


def redact_price_state(state: dict) -> dict:
    """Повертає НОВУ копію state без чутливих полів (cost/margin_pct у кожному
    SKU, last_avg_margin_pct у _meta). Вхід не мутується. Службовий ключ
    `_delisted_since` та все інше (price, competitor_price, competitor_alive,
    price_category, scanned_at, name, …) зберігаються без змін."""
    out = {}
    for key, val in state.items():
        if key == "_delisted_since":
            out[key] = val
        elif key == "_meta" and isinstance(val, dict):
            out[key] = {k: v for k, v in val.items() if k not in SENSITIVE_META_FIELDS}
        elif isinstance(val, dict):
            out[key] = {k: v for k, v in val.items() if k not in SENSITIVE_ENTRY_FIELDS}
        else:
            out[key] = val
    return out


if __name__ == "__main__":
    # CLI: читає файл-аргумент, друкує redacted JSON у stdout (для bash-публікаторів).
    with open(sys.argv[1], encoding="utf-8") as _f:
        _state = json.load(_f)
    json.dump(redact_price_state(_state), sys.stdout, ensure_ascii=False, indent=1)
