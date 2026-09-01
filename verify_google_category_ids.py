"""verify_google_category_ids.py — доводить, що всі google_product_category ID у нашому Google-фіді
є ВАЛІДНИМИ листками офіційної таксономії Google (google_product_taxonomy.txt).

ЧОМУ (2026-09-01, та сама дисципліна, що для EVA): Google вимагає валідні значення
`google_product_category`. `generate_google_feed.py` тримає хардкод-мапу `_CATEGORY_RULES`
(keyword → Google-ID), «звірену з офіційним файлом» ОДНОГО разу (2026-07-11), але:
  (1) офіційна таксономія НЕ була збережена в репо → нема з чим звіряти надалі;
  (2) немає автоперевірки → якщо Google оновить таксономію й задепрекейтить ID (він це робить),
      фід тихо віддаватиме невалідний ID → товари відхиляються, а ми дізнаємось випадково.

Ця перевірка звіряє КОЖЕН ID у `_CATEGORY_RULES` + fallback з довідником. exit 1 при невалідному.
Довідник поновлюється завантаженням taxonomy-with-ids.en-US.txt (ID мовно-незалежні).

ЗАПУСК: python verify_google_category_ids.py   (0 = усі ID валідні; 1 = є невалідні)
"""
import os
import re
import sys
from pathlib import Path

BASE = Path(__file__).parent
TAXONOMY = BASE / "google_product_taxonomy.txt"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_taxonomy() -> dict:
    """{google_id: full_path} з офіційного файлу (рядки 'ID - A > B > C', # = коментар)."""
    out = {}
    with TAXONOMY.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^(\d+)\s+-\s+(.+)$", line)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def main() -> int:
    if not TAXONOMY.exists() or TAXONOMY.stat().st_size == 0:
        print(f"[verify-google] НЕМАЄ довідника {TAXONOMY.name} — нема з чим звіряти.", file=sys.stderr)
        return 2
    valid = load_taxonomy()
    print(f"[verify-google] Листків таксономії Google у довіднику: {len(valid)}")

    # Беремо ID напряму з генератора (джерело правди), а не парсимо текст.
    from generate_google_feed import _CATEGORY_RULES, GOOGLE_CATEGORY_FALLBACK
    rule_ids = [cid for _kw, cid in _CATEGORY_RULES] + [GOOGLE_CATEGORY_FALLBACK]
    uniq = sorted(set(rule_ids))

    bad = [i for i in uniq if i not in valid]
    print(f"[verify-google] ID у _CATEGORY_RULES+fallback: {len(uniq)} | ВАЛІДНИХ: {len(uniq) - len(bad)} | "
          f"НЕВАЛІДНИХ: {len(bad)}")
    for i in bad:
        print(f"   ❌ {i} — немає в таксономії Google (задепрекейчено або друкарська помилка)")

    if bad:
        print("[verify-google] РЕЗУЛЬТАТ: ❌ фід віддає невалідні google_product_category — товари відхиляться.")
        if os.environ.get("AUDIT_NO_TELEGRAM") != "1":
            try:
                from telegram_notify import send_telegram_message
                send_telegram_message(
                    f"🟢 Google-фід: {len(bad)} невалідних google_product_category (напр. {bad[0]}) — "
                    "таксономія Google змінилась. Товари відхиляться. verify_google_category_ids.py")
            except Exception:  # noqa: BLE001 — best-effort
                pass
        return 1
    print("[verify-google] РЕЗУЛЬТАТ: ✅ усі google_product_category ID — валідні листки таксономії Google.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
