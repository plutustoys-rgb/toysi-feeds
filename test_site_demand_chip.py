#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_demand_chip.py — 13.10.2026 (власник: «маркетинг за попитом Google»): чип «Популярне зараз» із site/demand_featured.json від SEO. Файл {"date","source","ids","categories"}.
Нема файлу / зламаний / без date / застарів (>21 дня) / менше 6 придатних карток — чипа нема. Придатні: у наявності, з фото, внесок відомий і ≥ 0, не «Уцінка»/зброя/мілітарі. Топ-4 id завжди, решта слотів — щоденний зріз."""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


_n = [0]


def P(name=None, price=250, contrib=60, stock=5, cat=None, photo="https://x/p.jpg"):
    _n[0] += 1
    return {"id": str(7000 + _n[0]), "name": name or f"Іграшка «Л{_n[0]}»", "category": cat or f"К{_n[0] % 9}", "price": price, "contribution": contrib, "stock": stock, "photo": photo}


good = [P() for _ in range(30)]
bad = {
    "oos": P(stock=0), "nofoto": P(photo=""), "loss": P(contrib=-1.0), "nocost": P(contrib=None),
    "markdown": P(name="Уцінка. Ліхтарик «U1»"), "weapon": P(name="Пістолет водяний «W1»"), "mil": P(name="Військовий танк «M1»"),
}
prods = good + list(bad.values())
tmp = Path(tempfile.mkdtemp()) / "demand_featured.json"
bs.DEMAND_FILE = str(tmp)
os.environ["SITE_PICK_DATE"] = "2026-10-13"


def write(obj, bom=False, raw=None):
    data = raw if raw is not None else json.dumps(obj, ensure_ascii=False)
    tmp.write_text(data, encoding="utf-8-sig" if bom else "utf-8")


if tmp.exists():
    tmp.unlink()
chk("нема файлу: чипа «Популярне зараз» нема, без винятку", bs._load_demand_featured() is None and bs.demand_pick(prods) == [] and "pop" not in bs.home_chip_sets(prods))
ids_ok = [p["id"] for p in good]
write({"date": "2026-10-12", "source": "Search Console 28d", "ids": ids_ok + [b["id"] for b in bad.values()]})
d = bs._load_demand_featured()
chk("валідний файл: id читаються (до 40), дата й джерело", d is not None and len(d["ids"]) == 37 and str(d["date"]) == "2026-10-12" and d["source"] == "Search Console 28d")
pk = bs.demand_pick(prods)
chk("добірка попиту: 10 карток, жодної неприйдатної (нема залишку/фото, збиткова, невідома собівартість, «Уцінка», зброя, мілітарі)", len(pk) == 10 and not any(p["id"] in {b["id"] for b in bad.values()} for p in pk))
chk("топ-4 id списку (найвищий попит) завжди в добірці", all(i in {p["id"] for p in pk} for i in ids_ok[:4]))
os.environ["SITE_PICK_DATE"] = "2026-10-14"
chk("наступного дня решта слотів інша (зріз), топ-4 ті самі", [p["id"] for p in bs.demand_pick(prods)][:4] == ids_ok[:4] and {p["id"] for p in bs.demand_pick(prods)} != {p["id"] for p in pk})
os.environ["SITE_PICK_DATE"] = "2026-10-13"
chk("стабільно протягом доби", [p["id"] for p in bs.demand_pick(prods)] == [p["id"] for p in pk])
cs = bs.home_chip_sets(prods)
defs = bs.home_chip_defs(cs)
chk("чип «Популярне зараз» є, порядок: сезонні → «Популярне зараз» → «До 300 ₴»…, підпис", "pop" in cs and [x[0] for x in defs][0] == "pop" and "u300" not in [x[0] for x in defs] and any(x[1] == "Популярне зараз" and x[2] for x in defs))
os.environ["SITE_PICK_DATE"] = "2026-11-10"
chk("застарілий файл (> 21 дня) ігнорується голосно, чипа нема", bs._load_demand_featured() is None and "pop" not in bs.home_chip_sets(prods))
os.environ["SITE_PICK_DATE"] = "2026-10-13"
for label, raw in (("без date", {"ids": ids_ok}), ("зламана date", {"date": "13.10.2026", "ids": ids_ok}), ("неіснуюча дата", {"date": "2026-02-31", "ids": ids_ok}), ("ids не список", {"date": "2026-10-12", "ids": "abc"}), ("не dict", [1, 2, 3])):
    write(raw)
    chk(f"{label}: чипа нема, без винятку", bs._load_demand_featured() is None and bs.demand_pick(prods) == [])
write(None, raw="{не json")
chk("невалідний JSON: чипа нема, без винятку", bs._load_demand_featured() is None)
write({"date": "2026-10-12", "ids": [True, None, {"a": 1}, 7001, " 7002 ", "7001", ""] + ids_ok[:2]}, bom=True)
d = bs._load_demand_featured()
chk("BOM приймається; сміттєві елементи ids (bool/None/dict/дублі/порожні) пропускаються, числа й пробіли нормалізуються", d is not None and d["ids"][:2] == ["7001", "7002"] and len(set(d["ids"])) == len(d["ids"]))
write({"date": "2026-10-12", "ids": ids_ok[:5]})
chk("менше 6 придатних — чипа нема (не вигадуємо)", bs.demand_pick(prods) == [])
write({"date": "2026-10-12", "ids": [str(i) for i in range(500000, 500100)]})
chk("100 id, яких нема в каталозі — чипа нема; читається ≤ 40 id (гігантський файл не гальмує)", len(bs._load_demand_featured()["ids"]) == 40 and bs.demand_pick(prods) == [])
os.environ.pop("SITE_PICK_DATE", None)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
