# -*- coding: utf-8 -*-
"""Регрес (11.10.2026, запит Консультанта): фіди Google/Meta/Bing будуються з КАТАЛОГУ ВЛАСНОГО САЙТУ, а не з Prom-топу.

Було: `select_top_items` — відбір під ліміт підписки Prom (6000 SKU, маржа/конкурент/delisted Prom) → у фіді ~5 тис. із ~21 тис. карток сайту.
Тепер: select_feed_catalog = товари з карткою в site/index.json; фільтри, що ЛИШАЮТЬСЯ, названо й перевіряються тут:
  • є картка на сайті (not_on_site) • заборонені (forbidden_products) — власний гейт фіда, не залежить від свіжості індексу
  • «Уцінка» (condition у фіді жорстко «new») • UNO • репліки зброї • без фото • без собівартості
  • збиткові за економікою САЙТУ (внесок ≤ FEED_MIN_SITE_CONTRIBUTION) — це НЕ межа Prom.
Самодостатній, мережа не потрібна."""
import json
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
sys.path.insert(0, str(Path(__file__).parent / "site"))
import competitor_pricing as cp  # noqa: E402
import generate_google_feed as gg  # noqa: E402
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def item(pid, name="Плюшевий ведмідь", vendor="Test", price=400.0, category="М'які іграшки", **kw):
    d = {"id": str(pid), "name": name, "vendor": vendor, "price": price, "stock": 5, "category_name": category, "category_id": "10",
         "pictures": ["https://toysi.ua/p/enl_%s.jpg" % pid], "description": "Опис товару достатньої довжини для фіда.", "barcode": ""}
    d.update(kw)
    return d


def set_index(ids_prices):
    """Підмінює site/index.json у тимчасовій теці й повертає load_site_product_ids()."""
    d = Path(tempfile.mkdtemp())
    (d / "index.json").write_text(json.dumps([{"id": str(i), "n": "x", "pr": p} for i, p in ids_prices.items()]), encoding="utf-8")
    os.environ["SITE_DIR"] = str(d)


# ── один каталог: 1500 звичайних товарів + проблемні
catalog = {str(i): item(i) for i in range(1, 1501)}
bad_forbidden = item(9001, name="Сувенірний меч", category="Мечі, ножі та шаблі")
bad_pid_forbidden = item(166976, name="Ніж сувенірний Викидуха Скелетон", category="Різне")   # точковий pid з FORBIDDEN_PRODUCT_IDS
clear_by_name = item(9002, name="Уцінка. Лялька з дефектом")
clear_by_cat = item(9003, name="Лялька без префікса", category="Уцінка")
no_photo = item(9004, pictures=[])
loss = item(9005, name="Дешева дрібничка", price=12.0)    # ціна сайту ≈ 20 ₴ → внесок після логістики 17.81 ≤ 0
weapon = item(9006, name="Пістолет страйкбол металеві кульки 6 мм")
uno = item(9007, name="Карткова гра UNO Kids", vendor="MiC")
not_on_site = item(9008)
for it in (bad_forbidden, bad_pid_forbidden, clear_by_name, clear_by_cat, no_photo, loss, weapon, uno, not_on_site):
    catalog[it["id"]] = it

# індекс сайту: усе, крім not_on_site (як після build_site: forbidden/UNO/без фото сайт сам не збирає, але індекс може бути НЕСВІЖИМ — тому
# проблемні тут ПЕРЕЛІЧЕНО в індексі навмисно: гейти фіда не мають покладатись на сайт)
index = {pid: float(bs.price_of(it)) for pid, it in catalog.items() if pid != "9008"}   # ціна картки = формула сайту
set_index(index)

# ── select_feed_catalog
chosen, mode = gg.select_feed_catalog(catalog)
chk("режим 'site' і множина = товари з карткою (без поза-сайтового 9008)", mode == "site" and "9008" not in chosen and "1" in chosen and len(chosen) == len(catalog) - 1)

