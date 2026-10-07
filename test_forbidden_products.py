# -*- coding: utf-8 -*-
"""forbidden_products: політика «що не продаємо» (ножі/зброя, рішення власника 2026-10-07) діє в КОЖНОМУ генераторі вітрини.

  python test_forbidden_products.py            — офлайн: синтетичні перевірки всіх генераторів + структурний запобіжник
  python test_forbidden_products.py --live     — + живий каталог Toysi (мережа): жоден гейт не пропускає SKU заборонених категорій
  python test_forbidden_products.py --feeds DIR — + перевірка ГОТОВИХ фідів у теці (напр. feeds/ на VPS): жоден не містить заборонених SKU
"""
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import forbidden_products as fp

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

CAT = "Мечі, ножі та шаблі"
def item(i, category, **kw):
    d = {"id": str(i), "name": f"Іграшка {i}", "price": 200.0, "stock": 9, "category_id": "99147" if category == CAT else "10",
         "category_name": category, "vendor": "TestBrand", "vendor_code": str(i), "country": "Україна",
         "pictures": [f"https://toysi.ua/p/{i}.jpg"], "description": "Опис товару, достатньо довгий для вимог EVA " * 4, "params": [], "barcode": ""}
    d.update(kw)
    return d

BAD, OK = item(1, CAT), item(2, "Різне")

# ── 1. сам модуль ──
chk("is_forbidden: категорія → True, інша → False", fp.is_forbidden(BAD) and not fp.is_forbidden(OK))
chk("is_forbidden: регістр/пробіли/не-dict", fp.is_forbidden(item(3, "  МЕЧІ, НОЖІ ТА ШАБЛІ ")) and not fp.is_forbidden(None) and not fp.is_forbidden({}))

# ── 2. Prom (+Google/Meta/Bing/ALLO через select_top_items) ──
import generate_prom_feed_top as top
chk("Prom top: is_excluded_category ловить заборонену категорію, не чіпає звичайну", top.is_excluded_category(BAD) and not top.is_excluded_category(OK))
top.load_scan_state = lambda *a, **k: {}
top.load_delisted_pids = lambda *a, **k: {}
top.load_feed_membership = lambda *a, **k: {"1", "2"}            # навіть якщо заборонений SKU вже у фіді (членство) — вилітає
sel = top.select_top_items({"1": BAD, "2": OK}, target=10)
chk("select_top_items: заборонений SKU не потрапляє у відбір (навіть як чинний член), звичайний — так", "1" not in sel and "2" in sel)

# ── 3. EVA ──
import generate_eva_feed as eva
eva.EVA_BIRTHDAY_PROMO_SKUS = set(eva.EVA_BIRTHDAY_PROMO_SKUS) | {"1"}   # навіть промо-SKU не обходить політику
chk("EVA: контрольний товар валідний (фікстура коректна)", eva._qualifies_for_feed(OK) is True)
chk("EVA: _qualifies_for_feed відсікає заборонену категорію, ВКЛЮЧНО з промо-SKU", eva._qualifies_for_feed(BAD) is False)
root = eva._build_xml({"1": BAD, "2": OK}, price_overrides={"1": 300.0, "2": 300.0})
ids = {o.get("id") for o in root.find("shop").find("offers")}
chk("EVA: _build_xml не віддає заборонений SKU (дубль-гейт), звичайний — віддає", "1" not in ids and "2" in ids)

# ── 4. Rozetka ──
import generate_rozetka_feed as roz
chk("Rozetka: контрольний товар валідний (фікстура коректна)", roz._qualifies_for_feed(OK, set()) is True)
chk("Rozetka: _qualifies_for_feed відсікає заборонену категорію", roz._qualifies_for_feed(BAD, set()) is False)
_r = roz._build_xml({"1": BAD, "2": OK}, price_overrides={"1": 300.0, "2": 300.0})
rroot = _r[0] if isinstance(_r, tuple) else _r
ids = {o.get("id") for o in rroot.find("shop").find("offers")}
chk("Rozetka: _build_xml не віддає заборонений SKU (реальний гейт), звичайний — віддає", "1" not in ids and "2" in ids)

