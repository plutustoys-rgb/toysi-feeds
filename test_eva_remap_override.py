#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_eva_remap_override.py — перенаправлення незіставлених EVA-категорій у ВЖЕ співставлені в кабінеті (generate_eva_feed._load_remap_override, 20.10.2026).
Інваріанти: (1) mode=ids: лише Toysi-категорії, чия ЦІЛЬ (EVA-id) у списку етапу, і лише на старий id L, чия EVA-ціль у кабінеті ТОТОЖНА цілі мапи (без «(МП)»); (2) T з уже безпечним старим id (EVA_LEGACY_OVERRIDE) не чіпається;
(3) L без «(МП)» у назві і не з EVA_EXCLUDED_CATEGORIES; серед кількох — найбільший збіг слів із назвою цілі, далі менший id; (4) ціль без співставленого еквівалента — не чіпається; (5) off / нема файла / зіпсований JSON → {};
(6) _map_eva_category: legacy має пріоритет над remap, remap над EVA-id, решта без змін; (7) РОБОЧИЙ етап: кожна T з overrides → L із тією самою ціллю в живих файлах, 99075 («Пазли» поза мапою) тепер у мапі."""
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
matched = {"matched": {
    "10": {"name": "Пазли і вкладиші", "eva": "Пазли"},
    "11": {"name": "Інші пазли", "eva": "Пазли"},
    "12": {"name": "Пазли (МП)", "eva": "Пазли"},                # (МП) у назві — не береться
    "13": {"name": "Головоломки", "eva": "Головоломки"},
    "14": {"name": "Ігрові столики", "eva": "Різні набори для дитячої творчості (МП)"},   # виключена категорія — не береться
    "15": {"name": "Кінетичний пісок", "eva": "Різні набори для дитячої творчості (МП)"},
    "16": {"name": "Бейблейд", "eva": "Іграшки для малюків (МП)"},
}}
(tmp / "matched.json").write_text(json.dumps(matched, ensure_ascii=False), encoding="utf-8")
orig = (dict(g.EVA_CATEGORY_MAP), g.EVA_MATCHED_LEGACY_FILE, g.EVA_REMAP_ROLLOUT_FILE, dict(g.EVA_LEGACY_OVERRIDE), set(g.EVA_EXCLUDED_CATEGORIES))
g.EVA_CATEGORY_MAP.clear()
g.EVA_CATEGORY_MAP.update({
    "1": {"id": "105311", "name": "Пазли"}, "2": {"id": "105311", "name": "Пазли"}, "3": {"id": "105311", "name": "Пазли"},
    "4": {"id": "105918", "name": "Головоломки"},
    "5": {"id": "120704", "name": "Різні набори для дитячої творчості"},
    "6": {"id": "106605", "name": "Пенали"},                        # ціль без співставленого еквівалента
    "7": {"id": "100000", "name": "Інше"},
})
g.EVA_MATCHED_LEGACY_FILE = tmp / "matched.json"
g.EVA_EXCLUDED_CATEGORIES.add("14")
g.EVA_LEGACY_OVERRIDE.clear()
g.EVA_LEGACY_OVERRIDE["3"] = "Пазли і вкладиші"                 # T=3 уже віддає свій безпечний старий id


def load(cfg):
    g.EVA_REMAP_ROLLOUT_FILE = tmp / "roll.json"
    if cfg is None:
        (tmp / "roll.json").unlink(missing_ok=True)
    elif isinstance(cfg, str):
        (tmp / "roll.json").write_text(cfg, encoding="utf-8")
    else:
        (tmp / "roll.json").write_text(json.dumps(cfg), encoding="utf-8")
    g.EVA_REMAP_OVERRIDE = g._load_remap_override()
    return g.EVA_REMAP_OVERRIDE


o = load({"mode": "ids", "ids": ["105311", "105918", "120704", "106605"]})
chk("пазли: T=1,2 -> L=10 «Пазли і вкладиші» (більший збіг слів із «Пазли», (МП)-назву 12 пропущено)", o.get("1") == ("10", "Пазли і вкладиші") and o.get("2") == ("10", "Пазли і вкладиші"))
chk("T=3 (уже безпечний старий id) НЕ перенаправляється", "3" not in o)
chk("головоломки: T=4 -> L=13", o.get("4") == ("13", "Головоломки"))
chk("«Різні набори»: виключену категорію 14 пропущено -> L=15", o.get("5") == ("15", "Кінетичний пісок"))
chk("ціль без співставленого еквівалента (Пенали) і не з етапу (7) не чіпаються", "6" not in o and "7" not in o)
o = load({"mode": "ids", "ids": ["105918"]})
chk("етап лише з однією ціллю: перенаправляється тільки вона", set(o) == {"4"})
for label, cfg in (("mode=off", {"mode": "off"}), ("нема файла", None), ("зіпсований JSON", "{не json"), ("порожній об'єкт", {}), ("mode=all (невідомий)", {"mode": "all"})):
    chk(f"{label} -> {{}}", load(cfg) == {})
load({"mode": "ids", "ids": ["105311", "105918"]})
chk("_map_eva_category: remap -> (L, назва L); legacy має пріоритет; без override -> EVA-id; поза мапою без змін",
    g._map_eva_category("1", "x") == ("10", "Пазли і вкладиші") and g._map_eva_category("3", "x") == ("3", "Пазли і вкладиші")
    and g._map_eva_category("5", "x") == ("120704", "Різні набори для дитячої творчості") and g._map_eva_category("555", "Ім'я") == ("555", "Ім'я"))


# аудит #898: валідний JSON неправильної форми не валить імпорт, а дає {}
g.EVA_CATEGORY_MAP.clear()
g.EVA_CATEGORY_MAP.update({"1": {"id": "105311", "name": "Пазли"}})
g.EVA_LEGACY_OVERRIDE.clear()
for label, bad in (("matched=список", {"matched": [1, 2]}), ("запис-рядок", {"matched": {"10": "Пазли"}}), ("eva=число", {"matched": {"10": {"name": "Пазли і вкладиші", "eva": 5}}})):
    (tmp / "matched.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    g.EVA_MATCHED_LEGACY_FILE = tmp / "matched.json"
    g.EVA_REMAP_ROLLOUT_FILE = tmp / "roll.json"
    (tmp / "roll.json").write_text(json.dumps({"mode": "ids", "ids": ["105311"]}), encoding="utf-8")
    try:
        res = g._load_remap_override()
    except Exception as e:  # noqa: BLE001
        res = f"EXC {type(e).__name__}"
    chk(f"fail-closed: matched неправильної форми ({label}) -> {{}} без винятку", res == {})
# відновлення і перевірка на живих файлах
g.EVA_CATEGORY_MAP.clear()
g.EVA_CATEGORY_MAP.update(orig[0])
g.EVA_MATCHED_LEGACY_FILE, g.EVA_REMAP_ROLLOUT_FILE = orig[1], orig[2]
g.EVA_LEGACY_OVERRIDE.clear()
g.EVA_LEGACY_OVERRIDE.update(orig[3])
g.EVA_EXCLUDED_CATEGORIES.clear()
g.EVA_EXCLUDED_CATEGORIES.update(orig[4])
cfg = json.loads(g.EVA_REMAP_ROLLOUT_FILE.read_text(encoding="utf-8-sig"))
live = g._load_remap_override()
live_matched = json.loads(g.EVA_MATCHED_LEGACY_FILE.read_text(encoding="utf-8-sig"))["matched"]
want_t = {t for t, v in g.EVA_CATEGORY_MAP.items() if v["id"] in set(cfg.get("ids") or []) and t not in g.EVA_LEGACY_OVERRIDE}
chk("робочий етап: mode=ids, цілі етапу є в мапі, кожна їх T без безпечного старого id перенаправлена", cfg.get("mode") == "ids" and cfg.get("ids") and set(live) == want_t and len(live) >= 1)
bad = [t for t, (lid, ln) in live.items() if g._norm_eva_name(live_matched[lid]["eva"]) != g._norm_eva_name(g.EVA_CATEGORY_MAP[t]["name"]) or "(МП)" in ln or lid in g.EVA_EXCLUDED_CATEGORIES]
chk("робочий етап: у кожного L EVA-ціль у кабінеті ТОТОЖНА цілі мапи, без (МП), не виключена", not bad)
chk("«Пазли» поза мапою (99075) тепер у мапі -> 105311 і перенаправляється разом із пазлами", g.EVA_CATEGORY_MAP.get("99075", {}).get("id") == "105311" and "99075" in live)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