# fallback: індекс недоступний
called = []
gg.select_top_items = lambda c: (called.append(1) or {"1": c["1"]})
os.environ["SITE_DIR"] = tempfile.mkdtemp()   # нема index.json
chosen, mode = gg.select_feed_catalog(catalog)
chk("індекс недоступний → запасний відбір Prom-топом (НЕ весь каталог Toysi)", mode == "prom_top" and list(chosen) == ["1"] and called)
# fallback: індекс є, але збігів < FEED_MIN_SITE_CATALOG
called.clear()
set_index({str(i): 100 for i in range(1, 1100)})                 # індекс валідний (≥1000), але з каталогом перетинається ЛИШЕ 1100 > 1000
set_index({**{str(i): 100 for i in range(5000, 6200)}, "1": 100})   # 1201 id в індексі, збіг з каталогом = 1 << 1000
chosen, mode = gg.select_feed_catalog(catalog)
chk("збігів каталогу з індексом < межі → запасний відбір", mode == "prom_top" and called)

# ── build_feed_items: названі фільтри
set_index(index)
site_ids = gg.load_site_product_ids()
items, stats = gg.build_feed_items({k: v for k, v in catalog.items() if k != "9008"}, {}, {}, {}, None, site_ids)
ids = {i["id"] for i in items}
chk("звичайні товари проходять (1500)", all(str(i) in ids for i in range(1, 1501)) and stats["included"] == len(items))
chk("заборонена КАТЕГОРІЯ не у фіді навіть якщо є в індексі сайту", "9001" not in ids and stats["forbidden"] >= 2)
chk("заборонений ТОЧКОВИЙ pid (166976) не у фіді", "166976" not in ids)
chk("«Уцінка» (за префіксом і за категорією) не у фіді", "9002" not in ids and "9003" not in ids and stats["clearance"] == 2)
chk("без фото — не у фіді", "9004" not in ids and stats["no_image"] >= 1)
chk("збиткова за економікою сайту — не у фіді (site_unprofitable)", "9005" not in ids and stats["site_unprofitable"] >= 1)
chk("репліка зброї — не у фіді", "9006" not in ids)
chk("UNO — не у фіді", "9007" not in ids and stats["trademark_blocked"] == 1)

# ── поріг внеску: від'ємний поріг пускає збиткову, нульовий (дефолт) — ні
old = gg.FEED_MIN_SITE_CONTRIBUTION
gg.FEED_MIN_SITE_CONTRIBUTION = -1000.0
items2, st2 = gg.build_feed_items({"9005": loss}, {}, {}, {}, None, site_ids)
chk("FEED_MIN_SITE_CONTRIBUTION=-1000 → збиткова позиція проходить (поріг справді керує відсівом)", len(items2) == 1 and st2["site_unprofitable"] == 0)
gg.FEED_MIN_SITE_CONTRIBUTION = old
chk("дефолт порогу = 0", old == 0.0)

# ── внесок у фіді == внесок у build_site (одна формула, одне джерело логістики)
chk("логістика на замовлення: build_site бере число з competitor_pricing", bs.SITE_FULFILL_PER_ORDER == cp.SITE_FULFILL_PER_ORDER == gg.SITE_FULFILL_PER_ORDER)
probe = item(9100, price=400.0)
price = float(bs.price_of(probe))
c_site = price - cp.real_toysi_cost(probe) - price * cp.PAYMENT_COMMISSION["site"] - bs.SITE_FULFILL_PER_ORDER
set_index({**index, "9100": price})
site_ids = gg.load_site_product_ids()
its, st = gg.build_feed_items({"9100": probe}, {}, {}, {}, None, site_ids)
chk(f"позиція з внеском {c_site:.1f} ₴ > 0 проходить (формула фіда збігається з build_site)", c_site > 0 and len(its) == 1)

# ── структурно: фіди Google/Meta/Bing беруть select_feed_catalog, а не select_top_items напряму
for f in ("generate_google_feed.py", "generate_meta_feed.py", "generate_bing_feed.py"):
    src = (Path(__file__).parent / f).read_text(encoding="utf-8")
    chk(f"структура: {f} будує фід із select_feed_catalog", "select_feed_catalog(catalog)" in src)
for f in ("generate_meta_feed.py", "generate_bing_feed.py"):
    src = (Path(__file__).parent / f).read_text(encoding="utf-8")
    chk(f"структура: {f} не викликає select_top_items напряму", "select_top_items(" not in src)

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ фіди з каталогу сайту — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
