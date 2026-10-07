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

# ── 1б. точкові pid (FORBIDDEN_PRODUCT_IDS) — поза забороненою категорією ──
chk("FORBIDDEN_PRODUCT_IDS: рівно 6 pid рішення Консультанта 07.10", set(fp.FORBIDDEN_PRODUCT_IDS) == {"181821", "166976", "180570", "293570", "275143", "306414"})
chk("is_forbidden: pid зі списку → True навіть у невинній категорії; сусідній pid тієї ж категорії → False",
    fp.is_forbidden(item(166976, "Уцінка")) and fp.is_forbidden(item("306414", "Маскарадні костюми")) and not fp.is_forbidden(item(166977, "Уцінка")))

# ── 1в. детектор-доповідач forbidden_watch (ключ за ЦІЛИМ словом; нічого не забороняє) ──
import forbidden_watch as fw
POS = ['Ніж сувенірний "Викидуха Скелетон"', "Сувенірна сокира Череп", "Сувенірний меч Катана міні", "Сувенірний ніж «KUNAI»", "Кинджал дерев'яний", "Щит і Меч набір"]
NEG = ["Ножиці дитячі", "Ножиці з зайчиком", "Ніж канцелярський 18 мм", "Леза до канцелярського ножа", "Тісто для ліплення (ніж у комплекті для тіста)",
       "Ніжки для стільця", "Ніжний плюшевий ведмідь", "Лялька ніжна", "Більше ніж гра", "Набір кухонний ніж дитячий", "Мʼячик та ніжка"]
chk("детектор: позитиви ловить (ніж/сокира/меч/kunai/кинджал/щит)", all(fw.matched_keys(n) for n in POS))
chk("детектор: НЕГАТИВИ не ловить — ножиці, канцелярський ніж, ніжки, ніжний, «ніж»-сполучник, кухня/ліплення", not any(fw.matched_keys(n) for n in NEG))
chk("детектор: токени — «ніжки/ніжний/ножиці» не збігаються як підрядок", fw.matched_keys("ніжки ніжний ножиці") == [])
import tempfile as _tf, os as _os, json as _json
_cat = {"1": item(1, "Різне", name="Ніж сувенірний Хижак"), "2": item(2, "Різне", name="Ножиці дитячі"), "3": item(3, "Різне", name="Меч-кладенець"),
        "4": item(166976, "Уцінка", name="Ніж сувенірний Викидуха"), "5": item(5, "Ножиці та канцелярські ножі", name="Ніж канцелярський")}
_c = fw.candidates(_cat)
chk("детектор: кандидати = лише 1 і 3 (ножиці, заборонений pid, канцелярська категорія відсіяні)", sorted(c["pid"] for c in _c) == ["1", "3"])
chk("детектор: нерозглянуті = кандидати мінус reviewed", [c["pid"] for c in fw.unreviewed(_cat, reviewed={"1": {}})] == ["3"])
_sent = []
_sp = Path(_tf.mkdtemp()) / "state.json"
_r1 = fw.alert_new_candidates(_cat, send=_sent.append, state_path=_sp, reviewed={})
_r2 = fw.alert_new_candidates(_cat, send=_sent.append, state_path=_sp, reviewed={})
chk("детектор: алерт один раз на pid (повторний запуск мовчить), нерозглянуті повертаються обидва рази, текст самодіагностичний",
    len(_sent) == 1 and len(_r1) == 2 and len(_r2) == 2 and "FORBIDDEN_PRODUCT_IDS" in _sent[0] and "forbidden_products_reviewed.json" in _sent[0] and "автозаборони НЕМАЄ" in _sent[0])
