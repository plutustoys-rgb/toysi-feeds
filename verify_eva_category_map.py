"""verify_eva_category_map.py — доводить, що зіставлення категорій EVA ВІДПОВІДАЄ офіційному
дереву EVA, а не лише «є в нашій мапі».

ЧОМУ (2026-09-01, після 3-го повернення до цієї задачі): «зіставлення категорій EVA» тричі
поверталось, бо:
  (1) офіційний довідник дерева EVA (Google-таблиця «Категорії Eva Маркетплейс», gid=0) НЕ був
      збережений у репо → щоразу брали наново;
  (2) «100% зіставлено» МІРЯЛИ як «категорія Є в eva_category_map.json», а НЕ як «EVA впізнає її
      назву+id» → хибний «розвʼязано» маскував реальність, і кожна сесія перевідкривала проблему.

EVA заповнює категорію товару, ЛИШЕ якщо фід віддає ТОЧНУ EVA-назву листка (+ його BTK_id) —
у кабінеті самообслуговування зіставлення НЕМАЄ (eva.md §«Мапінг ... робить САМА EVA»). Тому
єдиний доказ «зроблено» — кожна емітована категорія фіда є валідним листком офіційного дерева.

Ця перевірка звіряє:
  • КОЖЕН EVA-таргет у eva_category_map.json → чи (назва, id) = офіційний листок;
  • КОЖНУ <category> в опублікованому фіді (якщо є feeds/eva_feed.xml) → валідний листок чи фолбек.
Гучно падає (exit 1) при будь-якій розбіжності таргета мапи. Фід-фолбеки друкує як ⚠ (це
не-зіставлені категорії; мають бути або зіставлені, або виключені у EVA_EXCLUDED_CATEGORIES).

ЗАПУСК: python verify_eva_category_map.py   (0 = мапа чиста; 1 = є розбіжності)
"""
import csv
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).parent
REFERENCE_CSV = BASE / "eva_category_reference.csv"   # офіційне дерево EVA (gid=0), збережене в репо
MAP_JSON = BASE / "eva_category_map.json"
FEED_XML = BASE / "feeds" / "eva_feed.xml"            # опційно — якщо згенеровано локально

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_reference() -> dict:
    """{leaf_name: btk_id} з офіційної таблиці EVA. Колонки (позиційно, як у gid=0):
    0=Розташування, 1=Категорія останнього рівня (назва листка), 2=БТК_id."""
    leaves = {}
    with REFERENCE_CSV.open(encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)  # заголовок
        for row in r:
            if len(row) < 3:
                continue
            leaf, bid = row[1].strip(), row[2].strip()
            if leaf and bid:
                leaves[leaf] = bid
    return leaves


def main() -> int:
    if not REFERENCE_CSV.exists():
        print(f"[verify-eva-cats] НЕМАЄ довідника {REFERENCE_CSV.name} — нема з чим звіряти.", file=sys.stderr)
        return 2
    leaves = load_reference()
    print(f"[verify-eva-cats] Офіційних листків EVA у довіднику: {len(leaves)}")

    cat_map = json.loads(MAP_JSON.read_text(encoding="utf-8"))
    # унікальні EVA-таргети (id, name), у які мапляться наші Toysi-категорії
    targets = {}
    for tid, ev in cat_map.items():
        if isinstance(ev, dict) and str(ev.get("id") or "").strip():
            targets[(str(ev["id"]).strip(), str(ev.get("name") or "").strip())] = None

    bad = []
    for eid, name in sorted(targets, key=lambda x: x[1]):
        if name not in leaves:
            bad.append((eid, name, "назви немає серед листків EVA"))
        elif leaves[name] != eid:
            bad.append((eid, name, f"id не збігається (наш {eid} ≠ BTK {leaves[name]})"))
    valid = len(targets) - len(bad)
    print(f"[verify-eva-cats] EVA-таргетів у мапі: {len(targets)} | ВАЛІДНИХ листків: {valid} | РОЗБІЖНИХ: {len(bad)}")
    for eid, name, why in bad:
        print(f"   ❌ {name[:44]:44} ({eid}): {why}")

    # Фід (опційно): які категорії фіда — фолбеки (не валідні листки EVA) = «Нова» в кабінеті
    if FEED_XML.exists():
        xml = FEED_XML.read_text(encoding="utf-8")
        feed_cats = re.findall(r'<category id="([^"]+)">([^<]*)</category>', xml)
        fb = [(cid, nm) for cid, nm in feed_cats if nm not in leaves]
        print(f"[verify-eva-cats] Категорій у фіді: {len(feed_cats)} | НЕ-листків (фолбек → «Нова»): {len(fb)}")
        for cid, nm in sorted(fb, key=lambda x: x[1]):
            print(f"   ⚠ фід віддає не-EVA-категорію: {nm[:40]:40} ({cid}) — зіставити або виключити")
    else:
        print(f"[verify-eva-cats] {FEED_XML} нема — фід-перевірку пропущено (згенеруй локально для повної звірки).")

    if bad:
        print("[verify-eva-cats] РЕЗУЛЬТАТ: ❌ мапа має розбіжності з офіційним деревом EVA — див. вище.")
        return 1
    print("[verify-eva-cats] РЕЗУЛЬТАТ: ✅ усі EVA-таргети мапи — валідні офіційні листки EVA.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
