#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_eva_legacy_override.py — повернення СТАРИХ співставлених у кабінеті EVA категорійних id у фід (generate_eva_feed._map_eva_category + _load_legacy_override, 16.10.2026).
Інваріанти: (1) mode=ids: лише категорії зі списку віддають старий Toysi-id + назву з eva_matched_legacy_ids.json, решта — EVA-id як раніше; (2) mode=all_safe: усі, де співставлена EVA-категорія збігається з нашою мапою (без «(МП)»-назви);
(3) off / нема файла / зіпсований JSON / id без співставленого запису → {} (поведінка як до зміни); (4) категорія не з мапи не чіпається; (5) робочий eva_legacy_rollout.json (етап 1) завантажується, усі його id — у мапі, співставлені й «безпечні».
`python test_eva_legacy_override.py` → exit 0/1."""
import json
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
sys.path.insert(0, str(Path(__file__).parent))
import generate_eva_feed as g  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


tmp = Path(tempfile.mkdtemp())
matched = {"_comment": "x", "matched": {
    "1": {"name": "Відерця", "eva": "Ігри для вулиці"},          # збігається з нашою мапою → safe
    "2": {"name": "Бейблейд", "eva": "Іграшки для малюків (МП)"},  # інша EVA-категорія → не safe
    "3": {"name": "Фігурки (МП)", "eva": "Ігрові фігурки (МП)"},    # назва з (МП), але EVA-категорія збігається; (МП) у НАЗВІ мерчанта → не safe
    "4": {"name": "Мʼячики", "eva": "Ігри для вулиці"},
}}
(tmp / "matched.json").write_text(json.dumps(matched, ensure_ascii=False), encoding="utf-8")
orig_map, orig_m, orig_r = dict(g.EVA_CATEGORY_MAP), g.EVA_MATCHED_LEGACY_FILE, g.EVA_LEGACY_ROLLOUT_FILE
g.EVA_CATEGORY_MAP.clear()
g.EVA_CATEGORY_MAP.update({"1": {"id": "105952", "name": "Ігри для вулиці"}, "2": {"id": "120707", "name": "Ігрові фігурки"}, "3": {"id": "120707", "name": "Ігрові фігурки"}, "4": {"id": "105952", "name": "Ігри для вулиці"}, "9": {"id": "100000", "name": "Інше"}})
g.EVA_MATCHED_LEGACY_FILE = tmp / "matched.json"


def load(cfg):
    g.EVA_LEGACY_ROLLOUT_FILE = tmp / "roll.json"
    if cfg is None:
        (tmp / "roll.json").unlink(missing_ok=True)
    elif isinstance(cfg, str):
        (tmp / "roll.json").write_text(cfg, encoding="utf-8")
    else:
        (tmp / "roll.json").write_text(json.dumps(cfg), encoding="utf-8")
    g.EVA_LEGACY_OVERRIDE = g._load_legacy_override()
    return g.EVA_LEGACY_OVERRIDE


o = load({"mode": "ids", "ids": ["1", "2", "9", "777"]})
chk("mode=ids: лише id зі списку, що є у співставлених (1, 2); 9 (нема в matched) і 777 (нема нікуди) ігноруються", set(o) == {"1", "2"} and o["1"] == "Відерця")
chk("_map_eva_category: категорія зі списку → старий id+назва з кабінету; інша → EVA-id як раніше; не з мапи → без змін", g._map_eva_category("1", "x") == ("1", "Відерця") and g._map_eva_category("4", "x") == ("105952", "Ігри для вулиці") and g._map_eva_category("555", "Ім'я") == ("555", "Ім'я"))
chk("категорія зі списку, але не в нашій мапі, не чіпається (override діє лише для зіставлених нами)", g._map_eva_category("777", "q") == ("777", "q"))
o = load({"mode": "all_safe"})
chk("mode=all_safe: 1 і 4 (EVA-категорія збігається) так; 2 (інша EVA-категорія) і 3 («(МП)» у назві мерчанта) — ні", set(o) == {"1", "4"})
for label, cfg in (("mode=off", {"mode": "off"}), ("нема файла", None), ("зіпсований JSON", "{не json"), ("порожній об'єкт", {})):
    chk(f"{label}: override порожній, поведінка як до зміни", load(cfg) == {} and g._map_eva_category("1", "x") == ("105952", "Ігри для вулиці"))
g.EVA_LEGACY_ROLLOUT_FILE = tmp / "roll.json"
(tmp / "roll.json").write_text(json.dumps({"mode": "ids", "ids": ["1"]}), encoding="utf-8")
g.EVA_MATCHED_LEGACY_FILE = tmp / "нема.json"
chk("нема eva_matched_legacy_ids.json → {} (без винятку)", g._load_legacy_override() == {})

# робочі файли репо (етап 1)
g.EVA_CATEGORY_MAP.clear(); g.EVA_CATEGORY_MAP.update(orig_map)
g.EVA_MATCHED_LEGACY_FILE, g.EVA_LEGACY_ROLLOUT_FILE = orig_m, orig_r
real = g._load_legacy_override()
cfg = json.loads(orig_r.read_text(encoding="utf-8"))
mm = json.loads(orig_m.read_text(encoding="utf-8"))["matched"]
chk("робочий rollout (етап 2): mode=all_safe; override = 160 безпечних id; кожен id у мапі, у співставлених і з тією ж EVA-категорією", cfg["mode"] == "all_safe" and len(real) == 160 and all(t in orig_map and t in mm and g._norm_eva_name(mm[t]["eva"]) == g._norm_eva_name(orig_map[t]["name"]) and "(МП)" not in str(mm[t].get("name") or "") for t in real))
chk("знімок кабінету: 212 співставлених id", len(mm) == 212)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