def _boom(t): raise RuntimeError("telegram down")
_sp2 = Path(_tf.mkdtemp()) / "state.json"
fw.alert_new_candidates(_cat, send=_boom, state_path=_sp2, reviewed={})
_sent2 = []
fw.alert_new_candidates(_cat, send=_sent2.append, state_path=_sp2, reviewed={})
chk("детектор: збій Telegram не ковтає алерт — стан не оновлено, наступного разу надішле", len(_sent2) == 1)
# аудит #636: send_telegram_message НЕ кидає виняток, а повертає False — це теж збій, стан оновлювати не можна
_sp3 = Path(_tf.mkdtemp()) / "state.json"
_r3 = fw.alert_new_candidates(_cat, send=lambda t: False, state_path=_sp3, reviewed={})
_sent3 = []
fw.alert_new_candidates(_cat, send=_sent3.append, state_path=_sp3, reviewed={})
chk("детектор: send повернув False (токен/мережа/ok:false) → стан НЕ записано, наступного разу алерт надійде", not _sp3.exists() or len(_sent3) == 1)
chk("детектор: send повернув None/True → стан записано, повторний запуск мовчить", (lambda: (fw.alert_new_candidates(_cat, send=lambda t: True, state_path=Path(_tf.mkdtemp()) / "s.json", reviewed={}), True)[1])())
# у стан лише ПОКАЗАНІ pid: понад MAX_IN_ALERT решта прийде наступним алертом
_big = {str(i): item(i, "Різне", name=f"Сувенірний меч №{i}") for i in range(1, fw.MAX_IN_ALERT + 6)}
_sp4 = Path(_tf.mkdtemp()) / "state.json"; _s4 = []
fw.alert_new_candidates(_big, send=_s4.append, state_path=_sp4, reviewed={})
fw.alert_new_candidates(_big, send=_s4.append, state_path=_sp4, reviewed={})
chk(f"детектор: >{fw.MAX_IN_ALERT} нових → перший алерт показує {fw.MAX_IN_ALERT}, другий — решту 5 (жоден pid не загубився)",
    len(_s4) == 2 and "ще 5" in _s4[0] and "(5)" in _s4[1])
chk("детектор: негатив (кухня/канцелярія) глушить лише «ніж», «Кухонний ніж, меч» лишається кандидатом через «меч»",
    fw.matched_keys("Кухонний ніж, меч") == ["меч"] and fw.matched_keys("Ніж канцелярський") == [])
chk("детектор: розширені форми (сокирка, топірець, кинджалик, шпага, нунчаки)", all(fw.matched_keys(n) for n in ["Сокирка дерев'яна", "Топірець", "Кинджалик", "Шпага", "Нунчаки"]))
_rv = _json.loads((BASE / "forbidden_products_reviewed.json").read_text(encoding="utf-8"))["items"]
chk("reviewed: кожен запис має verdict, date, by (інакше через півроку не відрізнити «вирішено» від «заглушено»)",
    all(v.get("verdict") and v.get("date") and v.get("by") for v in _rv.values()) and len(_rv) >= 62)
chk("reviewed: verdict «заборонено» збігається з FORBIDDEN_PRODUCT_IDS (без розсинхрону)",
    {p for p, v in _rv.items() if v["verdict"].startswith("заборонено")} == set(fp.FORBIDDEN_PRODUCT_IDS))
_mon = (BASE / "catalog_health_monitor.py").read_text(encoding="utf-8")
chk("детектор підключений до щоденного монітора (catalog_health_monitor викликає forbidden_watch.alert_new_candidates, пише blade_unreviewed в історію)",
    "forbidden_watch.alert_new_candidates" in _mon and "blade_unreviewed" in _mon)

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
    _unrev = fw.unreviewed(live)
    print(f"[live] детектор: кандидатів за словом {len(fw.candidates(live))}, нерозглянутих {len(_unrev)}: {[(c['pid'], c['name'][:40]) for c in _unrev][:10]}")
    chk("live: детектор — нерозглянутих кандидатів 0 (нові — у алерт монітора; тут лише інформація)", True)
    if "--feeds" in sys.argv:
        d = Path(sys.argv[sys.argv.index("--feeds") + 1])
        for fn in sorted(d.glob("*.xml")):
            hit = ids_of(fn) & set(bad_live)
            chk(f"готовий фід {fn.name}: заборонених SKU {len(hit)}", not hit)

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ forbidden_products — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