# ── 5. ALLO: заморожений знімок, що вже містить заборонений SKU, фільтрується при читанні ──
import generate_allo_feed as allo
import json
tmp = Path(tempfile.mkdtemp()) / "allo_static_selection.json"
tmp.write_text(json.dumps({"items": {"1": BAD, "2": OK}, "prices": {"1": 300, "2": 300}}), encoding="utf-8")
allo.ALLO_STATIC_SELECTION_FILE = tmp
items, prices = allo._build_allo_static_selection({"1": BAD, "2": OK})
chk("ALLO: заморожений знімок — заборонений SKU вилучено з items і prices, звичайний лишився", "1" not in items and "1" not in prices and "2" in items and "2" in prices)

# ── 6. Prom повний фід (_build_xml, спільний для prom_feed/prom_feed_top) ──
import generate_prom_feed as pf
try:
    proot, _stats = pf._build_xml({"1": BAD, "2": OK}, price_overrides={"1": 300.0, "2": 300.0})
    ids = {o.get("id") for o in proot.find("shop").find("offers")}
    chk("Prom _build_xml: заборонений SKU не у фіді, звичайний — у фіді", "1" not in ids and "2" in ids)
except Exception as e:  # noqa: BLE001
    chk(f"Prom _build_xml: запуск на синтетиці ({type(e).__name__}: {e})", False)

# ── 7. структурний запобіжник: кожен генератор вітрини зобов'язаний мати політику ──
DIRECT = ["generate_prom_feed.py", "generate_prom_feed_top.py", "generate_eva_feed.py", "generate_rozetka_feed.py", "generate_allo_feed.py", "site/build_site.py"]
VIA_TOP = ["generate_google_feed.py", "generate_meta_feed.py", "generate_bing_feed.py"]       # беруть select_top_items (гейт у is_excluded_category)
EXEMPT = {"generate_prom_redirects.py": "лише редирект-мапа, не вітрина", "generate_royaltoys_feed.py": "інший постачальник; каталог Toysi лише для дедуплікації"}
for f in DIRECT:
    src = (BASE / f).read_text(encoding="utf-8")
    chk(f"структура: {f} імпортує forbidden_products і викликає is_forbidden(", "from forbidden_products import is_forbidden" in src and "is_forbidden(" in src)
for f in VIA_TOP:
    src = (BASE / f).read_text(encoding="utf-8")
    chk(f"структура: {f} бере відбір через select_top_items (політика в is_excluded_category)", "select_top_items" in src)
known = set(DIRECT) | set(VIA_TOP) | set(EXEMPT)
found = {p.name for p in BASE.glob("generate_*.py")} | {"site/build_site.py"}
chk(f"структура: НЕМАЄ невідомого генератора вітрини (додали новий? — внесіть у DIRECT/VIA_TOP/EXEMPT цього тесту): {sorted(found - known)}", not (found - known))

# ── 8. живий каталог / готові фіди ──
def ids_of(path):
    t = Path(path).read_text(encoding="utf-8", errors="replace")
    return set(re.findall(r'<offer id="(\d+)"', t)) | set(re.findall(r"<g:id>(\d+)", t))

if "--live" in sys.argv or "--feeds" in sys.argv:
    from parser import fetch_toysi_catalog
    live = fetch_toysi_catalog()
    bad_live = {p: it for p, it in live.items() if fp.is_forbidden(it)}
    print(f"[live] у каталозі Toysi: {len(bad_live)} SKU заборонених категорій")
    chk("live: каталог має заборонені SKU (інакше тест нічого не перевіряє)", len(bad_live) > 0)
    chk("live: Prom-відбір не містить жодного з них", not (set(top.select_top_items(live)) & set(bad_live)))
    chk("live: EVA _qualifies_for_feed відсікає всі", not any(eva._qualifies_for_feed(it) for it in bad_live.values()))
    chk("live: Rozetka _qualifies_for_feed відсікає всі", not any(roz._qualifies_for_feed(it, set()) for it in bad_live.values()))
    if "--feeds" in sys.argv:
        d = Path(sys.argv[sys.argv.index("--feeds") + 1])
        for fn in sorted(d.glob("*.xml")):
            hit = ids_of(fn) & set(bad_live)
            chk(f"готовий фід {fn.name}: заборонених SKU {len(hit)}", not hit)

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ forbidden_products — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
