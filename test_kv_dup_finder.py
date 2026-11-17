#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_kv_dup_finder.py — дублі KIDDISVIT (kv-) ↔ Toysi / RoyalToys (запит SEO 17.11.2026). Інваріанти: (1) алгоритм rt_dup_finder: той самий товар з тією самою назвою → дубль; різні числа/кольори/серії → НЕ дубль;
(2) kv→Toysi дає `dup_of` (id Toysi), kv→RT дає зворотне `rt-<id>` → `kv-<id>`; (3) захист пар kv↔RT: ціна RT ≈ РРЦ KIDDISVIT або габарити збігаються, інакше пара відкидається; нема РРЦ → захист вимкнено;
(4) kv_supplier_export кладе `dup_of` лише kv- (не kvu-), rt_supplier_export — `kv_dup_of`; (5) завантажувачі мап відкидають сміття; (6) один товар пулу не віддається двом kv-картками."""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import kv_dup_finder as kd  # noqa: E402
import kv_supplier_export as kx  # noqa: E402
import rt_supplier_export as rx  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


kx.rt._alert = lambda k, t: None


def T(tid, name, vendor="Funko", stock=5, price=700, cat="Фігурки"):
    return {"id": tid, "name": name, "vendor": vendor, "stock": stock, "pictures": ["https://x/p.jpg"], "category_name": cat, "price": price, "vendor_code": tid}


def R(rid, name, vendor="Funko", stock=4, price=945, dims=None):
    return {"id": rid, "name": name, "name_ua": name, "vendor": vendor, "stock": str(stock), "pictures": ["https://x/r.jpg"], "category_name": "Фігурки", "price": str(price), "vendor_code": "R" + rid, "dims_cm": dims}


KV = {"100": {"name_ua": "Ігрова фігурка Funko POP! серії Гаррі Поттер - Герміона Грейнджер", "name": "Ігрова фігурка Funko POP! серії Гаррі Поттер - Герміона Грейнджер", "vendor": "Funko", "vendor_code": "KV100", "rrp": 945, "dims_cm": [15.9, 11.5, 8.8]},
      "101": {"name_ua": "Ігрова фігурка Funko POP! серії Гаррі Поттер - Рон Візлі", "name": "Ігрова фігурка Funko POP! серії Гаррі Поттер - Рон Візлі", "vendor": "Funko", "vendor_code": "KV101", "rrp": 945, "dims_cm": [15.9, 11.5, 8.8]}}
for i in range(200, 215):      # шумовий фон для IDF
    KV[str(i)] = {"name_ua": f"Ігрова фігурка Funko POP! серії Диснеї - Персонаж{i} Особливий", "name": f"Ігрова фігурка Funko POP! серії Диснеї - Персонаж{i} Особливий", "vendor": "Funko", "vendor_code": f"KV{i}", "rrp": 945, "dims_cm": [15.9, 11.5, 8.8]}
TOYSI = {"t1": T("t1", "Ігрова фігурка Funko POP! серії Гаррі Поттер - Герміона Грейнджер")}
for i in range(200, 215):
    TOYSI[f"t{i}"] = T(f"t{i}", f"Ігрова фігурка Funko POP! серії Диснеї - Персонаж{i} Особливий-чужий")
RT = {"r1": R("r1", "Ігрова фігурка Funko POP! серії Гаррі Поттер - Рон Візлі", price=945)}

kt, kr = kd.find_kv_dups(KV, TOYSI, RT)
chk("kv→Toysi: Герміона = товар Toysi t1; Рон (нема в Toysi) — не дубль", kt.get("kv-100") == "t1" and "kv-101" not in kt)
chk("kv→RT: Рон = rt r1 (ціна RT = РРЦ KIDDISVIT 945)", kr.get("kv-101") == "r1")
kvm, rtm = kd.build_maps(KV, TOYSI, RT)
chk("build_maps: kv_dup_map {kv-100: t1}; rt_kv_dup_map {rt-r1: kv-101} (зворотне посилання)", kvm == {"kv-100": "t1"} and rtm == {"rt-r1": "kv-101"})

RT2 = {"r1": R("r1", "Ігрова фігурка Funko POP! серії Гаррі Поттер - Рон Візлі", price=534, dims=[3, 4, 5])}
chk("захист kv↔RT: ціна RT 534 ≠ РРЦ 945 і габарити різні → пара ВІДКИНУТА", kd.build_maps(KV, TOYSI, RT2)[1] == {})
RT3 = {"r1": R("r1", "Ігрова фігурка Funko POP! серії Гаррі Поттер - Рон Візлі", price=534, dims=[8.8, 11.5, 15.9])}
chk("захист kv↔RT: ціна інша, але габарити збігаються (±3 см) → пара приймається", kd.build_maps(KV, TOYSI, RT3)[1] == {"rt-r1": "kv-101"})
KVnorrp = {k: {**v, "rrp": None} for k, v in KV.items()}
chk("нема РРЦ у kv-елементі → захист вимкнено, пара за назвою лишається", kd.build_maps(KVnorrp, TOYSI, RT2)[1] == {"rt-r1": "kv-101"})
chk("rt_oracle_ok: сміття/нуль/порожні габарити → False при заданому РРЦ", not kd.rt_oracle_ok({"rrp": 945, "dims_cm": None}, {"price": "x", "dims_cm": None}) and kd.rt_oracle_ok({"rrp": None}, {}))

KVc = {"100": KV["100"], "102": {**KV["100"], "vendor_code": "KV102"}}
TOYSIc = {"t1": TOYSI["t1"]}
for i in range(200, 215):
    TOYSIc[f"t{i}"] = TOYSI[f"t{i}"]
chk("один товар Toysi «претендують» дві kv-картки → не віддаємо нікому (зворотна неоднозначність)", "kv-100" not in kd.find_kv_dups(KVc, TOYSIc, {})[0] and "kv-102" not in kd.find_kv_dups(KVc, TOYSIc, {})[0])

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    p = td / "kv.json"
    p.write_text(json.dumps({"items": {"kv-5": {"name": "Назва", "vendor": "V", "sku": "S"}, "kvu-5": {"name": "Уцінка", "vendor": "V"}, "x": {"name": "n"}, "kv-6": {"vendor": "V"}}}), encoding="utf-8")
    it = kd.load_kv_items(p)
    chk("load_kv_items: лише kv-<цифри> з назвою (kvu-, сміття, без назви — відкинуто); sku → vendor_code", list(it) == ["5"] and it["5"]["vendor_code"] == "S")
    m1 = td / "d.json"
    m1.write_text(json.dumps({"kv-1": "123", "kv-2": "abc", "bad": "5", "kvu-3": "7", "kv-4": 55}), encoding="utf-8")
    chk("kx.load_dup_map: лише kv-<id> → цифровий id Toysi (kv-2 «abc», bad, kvu-3 відкинуто; число → рядок)", kx.load_dup_map(m1) == {"kv-1": "123", "kv-4": "55"})
    m2 = td / "r.json"
    m2.write_text(json.dumps({"rt-1": "kv-9", "rt-2": "xx", "zz": "kv-1"}), encoding="utf-8")
    chk("rx.load_kv_dup_map: rt-<id> → kv-<цифри> (решта відкинута)", rx.load_kv_dup_map(m2) == {"rt-1": "kv-9"})
    chk("нема/битий файл → {}", kx.load_dup_map(td / "nope.json") == {} and rx.load_kv_dup_map(td / "nope.json") == {})

# kv_supplier_export: dup_of лише для kv-, не для kvu-
PROPS = [{"name": "Розмір іграшки в упаковці довжина", "value": "15,9"}, {"name": "Розмір іграшки в упаковці ширина", "value": "11.5"}, {"name": "Розмір іграшки в упаковці висота", "value": "8,8"}]
CATS = {"leaf": {"id": "leaf", "parentID": None, "name": "Фігурки"}}
prod = {"9": {"productID": "9", "name": "Фігурка Тест", "description": "", "brand_name": "Funko", "brand_id": "B", "barcode": "1", "sku": "S9", "price": "100", "category_id": "leaf", "images": ["https://i/1.jpg"], "properties": PROPS}}
price = {"9": {"ProductID": "9", "clientPrice": 70, "retailPrice": 100, "rest": [{"WarehouseID": "Dnepr", "Rest": 7}, {"WarehouseID": "NTV20", "Rest": 5}]}}


def fetch_ok(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"j")
    return True


with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    out = td / "o.json"
    ok = kx.run_once(collected=(prod, price, CATS), out=out, fetch=fetch_ok, img_dir=td / "img", selection=kx.DEFAULT_SELECTION, index_ids=set(), dup_map={"kv-9": "777"})
    d = json.loads(out.read_text(encoding="utf-8"))["items"]
    chk("supplier_kv.json: kv-9 має dup_of=777; kvu-9 (Уцінка) — БЕЗ dup_of", ok and d["kv-9"]["dup_of"] == "777" and "dup_of" not in d["kvu-9"])
    out2 = td / "o2.json"
    kx.run_once(collected=(prod, price, CATS), out=out2, fetch=fetch_ok, img_dir=td / "img", selection=kx.DEFAULT_SELECTION, index_ids=set(), dup_map={})
    chk("без дублів → поля dup_of нема", "dup_of" not in json.loads(out2.read_text(encoding="utf-8"))["items"]["kv-9"])

# rt_supplier_export: kv_dup_of
real_kv_dup = rx.load_kv_dup_map
rx.load_kv_dup_map = lambda: {"rt-1": "kv-9"}
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    rt_cat = {"1": {"name_ua": "Іграшка", "description_ua": "д", "price": "100", "stock": 5, "available": True, "vendor": "V", "vendor_code": "C", "category_name": "Конструкторы", "dims_cm": [10.0, 10.0, 10.0], "pictures": ["https://x/1.jpg"]}}
    o = td / "rt.json"
    rx.run_once(rt_cat, ["rt-1"], o, fetch_ok, td / "img", dup_map={})
    chk("supplier_rt.json: rt-1 має kv_dup_of=kv-9", json.loads(o.read_text(encoding="utf-8"))["items"]["rt-1"].get("kv_dup_of") == "kv-9")
rx.load_kv_dup_map = real_kv_dup

# реальні файли в репо
for name, pat in (("kv_dup_map.json", r"kv-[0-9]+"), ("rt_kv_dup_map.json", r"rt-[0-9A-Za-z_]+")):
    pth = HERE / name
    if pth.exists():
        dd = json.loads(pth.read_text(encoding="utf-8"))
        import re
        chk(f"{name}: валідний словник, ключі {pat}", isinstance(dd, dict) and len(dd) > 0 and all(re.fullmatch(pat, k) for k in dd))
        if name == "kv_dup_map.json":
            chk("kv_dup_map.json: значення — цифрові id Toysi", all(re.fullmatch(r"[0-9]{1,12}", str(v)) for v in dd.values()))
        else:
            chk("rt_kv_dup_map.json: значення kv-<цифри>, жодна kv-картка не віддана двом RT", all(re.fullmatch(r"kv-[0-9]+", v) for v in dd.values()) and len(set(dd.values())) == len(dd))

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
