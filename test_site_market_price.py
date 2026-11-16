#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_site_market_price.py — рішення власника 16.11.2026: ціна сайту = контрольна ціна Google − 3 ₴ (не нижче підлоги собівартість×1,03; без контрольної — ×1,5).
ІНВАРІАНТИ: ворота SITE_MARKET_PRICE (нема/0 → формула як було); з воротами і свіжою контрольною ціною — max(підлога, floor(контрольна−3)) і ніколи вище контрольної−3, якщо підлога нижча;
без контрольної / битої / нескінченної / нульової → формула; застарілий знімок (>7 діб) → формула; offerId = vendor_code, інакше id; підвищення для вже дешевших («Так, усі»).
`python test_site_market_price.py`
"""
import json
import math
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
sys.path.insert(0, str(Path(__file__).parent))

import competitor_pricing as cp  # noqa: E402
import gmc_price_competitiveness as g  # noqa: E402

F = []


def chk(label, ok):
    print(("[OK ] " if ok else "[FAIL] ") + label)
    if not ok:
        F.append(label)


def item(pid, price, vc=""):
    return {"id": str(pid), "vendor_code": vc, "price": str(price)}


def floor_of(it):
    return math.ceil(cp.real_toysi_cost(it) * (1 + cp.SITE_MIN_MARGIN))


os.environ.pop("SITE_MARKET_PRICE", None)
cp._MARKET_BENCH_CACHE = None
A = item(1, 200, "VC1")             # формула ≈ 1.5 × знижена
formula = cp.site_retail_price(A)
fl = floor_of(A)
chk("ворота вимкнені (нема змінної) → site_market_price = None, ціна = формула", cp.site_market_price(A, fl, {"VC1": 500.0}) is None and cp.site_retail_price(A) == formula)
os.environ["SITE_MARKET_PRICE"] = "0"
chk("SITE_MARKET_PRICE=0 → формула", cp.site_market_price(A, fl, {"VC1": 500.0}) is None)
os.environ["SITE_MARKET_PRICE"] = " 1"
chk("« 1» з пробілом тут вмикає (strip) — так само, як у інших воротах не критично; перевіряємо точне «1»", cp.site_market_price_on() is True)
os.environ["SITE_MARKET_PRICE"] = "1"

# контрольна вища за формулу: піднімаємо до контрольна−3 (рішення «Так, усі»)
bm = {"VC1": 500.0}
cp._MARKET_BENCH_CACHE = bm
chk("контрольна 500 → 497 (вище за формулу — піднімає)", cp.site_retail_price(A) == 497 and 497 > formula)
cp._MARKET_BENCH_CACHE = {"VC1": 500.5}
chk("контрольна 500,5 → 497 (вниз до цілого, не вище контрольної−3 = 497,5)", cp.site_retail_price(A) == 497)
cp._MARKET_BENCH_CACHE = {"VC1": 500.0 + 1e-12}
chk("контрольна 500,000000000001 → 497", cp.site_retail_price(A) == 497)
# контрольна нижча за формулу, вища за підлогу
mid = (fl + formula) // 2 + 3
cp._MARKET_BENCH_CACHE = {"VC1": float(mid)}
chk(f"контрольна {mid} (між підлогою {fl} і формулою {formula}) → {mid - 3} (дешевшає)", cp.site_retail_price(A) == max(fl, mid - 3) and cp.site_retail_price(A) <= formula)
# підлога: контрольна−3 нижче собівартості×1,03 → підлога
cp._MARKET_BENCH_CACHE = {"VC1": float(fl)}
chk("контрольна нижча за підлогу (контрольна−3 < підлоги) → підлога, не нижче собівартості×1,03", cp.site_retail_price(A) == fl)
cp._MARKET_BENCH_CACHE = {"VC1": 1.0}
chk("контрольна 1 грн → підлога", cp.site_retail_price(A) == fl)
# нема/некоректна контрольна → формула
for label, bmv in (("нема offerId у знімку", {"OTHER": 500.0}), ("0", {"VC1": 0}), ("від'ємна", {"VC1": -5.0}), ("NaN", {"VC1": float("nan")}), ("inf", {"VC1": float("inf")}), ("рядок", {"VC1": "500"}), ("порожній знімок", {})):
    cp._MARKET_BENCH_CACHE = bmv
    chk(f"контрольна «{label}» → формула без змін", cp.site_retail_price(A) == formula)
# offerId: vendor_code, інакше id
B = item(77, 200, "")
cp._MARKET_BENCH_CACHE = {"77": 400.0}
chk("без vendor_code offerId = id каталогу", cp.market_offer_id(B) == "77" and cp.site_retail_price(B) == 397)
cp._MARKET_BENCH_CACHE = {"VC1": 400.0, "1": 999.0}
chk("з vendor_code береться він (не id)", cp.site_retail_price(A) == 397)
chk("порожній товар без id/vendor_code → offerId порожній, правило не застосовується навіть при ключі «» у знімку", cp.market_offer_id({}) == "" and cp.site_market_price({}, 10, {"": 50.0}) is None)
# невизначена собівартість → як було (формула), навіть із контрольною
Z = {"id": "9", "vendor_code": "Z9", "price": "0"}
cp._MARKET_BENCH_CACHE = {"Z9": 300.0}
chk("ціна Toysi 0 (собівартість невизначена) → правило НЕ застосовується (ціна 0, а не 297)", cp.site_retail_price(Z) == 0)

# ---- знімок: bench у файлі, свіжість
NOW = datetime(2026, 11, 16, 12, 0, 0)
detail = {"a": {"price": 10.0, "benchmark": 12.345, "ratio": 0.8, "class": "vyhidni"}, "b": {"price": 5.0, "benchmark": None, "ratio": None, "class": "no_benchmark"},
          "c": {"price": 5.0, "benchmark": float("nan"), "ratio": None, "class": "x"}, "d": {"price": 5.0, "benchmark": 0.0, "ratio": None, "class": "x"}}
bf = g.bench_from_detail(detail)
chk("bench_from_detail: лише скінченні > 0, 2 знаки", bf == {"a": 12.35} or bf == {"a": 12.34})
tmp = Path(tempfile.mkdtemp()) / "snap.json"
g.write_snapshot({"a": "vyhidni"}, {"units": 1}, "t", NOW, tmp, bench=bf)
chk("знімок містить bench, мітки лишились", json.loads(tmp.read_text(encoding="utf-8")).get("bench") == bf and g.load_labels(tmp, now=NOW) == {"a": "vyhidni"})
chk("load_benchmarks: свіжий знімок", g.load_benchmarks(tmp, now=NOW) == bf)
chk("load_benchmarks: 6 діб — ще діє", g.load_benchmarks(tmp, now=NOW + timedelta(days=6)) == bf)
chk("load_benchmarks: 8 діб → {} (застарів, формула)", g.load_benchmarks(tmp, now=NOW + timedelta(days=8)) == {})
tmp2 = tmp.with_name("old.json")
g.write_snapshot({"a": "ni"}, {"units": 1}, "t", NOW, tmp2)       # знімок БЕЗ bench (старий формат)
chk("знімок без поля bench → {} (мітки працюють, ціна — формула)", g.load_benchmarks(tmp2, now=NOW) == {} and g.load_labels(tmp2, now=NOW) == {"a": "ni"})
tmp3 = tmp.with_name("bad.json")
tmp3.write_text("{не json", encoding="utf-8")
chk("битий файл / нема файлу → {} без винятку", g.load_benchmarks(tmp3, now=NOW) == {} and g.load_benchmarks(tmp.with_name("nope.json"), now=NOW) == {})
tmp4 = tmp.with_name("mix.json")
tmp4.write_text(json.dumps({"generated": NOW.strftime("%Y-%m-%dT%H:%M:%S"), "bench": {"x": 5, "y": "7", "z": -1, "w": 0, "v": 2.5}}), encoding="utf-8")
chk("load_benchmarks відсіює нечислові/невід'ємні/нульові значення", g.load_benchmarks(tmp4, now=NOW) == {"x": 5.0, "v": 2.5})

# ---- кеш: збій завантаження не валить ціну
cp._MARKET_BENCH_CACHE = None
os.environ["SITE_MARKET_PRICE"] = "1"
orig = g.load_benchmarks
try:
    g.load_benchmarks = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    chk("збій load_benchmarks → ціна за формулою, без винятку", cp.site_retail_price(A) == formula)
finally:
    g.load_benchmarks = orig
os.environ.pop("SITE_MARKET_PRICE", None)
cp._MARKET_BENCH_CACHE = None

# ---- звіт «що зміниться» (site_market_price_report): одиниця — товар, рахує стару/нову ціну тими самими функціями
import site_market_price_report as rep  # noqa: E402

cat = {"1": {"id": "1", "vendor_code": "R1", "price": "200", "stock": "5"},      # контрольна вища → дорожчає
       "2": {"id": "2", "vendor_code": "R2", "price": "200", "stock": "5"},      # контрольна нижча за формулу, вища за підлогу → дешевшає
       "3": {"id": "3", "vendor_code": "R3", "price": "200", "stock": "0"},      # контрольна нижча за підлогу → підлога; нема в наявності
       "4": {"id": "4", "vendor_code": "R4", "price": "200", "stock": "5"}}      # без контрольної → не рахується
f200 = floor_of(item(1, 200))
os.environ["SITE_MARKET_PRICE"] = "0"
old200 = cp.site_retail_price(item(1, 200))
os.environ["SITE_MARKET_PRICE"] = "1"
bmr = {"R1": 600.0, "R2": float(old200 - 20), "R3": 5.0}
sm = rep.compute(cat, bmr, formula_fn=lambda it: old200)
chk("звіт: одиниця = 3 товари з контрольною (4-й без неї не рахується)", sm["units"] == 3 and sm["units_instock"] == 2)
chk("звіт: 1 дорожчає (R1), 2 дешевшає (R2 і R3 до підлоги), у підлогу впирається 1 (R3)", sm["pricier"] == 1 and sm["cheaper"] == 2 and sm["at_floor"] == 1)
chk("звіт: сума в наявності: було 2×стара, стане 597 + (стара−20−3)", sm["sum_old_instock"] == 2 * old200 and sm["sum_new_instock"] == 597 + max(f200, old200 - 23))
chk("звіт: render не падає й містить одиницю та назву", "Ринкова ціна" in rep.render(sm, "2026-11-16T10:00:00") and "товар каталогу" in rep.render(sm))
os.environ.pop("SITE_MARKET_PRICE", None)
cp._MARKET_BENCH_CACHE = None

if F:
    print(f"\n❌ ПРОВАЛЕНО: {len(F)}")
    sys.exit(1)
print("\n✅ усе ок")
