#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_cost_isimport_prom_fee.py — два рішення власника 16.11.2026 щодо собівартості/ціни.

П.1 («так, роби п.1»): знижка Toysi у СОБІВАРТОСТІ за тегом <isImport> (true -> 15 %, false -> 5 %), лише в
    competitor_pricing.toysi_cost_discounted_price -> real_toysi_cost. toysi_discounted_price (база ціни сайту ×1,5 / EVA ×1,45)
    НЕ змінюється; ворота TOYSI_COST_BY_IMPORT=0 повертає старе.
П.2 («додай 10 грн»): + PROM_ORDER_FEE_UAH (10 ₴) до собівартості Prom у нижній межі й ціні (decide_price_for_platform
    і всі окремі Prom-підлоги). Rozetka/сайт не чіпаємо. "0" вимикає.

`python test_cost_isimport_prom_fee.py` -> exit 0/1. Мережі нема; реальний каталог (toysi_catalog_cache.json) — якщо є поруч
(або в основному репо), інакше ця одна перевірка пропускається з явним SKIP.
"""
import json
import math
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ.pop("SITE_MARKET_PRICE", None)
os.environ.pop("TOYSI_COST_BY_IMPORT", None)
sys.path.insert(0, str(Path(__file__).parent))

import competitor_pricing as cp  # noqa: E402
import parser as toysi_parser  # noqa: E402

F = []


def chk(label, ok):
    print(("[OK ] " if ok else "[FAIL] ") + label)
    if not ok:
        F.append(label)


def near(a, b, eps=1e-9):
    return abs(a - b) <= eps


def item(pid="1", price=200, **kw):
    d = {"id": str(pid), "vendor_code": str(pid), "price": str(price), "vendor": "MIC", "country": "Китай",
         "category_name": "Пазли підлогові"}
    d.update(kw)
    return d


# детермінізм: без спостережених знижок (файл VPS-only), підміняємо кеш
cp._observed_discount_cache = {}

# ============================================================ парсер: <isImport>
XML = """<yml_catalog><shop><categories><category id="1">Пазли</category></categories><offers>
<offer id="1"><vendorCode>1</vendorCode><name>A</name><price>100</price><isImport>true</isImport></offer>
<offer id="2"><vendorCode>2</vendorCode><name>B</name><price>100</price><isImport> FALSE </isImport></offer>
<offer id="3"><vendorCode>3</vendorCode><name>C</name><price>100</price></offer>
<offer id="4"><vendorCode>4</vendorCode><name>D</name><price>100</price><isImport>maybe</isImport></offer>
<offer id="5"><vendorCode>5</vendorCode><name>E</name><price>100</price><isImport></isImport></offer>
</offers></shop></yml_catalog>""".encode("utf-8")
cat = toysi_parser._parse_xml(XML)
chk("parser: isImport=true -> True", cat["1"]["is_import"] is True)
chk("parser: isImport=' FALSE ' (регістр/пробіли) -> False", cat["2"]["is_import"] is False)
chk("parser: тега нема -> None", cat["3"]["is_import"] is None)
chk("parser: сміття 'maybe' -> None (не False)", cat["4"]["is_import"] is None)
chk("parser: порожній тег -> None", cat["5"]["is_import"] is None)
try:
    rt = json.loads(json.dumps(cat, ensure_ascii=False))
    chk("parser: каталог із is_import проходить JSON-цикл (кеш toysi_catalog_cache.json), true/false/null збережені",
        rt["1"]["is_import"] is True and rt["2"]["is_import"] is False and rt["3"]["is_import"] is None)
except Exception as e:  # noqa: BLE001
    chk(f"parser: JSON-цикл ({e})", False)

# ============================================================ собівартість: isImport
P = 200.0
chk("is_import=True -> знижка 15 %", near(cp.toysi_cost_discounted_price(item(is_import=True)), P * 0.85))
chk("is_import=False -> знижка 5 %", near(cp.toysi_cost_discounted_price(item(is_import=False)), P * 0.95))
chk("is_import=None -> старий фолбек (15 %)", near(cp.toysi_cost_discounted_price(item(is_import=None)), P * 0.85))
chk("тега/ключа нема -> старий фолбек, ідентичний toysi_discounted_price",
    cp.toysi_cost_discounted_price(item()) == cp.toysi_discounted_price(item()))
UA = item(country="Україна")
chk("is_import=None + Україна -> старий кап 5 %", near(cp.toysi_cost_discounted_price({**UA, "is_import": None}), P * 0.95))
chk("is_import=True + Україна -> 15 % (кап України на цьому шляху НЕ діє)", near(cp.toysi_cost_discounted_price({**UA, "is_import": True}), P * 0.85))
chk("is_import=False + Україна -> 5 %", near(cp.toysi_cost_discounted_price({**UA, "is_import": False}), P * 0.95))
chk("не-bool значення ('true', 1) НЕ трактуються як True/False (лише справжній bool) -> старий фолбек",
    near(cp.toysi_cost_discounted_price(item(is_import="true")), P * 0.85) and near(cp.toysi_cost_discounted_price(item(is_import=0)), P * 0.85))

# observed має пріоритет над isImport
cp._observed_discount_cache = {"1": {"pct": 0.0, "observed_at": datetime.now().isoformat(timespec="seconds"), "source_order": "t"}}
chk("observed 0 % перемагає is_import=True", near(cp.toysi_cost_discounted_price(item(is_import=True)), P))
cp._observed_discount_cache = {"1": {"pct": 0.15, "observed_at": datetime.now().isoformat(timespec="seconds"), "source_order": "t"}}
chk("observed 15 % перемагає is_import=False", near(cp.toysi_cost_discounted_price(item(is_import=False)), P * 0.85))
cp._observed_discount_cache = {}

# винятки бренд/категорія -> без знижки навіть при isImport
chk("виняток-бренд Kinsmart + is_import=True -> без знижки", near(cp.toysi_cost_discounted_price(item(vendor="Kinsmart", is_import=True)), P))
chk("виняток-категорія «Велосипеди» + is_import=False -> без знижки", near(cp.toysi_cost_discounted_price(item(category_name="Велосипеди дитячі", is_import=False)), P))

# real_toysi_cost = знижена + 15 Збірка
chk("real_toysi_cost(import=True) = 200*0.85 + 15", near(cp.real_toysi_cost(item(is_import=True)), 185.0))
chk("real_toysi_cost(import=False) = 200*0.95 + 15", near(cp.real_toysi_cost(item(is_import=False)), 205.0))
chk("real_toysi_cost(None) = як було (185.0)", near(cp.real_toysi_cost(item()), 185.0))
chk("price<=0 -> без Збірки/знижки (як було)", cp.real_toysi_cost(item(price=0, is_import=False)) == 0 and cp.toysi_cost_discounted_price(item(price="x")) == 0.0)

# ворота
os.environ["TOYSI_COST_BY_IMPORT"] = "0"
chk("ворота TOYSI_COST_BY_IMPORT=0: is_import=False -> старий 15 %", near(cp.real_toysi_cost(item(is_import=False)), 185.0))
chk("ворота =0: is_import=True -> 15 % (як було)", near(cp.real_toysi_cost(item(is_import=True)), 185.0))
chk("ворота =0: Україна + is_import=True -> старий кап 5 %", near(cp.toysi_cost_discounted_price({**UA, "is_import": True}), P * 0.95))
os.environ["TOYSI_COST_BY_IMPORT"] = "1"
chk("ворота =1: is_import=False -> 5 %", near(cp.real_toysi_cost(item(is_import=False)), 205.0))
os.environ.pop("TOYSI_COST_BY_IMPORT", None)
chk("ворота за замовчуванням ВВІМКНЕНІ", cp.toysi_cost_by_import_on() is True)

# 🔴 база ціни сайту/EVA (toysi_discounted_price) НЕ залежить від is_import
base_same = all(cp.toysi_discounted_price(item(is_import=v)) == cp.toysi_discounted_price(item()) for v in (True, False, None))
chk("toysi_discounted_price (база сайту/EVA) однакова для is_import True/False/None", base_same)
hi_true = cp.site_retail_price(item(price=500, is_import=True))
hi_false = cp.site_retail_price(item(price=500, is_import=False))
hi_none = cp.site_retail_price(item(price=500))
chk("site_retail_price (підлога не зв'язує, ціна 500) однакова для is_import True/False/None", hi_true == hi_false == hi_none == math.ceil(500 * 0.85 * 1.5))
# коли ПІДЛОГА cost*1.03 зв'язує (дешеві) — ціна зростає для не-імпорту (очікувана, задокументована зміна)
lo_none = cp.site_retail_price(item(price=20))
lo_false = cp.site_retail_price(item(price=20, is_import=False))
chk("site_retail_price дешевого (20 ₴, підлога зв'язує): не-імпорт >= як було (підлога cost*1,03 піднялась); імпорт = як було",
    lo_false >= lo_none and cp.site_retail_price(item(price=20, is_import=True)) == lo_none)

# ============================================================ Prom +10 ₴
chk("PROM_ORDER_FEE_UAH за замовчуванням = 10.0", cp.PROM_ORDER_FEE_UAH == 10.0)
for raw, want in [("0", 0.0), ("10", 10.0), ("12.5", 12.5), ("5,5", 5.5), ("abc", 10.0), ("", 10.0), ("-3", 10.0),
                  ("nan", 10.0), ("inf", 10.0), (None, 10.0)]:
    chk(f"_parse_prom_order_fee({raw!r}) = {want}", cp._parse_prom_order_fee(raw) == want)
chk("prom_effective_cost(100) = 110", cp.prom_effective_cost(100) == 110.0)
chk("prom_effective_cost(0/від'ємна/нечисло) без змін (невизначена лишається невизначеною)",
    cp.prom_effective_cost(0) == 0 and cp.prom_effective_cost(-5) == -5 and cp.prom_effective_cost("x") == "x")

CAT = "Пазли підлогові"
COST = 100.0
cp.PROM_ORDER_FEE_UAH = 0.0
old_nc = cp.decide_price_for_platform(COST, None, "prom", CAT)
old_c = cp.decide_price_for_platform(COST, 150.0, "prom", CAT)
old_c_lo = cp.decide_price_for_platform(COST, 80.0, "prom", CAT)
old_rz = cp.decide_price_for_platform(COST, None, "rozetka")
old_rz_c = cp.decide_price_for_platform(COST, 150.0, "rozetka")
cp.PROM_ORDER_FEE_UAH = 10.0
new_nc = cp.decide_price_for_platform(COST, None, "prom", CAT)
new_c = cp.decide_price_for_platform(COST, 150.0, "prom", CAT)
new_c_lo = cp.decide_price_for_platform(COST, 80.0, "prom", CAT)
new_rz = cp.decide_price_for_platform(COST, None, "rozetka")
new_rz_c = cp.decide_price_for_platform(COST, 150.0, "rozetka")

tc = cp.compute_total_commission("prom", CAT)
ce = COST + 10.0
# без конкурента: floor = ce*1.25/(1-tc); ціна = max(ce*1.75, floor)
chk("Prom без конкурента: floor = (cost+10)*1.25/(1-tc)", near(new_nc["floor"], round(ce * 1.25 / (1 - tc), 2), 0.011))
chk("Prom без конкурента: ціна = max((cost+10)*1.75, floor)", near(new_nc["price"], round(max(ce * 1.75, ce * 1.25 / (1 - tc)), 2), 0.011))
chk("Prom без конкурента: ціна/floor ВИЩІ за стару", new_nc["price"] > old_nc["price"] and new_nc["floor"] > old_nc["floor"])
# з конкурентом 150: candidate = 147, B = ce*1.03 + 147*tc
cand = 150.0 - cp.PRICE_STEP
want_floor = round(ce * 1.03 + cand * tc, 2)
chk("Prom з конкурентом: floor = (cost+10)*1.03 + candidate*tc (канонічна формула)", near(new_c["floor"], want_floor, 0.011))
chk("Prom з конкурентом: floor вищий рівно на 10*1.03 від старого", near(new_c["floor"] - old_c["floor"], 10.3, 0.011))
chk("Prom: margin_pct рахується від cost_eff (при ціні = floor маржа ~3 % від cost+10)",
    near(new_c["margin_pct"], round((new_c["price"] * (1 - tc) - ce) / ce * 100, 1), 0.051))
# min_safe_price: конкурент ≈ дно -> net-виручка = cost_eff
want_safe = round(ce / (1 - cp.compute_total_commission("prom", CAT, new_c_lo["price"])), 2)
chk("Prom: аварійний запобіжник net-виручка >= cost+10 (min_safe_price від cost_eff)",
    new_c_lo["price"] >= want_safe - 0.011 and new_c_lo["category"] == "floor")
chk("Prom: ціна з конкурентом, що вже вище floor, не змінюється (undercut -3 лишається)",
    (new_c["price"] == old_c["price"]) if old_c["category"] == "undercut" and new_c["category"] == "undercut" else True)
chk("Rozetka БЕЗ змін (без конкурента і з конкурентом) при будь-якій витраті Prom", new_rz == old_rz and new_rz_c == old_rz_c)
cp.PROM_ORDER_FEE_UAH = 0.0
chk('PROM_ORDER_FEE_UAH=0 -> Prom побайтово як до змін (формула без витрати)',
    cp.decide_price_for_platform(COST, None, "prom", CAT) == old_nc and cp.decide_price_for_platform(COST, 150.0, "prom", CAT) == old_c)
chk('"0": floor без витрати = cost*1.25/(1-tc)', near(old_nc["floor"], round(COST * 1.25 / (1 - tc), 2), 0.011))
cp.PROM_ORDER_FEE_UAH = 10.0

# сайт/EVA від витрати Prom не залежать
cp.PROM_ORDER_FEE_UAH = 0.0
s0 = [cp.site_retail_price(item(price=p)) for p in (20, 60, 200, 900)]
cp.PROM_ORDER_FEE_UAH = 10.0
s10 = [cp.site_retail_price(item(price=p)) for p in (20, 60, 200, 900)]
chk("site_retail_price не залежить від PROM_ORDER_FEE_UAH", s0 == s10)

# ============================================================ окремі Prom-підлоги (синхронність)
import generate_prom_feed as gpf  # noqa: E402
import generate_google_feed as gg  # noqa: E402


def feed_price(fee, price_override, comp):
    cp.PROM_ORDER_FEE_UAH = fee
    it = {"id": "1", "name": "Пазл «Тест» 100 ел.", "price": 200.0, "stock": 5, "category_id": "98887",
          "category_name": CAT, "vendor": "X", "vendor_code": "1", "pictures": ["https://toysi.ua/p/1.jpg"],
          "description": "Опис", "params": [], "barcode": ""}
    root, _ = gpf._build_xml({"1": it}, price_overrides={"1": price_override}, competitor_prices={"1": comp} if comp else None)
    off = root.find("shop").find("offers").find("offer")
    return float(off.find("price").text)


cost_item = cp.real_toysi_cost({"id": "1", "price": "200", "vendor": "X", "country": "Китай", "category_name": CAT})  # 185
tc_f = cp.compute_total_commission("prom", CAT, 1.0)
for fee in (0.0, 10.0):
    pf = feed_price(fee, 1.0, 300.0)   # override нижче будь-якого floor, свіжий конкурент 300
    cand_f = 300.0 - cp.PRICE_STEP
    fl = max(round((cost_item + fee) * 1.03 + cand_f * cp.compute_total_commission("prom", CAT, pf), 2), 0)
    chk(f"Prom-фід floor-гард (fee={fee}): ціна піднята до канонічного floor від cost+fee", pf >= fl - 0.011 and pf <= fl + 0.5)
chk("Prom-фід floor-гард: з витратою 10 ціна ВИЩА, ніж без", feed_price(10.0, 1.0, 300.0) > feed_price(0.0, 1.0, 300.0))
chk("Prom-фід (без конкурента, override нижче): гард піднімає на division-floor від cost+10",
    feed_price(10.0, 1.0, None) > feed_price(0.0, 1.0, None))
# Google _prom_page_price (мусить збігатись з generate_prom_feed)
cp.PROM_ORDER_FEE_UAH = 0.0
g0 = gg._prom_page_price(cost_item, CAT, 1.0)
cp.PROM_ORDER_FEE_UAH = 10.0
g10 = gg._prom_page_price(cost_item, CAT, 1.0)
chk("generate_google_feed._prom_page_price: floor включає витрату Prom (10 > 0)", g10 > g0)
cp.PROM_ORDER_FEE_UAH = 10.0

# ============================================================ реальний каталог: site_retail_price побайтово як було
def ref_site_price(it):
    """Еталон СТАРОЇ логіки: real_toysi_cost = round(toysi_discounted_price + 15, 2); toysi_discounted_price НЕ змінена."""
    d = cp.toysi_discounted_price(it)
    base = math.ceil(d * cp.SITE_PRICE_MULT)
    cost = round(d + cp.TOYSI_ASSEMBLY_FEE_UAH, 2) if d > 0 else d
    if not cost or cost <= 0:
        return int(base)
    return int(max(base, math.ceil(cost * (1 + cp.SITE_MIN_MARGIN))))


cands = [Path(os.environ["TOYSI_CATALOG_CACHE"])] if os.environ.get("TOYSI_CATALOG_CACHE") else []
cands += [Path(__file__).parent / "toysi_catalog_cache.json", Path(r"C:\Users\smach\rozetka_agent\toysi_catalog_cache.json")]
cache = next((c for c in cands if c.exists()), None)
if cache is None:
    print("[SKIP] реальний каталог toysi_catalog_cache.json не знайдено — перевірку побайтового збігу site_retail_price пропущено")
else:
    cat_real = json.loads(cache.read_text(encoding="utf-8"))
    items = list(cat_real.values()) if isinstance(cat_real, dict) else list(cat_real)
    n = len(items)
    has_flag = sum(1 for it in items if isinstance(it, dict) and it.get("is_import") is not None)
    diff = [it.get("id") for it in items if cp.site_retail_price(it) != ref_site_price(it)]
    cost_diff = [it.get("id") for it in items if cp.real_toysi_cost(it) != round(cp.toysi_discounted_price(it) + 15, 2)
                 and cp.toysi_discounted_price(it) > 0]
    chk(f"реальний каталог: {n} товарів (>= 20000), поле is_import у кеші відсутнє ({has_flag} шт. з ним) -> перевірка старого фолбеку",
        n >= 20000 and has_flag == 0)
    chk(f"реальний каталог: site_retail_price побайтово = еталон старої логіки на всіх {n} товарах (розбіжностей: {len(diff)})", not diff)
    chk(f"реальний каталог: real_toysi_cost = стара формула на всіх {n} (без is_import) (розбіжностей: {len(cost_diff)})", not cost_diff)

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ isImport-собівартість і витрата Prom 10 ₴ — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
