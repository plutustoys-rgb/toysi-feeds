#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_dropt.py — 04.11.2026 (Код, SITE_CHANNEL 04.11 12:22: dropt_supplier_export пише приватний supplier_dropt.json; сайт показує картки «dropt-<цифри>» ЗА ВОРОТАМИ SITE_DROPT_LIVE).
(1) ворота: лише рівно «1» вмикає; вимкнено → файл навіть не відкривається, вихід збірки байт-у-байт той самий, що без файлу;
(2) читач (ворота ввімкнені): fail-closed — нема/битий/не об'єкт/застарілий файл, картка зі старим checked_at, невідома категорія або ціль поза категоріями Toysi, поганий id, чуже/не https фото → не публікуємо, голосно;
(3) повна збірка на синтетичному файлі: картки є, rt-родина (лише передоплата, поза головною/кошиком, index-rt.json), noindex і поза sitemap без id у dropt_index_ids.json, сегмент sitemap-dropt.xml;
    cost (закупівля) НІДЕ у виході й у логах збірки; XSS у назві/описі екранується; JSON-LD без нових полів."""
import builtins
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["SITE_PICK_DATE"] = "2026-10-10"
os.environ.pop("SITE_DROPT_LIVE", None)
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402
import site_photo_probe  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


COST = 31337          # закупівля «Дроп»: унікальне число, якого не має бути в жодному публічному файлі
COST_F = 4242.17      # дробова закупівля другої картки
DEFAULT_FILE = bs.SUPPLIER_DROPT_FILE
site_photo_probe.run = lambda idx: None
tmp = Path(tempfile.mkdtemp())
now = datetime.now(timezone.utc)
NOW_TS = int(now.timestamp())
LONG = "Автокрісло для дітей від 9 до 36 кг, регульований підголівник, знімний чохол, який можна прати. Кріплення ременями автомобіля."


def good(pid, **kw):
    d = {"name": "Автокрісло дитяче «Подорож» група 1-2-3", "description": LONG, "vendor": "Asson", "vendor_code": pid[6:], "category": "Товари для дітей / Дитячі автокрісла/бустери",
         "images": [f"https://plutustoys.com.ua/img/dropt/{pid}-1.jpg", f"https://plutustoys.com.ua/img/dropt/{pid}-2.jpg"], "price": 1890, "cost": COST, "stock": 3,
         "dims_cm": None, "weight_kg": None, "index": True, "source_url": "https://dropt.in.ua/ua/x", "checked_at": NOW_TS - 3600}
    d.update(kw)
    return d


def write_supplier(items, updated=None, raw=None):
    p = tmp / "supplier_dropt.json"
    if raw is not None:
        p.write_text(raw, encoding="utf-8")
    else:
        p.write_text(json.dumps({"updated": (updated or now).isoformat(timespec="seconds"), "min_checked_at": NOW_TS - 3600, "catalog_n": len(items), "items": items}, ensure_ascii=False), encoding="utf-8")
    bs.SUPPLIER_DROPT_FILE = str(p)


def write_index_ids(ids):
    (tmp / "ids.json").write_text(json.dumps({"ids": ids}), encoding="utf-8")
    bs.DROPT_INDEX_IDS_FILE = str(tmp / "ids.json")


SITE_CATS = {"Автокрісла", "Батарейки", "Машинки"}


def load(items=None, site_cats=SITE_CATS, **kw):
    if items is not None or kw:
        write_supplier(items or {}, **kw)
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        res = bs._load_supplier_dropt(site_cats)
    return res, err.getvalue()


# ── (0) приватність файлу й дані ───────────────────────────────────────────────────────────────────
site_dir = (HERE / "site").resolve()
chk("supplier_dropt.json за замовчуванням — у корені репо, НЕ в site/ (DocumentRoot), і в .gitignore", Path(DEFAULT_FILE).resolve() == (HERE / "supplier_dropt.json").resolve()
    and site_dir not in Path(DEFAULT_FILE).resolve().parents and re.search(r"^/supplier_dropt\.json\s*$", (HERE / ".gitignore").read_text(encoding="utf-8"), re.M) is not None)
cmap = json.loads((HERE / "site" / "dropt_category_map.json").read_text(encoding="utf-8"))
chk("dropt_category_map.json: об'єкт рядок→рядок; непорожні цілі — без слова «Dropt» (постачальника покупець не бачить)", isinstance(cmap, dict) and cmap
    and all(isinstance(k, str) and isinstance(v, str) for k, v in cmap.items()) and not any("dropt" in v.lower() for v in cmap.values()))
ids_doc = json.loads((HERE / "dropt_index_ids.json").read_text(encoding="utf-8"))
chk("dropt_index_ids.json у корені (той самий, що читає експортер): {\"ids\": [...]}, усі id — dropt-<цифри>", isinstance(ids_doc, dict) and isinstance(ids_doc.get("ids"), list)
    and all(re.fullmatch(r"dropt-[0-9]+", str(i)) for i in ids_doc["ids"]))
pt = json.loads((HERE / "site" / "product_texts.json").read_text(encoding="utf-8"))
chk("product_texts.json без dropt-ключів", not any(str(k).startswith("dropt-") for k in pt))

# ── (1) ворота: значення змінної ───────────────────────────────────────────────────────────────────
for val, want in (("1", True), ("0", False), ("", False), ("true", False), (" 1", False), ("yes", False), (None, False)):
    env = {k: v for k, v in os.environ.items() if k != "SITE_DROPT_LIVE"}
    if val is not None:
        env["SITE_DROPT_LIVE"] = val
    r = subprocess.run([sys.executable, "-c", "import sys; sys.path[:0]=[r'%s', r'%s']; import build_site; print(build_site.DROPT_LIVE)" % (HERE / "site", HERE)],
                       env=env, capture_output=True, text=True, cwd=str(HERE))
    chk(f"SITE_DROPT_LIVE={val!r} → DROPT_LIVE={want}", r.stdout.strip().splitlines()[-1:] == [str(want)])
chk("за замовчуванням (тест без змінної) ворота ВИМКНЕНІ", bs.DROPT_LIVE is False)

# ── (2) читач ──────────────────────────────────────────────────────────────────────────────────────
bs.LIQPAY_LIVE = True
write_index_ids(["dropt-101"])
bs.DROPT_LIVE = False
r, e = load({"dropt-101": good("dropt-101")})
chk("ворота вимкнені: читач повертає порожньо й мовчить, навіть коли файл валідний", r == {} and e == "")
bs.DROPT_LIVE = True
r, e = load({"dropt-101": good("dropt-101"), "dropt-102": good("dropt-102", cost=COST_F, index=False)})
it = r.get("dropt-101", {})
chk("валідна картка: категорія сайту з мапи, ціна сайту, фото нашого сервера, залишок, без помилок", it.get("category_name") == "Автокрісла" and it.get("site_price") == 1890
    and it.get("pictures") == good("dropt-101")["images"] and it.get("stock") == 3 and e == "")
chk("cost не переноситься в картку взагалі (ні ключа, ні числа)", all("cost" not in x for x in r.values()) and str(COST) not in json.dumps(r) and "4242" not in json.dumps(r))
chk("price_of бере ціну з файлу", bs.price_of(it) == 1890)
chk("індексація: index=true і id у dropt_index_ids → індексується; index=false → noindex", it.get("rt_noindex") is False and r["dropt-102"]["rt_noindex"] is True)
write_index_ids([])
r, e = load({"dropt-101": good("dropt-101")})
chk("index=true, але id нема в dropt_index_ids.json → noindex", r["dropt-101"]["rt_noindex"] is True)
write_index_ids(["dropt-101"])
r, e = load({"dropt-101": good("dropt-101", description="Коротко.")})
chk("тонкий опис (<50 знаків) → noindex навіть з id у списку", r["dropt-101"]["rt_noindex"] is True)

r, e = load({"dropt-101": good("dropt-101", checked_at=NOW_TS - 7 * 3600), "dropt-102": good("dropt-102", checked_at=NOW_TS - 5 * 3600),
             "dropt-103": good("dropt-103", checked_at=None), "dropt-104": good("dropt-104", checked_at=NOW_TS + 3600), "dropt-105": good("dropt-105", checked_at=True),
             "dropt-106": good("dropt-106", checked_at="1730000000")})
chk("checked_at: 7 год → ні; 5 год → так; нема/майбутнє/bool/рядок → ні; голосно", set(r) == {"dropt-102"} and "checked_at" in e)

r, e = load({"dropt-101": good("dropt-101", category="Невідома / Щось"), "dropt-102": good("dropt-102", category="Акустика / Навушники"),
             "dropt-103": good("dropt-103", category="Новий Рік / Новорічні гірлянди"), "dropt-104": good("dropt-104", category=None),
             "dropt-105": good("dropt-105", category="Електроніка / Акумуляторні батарейки")})
chk("невідома категорія / ціль «» у мапі / ціль поза категоріями Toysi цієї збірки / нема категорії → ні; відома й існуюча → так", set(r) == {"dropt-105"}
    and r["dropt-105"]["category_name"] == "Батарейки" and "category:" in e)

# ── покартковий файл dropt_item_category.json (07.11.2026): категорії Dropt брудні → запис по id має пріоритет над мапою категорій ──
_orig_item_file = bs.DROPT_ITEM_CATEGORY_FILE


def with_item_map(content, items, site_cats=SITE_CATS):
    f = tmp / "item_cat.json"
    if content is None:
        if f.exists():
            f.unlink()
    else:
        f.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False), encoding="utf-8")
    bs.DROPT_ITEM_CATEGORY_FILE = str(f)
    try:
        return load(items, site_cats=site_cats)
    finally:
        bs.DROPT_ITEM_CATEGORY_FILE = _orig_item_file


_dirty = {"dropt-201": good("dropt-201", category="Електроніка / Товари для ПК"), "dropt-202": good("dropt-202", category="Невідома / Щось"),
          "dropt-203": good("dropt-203", category="Електроніка / Акумуляторні батарейки"), "dropt-204": good("dropt-204", category=None)}
r, e = with_item_map({"dropt-201": "Машинки", "dropt-204": "Автокрісла", "dropt-203": "Автокрісла"}, _dirty)
chk("покартковий запис: товар зі «брудною» категорією Dropt (і без категорії) отримує категорію з файлу; запис перебиває мапу категорій; без запису й без мапи — ні",
    set(r) == {"dropt-201", "dropt-203", "dropt-204"} and r["dropt-201"]["category_name"] == "Машинки" and r["dropt-203"]["category_name"] == "Автокрісла"
    and r["dropt-204"]["category_name"] == "Автокрісла" and "dropt-202" not in r)
r, e = with_item_map(None, _dirty)
chk("файлу нема → як раніше (лише мапа категорій): без падіння, брудний id без запису не публікується", isinstance(r, dict) and "dropt-201" not in r and "dropt-204" not in r)
r, e = with_item_map("{not json", _dirty)
chk("файл битий → як раніше (мапа категорій), без падіння збірки, з попередженням у лозі", isinstance(r, dict) and "dropt-201" not in r and "dropt_item_category.json" in e)
r, e = with_item_map(None, _dirty)
chk("файлу нема → попередження у лозі (не мовчки)", "dropt_item_category.json" in e)
r, e = with_item_map(["dropt-201"], _dirty)
chk("файл не об'єкт → ігнорується", "dropt-201" not in r)
r, e = with_item_map({"dropt-201": "Нема такої категорії", "dropt-202": "", "dropt-203": 5, "dropt-204": None}, _dirty)
chk("ціль поза категоріями Toysi цієї збірки / порожній рядок / не рядок (5, null) → картку не публікуємо й не віддаємо мапі категорій (category-target:)", not ({"dropt-201", "dropt-202", "dropt-203", "dropt-204"} & set(r)) and "category-target:" in e)
r, e = with_item_map({"dropt-201": "Машинки"}, {"dropt-201": good("dropt-201", category="Електроніка / Товари для ПК"), "dropt-999": good("dropt-999", category="Електроніка / Товари для ПК")})
chk("запис лише для одного id не відкриває сусіда з тією самою брудною категорією", set(r) == {"dropt-201"})
chk("мапа категорій: жоден НЕпорожній ключ не збігається з реальними рядками брудних категорій Dropt (через кому: «Дитячі автокрісла, бустери», «Акумуляторні батарейки, батареї, ЗП») — щоб їх не «виправили» і не відкрили автокрісла/батарейки цілою категорією",
    not any(v and ("автокрісла, бустери" in k or "Акумуляторні батарейки, батареї" in k) for k, v in cmap.items()))
_item_doc = json.loads((HERE / "site" / "dropt_item_category.json").read_text(encoding="utf-8"))
chk("реальний site/dropt_item_category.json: об'єкт; ключі dropt-<цифри>; цілі — непорожні рядки без слова «Dropt», без дублікатів ключів", isinstance(_item_doc, dict)
    and all(re.fullmatch(r"dropt-[0-9]{1,12}", k) for k in _item_doc) and all(isinstance(v, str) and v.strip() and "dropt" not in v.lower() for v in _item_doc.values()))

bad_imgs = ["http://plutustoys.com.ua/img/dropt/dropt-101-1.jpg", "https://evil.example/img/dropt/dropt-101-1.jpg", "https://plutustoys.com.ua.evil.com/img/dropt/dropt-101-1.jpg",
            "https://plutustoys.com.ua/img/dropt/../../supplier_dropt.json", "https://plutustoys.com.ua/img/dropt/dropt-999-1.jpg", "https://plutustoys.com.ua/img/rt/dropt-101-1.jpg",
            "javascript:alert(1)", "https://plutustoys.com.ua/img/dropt/dropt-101-1.jpg\" onerror=\"x", 7]
r, e = load({"dropt-101": good("dropt-101", images=bad_imgs), "dropt-102": good("dropt-102", images=bad_imgs + ["https://plutustoys.com.ua/img/dropt/dropt-102-3.jpg"]),
             "dropt-103": good("dropt-103", images="https://plutustoys.com.ua/img/dropt/dropt-103-1.jpg")})
chk("фото: лише https://plutustoys.com.ua/img/dropt/<цей id>-N.jpg; решта відкинута; без жодного валідного → картки нема", set(r) == {"dropt-102"}
    and r["dropt-102"]["pictures"] == ["https://plutustoys.com.ua/img/dropt/dropt-102-3.jpg"] and "images" in e)

r, e = load({"dropt-12a": good("dropt-12a"), "dropt-١٢": good("dropt-١٢"), "rt-5": good("rt-5"), "dropt-": good("dropt-"), "DROPT-7": good("DROPT-7"), "dropt-7 ": good("dropt-7 "),
             "dropt-8": "not a dict", "dropt-9": good("dropt-9", name="  "), "dropt-10": good("dropt-10", price=0), "dropt-11": good("dropt-11", price=True), "dropt-12": good("dropt-12", stock=-1),
             "dropt-13": good("dropt-13", name={"x": 1})})
chk("поганий id (не dropt-<ASCII-цифри>) / не об'єкт / нема назви / ціна 0 або bool / від'ємний залишок → ні, голосно", r == {} and "supplier_dropt.json: не опубліковано" in e and "id" in e)

# аудит, знахідка 4: суворі price/stock (лише int або float без дробу; межі), назва (довжина, керівні й bidi-символи), updated не з майбутнього
r, e = load({"dropt-21": good("dropt-21", price="1890"), "dropt-22": good("dropt-22", price=1890.5), "dropt-23": good("dropt-23", price=1890.0), "dropt-24": good("dropt-24", price=1_000_001),
             "dropt-25": good("dropt-25", price=1_000_000), "dropt-26": good("dropt-26", stock="3"), "dropt-27": good("dropt-27", stock=2.0), "dropt-28": good("dropt-28", stock=100_001),
             "dropt-29": good("dropt-29", stock=3.5), "dropt-30": good("dropt-30", price=float("nan")), "dropt-31": good("dropt-31", stock=False), "dropt-32": good("dropt-32", price=-5),
             "dropt-33": good("dropt-33", price=[1890]), "dropt-34": good("dropt-34", stock=0)})
chk("price/stock: рядок/дріб/NaN/bool/список/від'ємне/понад межу (ціна 1 000 000, залишок 100 000) → ні; 1890.0, 2.0, рівно межа, залишок 0 → так (ціле int)",
    set(r) == {"dropt-23", "dropt-25", "dropt-27", "dropt-34"} and r["dropt-23"]["site_price"] == 1890 and type(r["dropt-23"]["site_price"]) is int
    and r["dropt-27"]["stock"] == 2 and type(r["dropt-27"]["stock"]) is int and "price/stock" in e)
r, e = load({"dropt-41": good("dropt-41", name="А" * 300), "dropt-42": good("dropt-42", name="А" * 301), "dropt-43": good("dropt-43", name="Лялька\x07 дзвоник"),
             "dropt-44": good("dropt-44", name="Лялька\nз переносом"), "dropt-45": good("dropt-45", name="Лялька ‮абв"), "dropt-46": good("dropt-46", name="Лялька ⁦x⁩"),
             "dropt-47": good("dropt-47", name="Лялька\x00"), "dropt-48": good("dropt-48", name="Лялька \x7f"), "dropt-49": good("dropt-49", name="М'яка лялька «Соня» & друзі")})
chk("назва: ≤300 → так, 301 → ні; керівні символи (Cc: \\x07, \\n, \\x00, \\x7f) і bidi U+202A–202E / U+2066–2069 → ні; звичайні лапки/апостроф/& → так",
    set(r) == {"dropt-41", "dropt-49"} and "name" in e)
r, e = load({"dropt-101": good("dropt-101")}, updated=now + timedelta(hours=2))
chk("«updated» з майбутнього більш ніж на 1 год → файл відкинуто, голосно", r == {} and "майбутнього" in e)
r, e = load({"dropt-101": good("dropt-101")}, updated=now + timedelta(minutes=30))
chk("«updated» на 30 хв уперед (розбіжність годинників) → приймається", set(r) == {"dropt-101"} and e == "")

for label, kw in (("файл застарів (4 доби > 3)", {"updated": now - timedelta(days=4)}),):
    r, e = load({"dropt-101": good("dropt-101")}, **kw)
    chk(f"{label} → порожньо, голосно", r == {} and "застарів" in e)
for label, raw, msg in (("битий JSON", "{не json", "НЕ прочитано"), ("не об'єкт (масив)", "[1, 2]", "очікується"), ("items не об'єкт", '{"updated": "2026-11-04T00:00:00+00:00", "items": []}', "очікується"),
                        ("нема updated", '{"items": {}}', "updated")):
    r, e = load(raw=raw)
    chk(f"{label} → порожньо, голосно, без падіння", r == {} and msg in e)
bs.SUPPLIER_DROPT_FILE = str(tmp / "nope.json")
r, e = load()
chk("файлу нема (ворота ввімкнені) → порожньо, попередження", r == {} and "нема" in e)
bs.LIQPAY_LIVE = False
r, e = load({"dropt-101": good("dropt-101")})
chk("без онлайн-оплати (SITE_LIQPAY_LIVE) — не публікуємо, як RoyalToys", r == {} and "онлайн-оплата не ввімкнена" in e)
bs.LIQPAY_LIVE = True
chk("код товару для покупця: dropt-101 → PT-D101 (rt/Toysi без змін)", bs.site_product_code("dropt-101") == "PT-D101" and bs.site_product_code("rt-5") == "PT-R5" and bs.site_product_code("5") == "PT-5")


# ── (3) повна збірка ───────────────────────────────────────────────────────────────────────────────
def toysi(pid, name, cat):
    return {"id": str(pid), "name": name, "vendor": "T", "price": 200 + pid % 200, "stock": 5, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/p/{pid}"}


catalog = {str(i): toysi(i, f"Іграшка {i}", ("Автокрісла", "Батарейки", "Машинки")[i % 3]) for i in range(1, 41)}
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.CATEGORY_GROUPS_FILE = str(tmp / "none.json")
bs.SUPPLIER_RT_FILE = str(tmp / "no_rt.json")
XSS_NAME = 'Іграшка <script>alert("n")</script> & «лапки» \' "'
ENT_DESC = 'М\'який чохол & ремені "Isofix" — крісло для дітей від 9 до 36 кг, знімний чохол можна прати.'
XSS_DESC = 'Гарна іграшка для дітей <img src=x onerror=alert(1)> </p><script>alert(2)</script> — довгий опис понад п\'ятдесят знаків, щоб не тонкий.'
ITEMS = {"dropt-101": good("dropt-101"), "dropt-102": good("dropt-102", name="Акумуляторні батарейки АА 4 шт", category="Електроніка / Акумуляторні батарейки", cost=COST_F, price=150),
         "dropt-103": good("dropt-103", name=XSS_NAME, description=XSS_DESC, category="Електроніка / Акумуляторні батарейки"),
         "dropt-104": good("dropt-104", name="Застарілий товар", checked_at=NOW_TS - 8 * 3600), "dropt-105": good("dropt-105", name="Невідомий розділ", category="Акустика / Навушники"),
         "dropt-106": good("dropt-106", name="Чуже фото", images=["https://evil.example/a.jpg"]), "dropt-107": good("dropt-107", name="Скарбничка", category="Товари для дітей / Дитячі скарбнички/сейфи"),
         "dropt-12x": good("dropt-12x", name="Поганий id"),
         "dropt-108": good("dropt-108", name="Автокрісло з базою", description=ENT_DESC)}


def run_build(live, supplier_present):
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    bs.DROPT_LIVE = live
    if supplier_present:
        write_supplier(ITEMS)
    else:
        bs.SUPPLIER_DROPT_FILE = str(tmp / "absent.json")
    opened = []
    real_open = builtins.open

    def spy_open(f, *a, **k):
        opened.append(str(f))
        return real_open(f, *a, **k)
    so, se = io.StringIO(), io.StringIO()
    builtins.open = spy_open
    try:
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            bs.build()
    finally:
        builtins.open = real_open
    return out, so.getvalue() + se.getvalue(), opened


def tree(d):
    """{файл: байти}. .htlastmod_state.json (службовий, Apache не віддає) містить епоху моменту збірки «t» — порівнюємо лише хеші сторінок «h»."""
    t = {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}
    if bs.LASTMOD_STATE_FILE in t:
        t[bs.LASTMOD_STATE_FILE] = json.dumps({k: (v or {}).get("h") for k, v in json.loads(t[bs.LASTMOD_STATE_FILE]).items()}, sort_keys=True).encode()
    return t


write_index_ids(["dropt-101", "dropt-103"])
out0, log0, _ = run_build(False, False)
out1, log1, opened1 = run_build(False, True)
t0, t1 = tree(out0), tree(out1)
diff = sorted(k for k in set(t0) | set(t1) if t0.get(k) != t1.get(k))
chk(f"ворота вимкнені + валідний supplier_dropt.json: вихід збірки байт-у-байт той самий, що без файлу ({len(t0)} файлів; різниця: {diff[:5]})", t0 and diff == [])
chk("ворота вимкнені: supplier_dropt.json, мапа категорій і dropt_index_ids НЕ відкривались", not any(p in (bs.SUPPLIER_DROPT_FILE, bs.DROPT_CATEGORY_MAP_FILE, bs.DROPT_INDEX_IDS_FILE) for p in opened1))
chk("ворота вимкнені: у виході й лозі збірки нема жодного dropt", not any(b"dropt" in v.lower() for v in t1.values()) and "dropt" not in log1.lower())

out, log, _ = run_build(True, True)
T = tree(out)
idx = json.loads((out / "index.json").read_text(encoding="utf-8"))
ix = {x["id"]: x for x in idx}
chk("ворота ввімкнені: картки 101/102/103 є (сторінки + index.json), 104 (checked_at 8 год)/105 (невідома категорія)/106 (чуже фото)/107 (ціль поза Toysi)/12x (id) — нема",
    all((out / f"product-dropt-{i}.html").exists() and f"dropt-{i}" in ix for i in (101, 102, 103))
    and not any((out / f"product-dropt-{i}.html").exists() or f"dropt-{i}" in ix for i in ("104", "105", "106", "107", "12x")))
chk("лог збірки пояснює відкинуті картки (checked_at, category, images)", all(w in log for w in ("checked_at", "category:", "category-target:", "images", "id")))
blob = b"".join(T.values())
chk("cost (31337 / 4242.17) НІДЕ у виході збірки (html, index*.json, sitemap, JSON-LD) і ні слова «cost» у index.json", str(COST).encode() not in blob and b"4242" not in blob and all("cost" not in x for x in idx))
chk("cost нема і в лозі збірки (stdout+stderr)", str(COST) not in log and "4242" not in log)
chk("постачальника покупець не бачить: ні «Dropt»/«dropt.in.ua», ні source_url у виході", b"Dropt" not in blob and b"dropt.in.ua" not in blob)
h101 = (out / "product-dropt-101.html").read_text(encoding="utf-8")
main101 = h101.split("<footer")[0]
chk("картка dropt-101: ціна 1890, фото нашого сервера, код PT-D101, категорія «Автокрісла»", "1890" in h101 and "img/dropt/dropt-101-1.jpg" in h101 and "PT-D101" in h101 and "Автокрісла" in h101)
chk("картка dropt-101 (rt-механізм): «Оплата карткою онлайн», «лише за передоплатою», без «Оплата при отриманні»/«накладеному платежі»", "✓ Оплата карткою онлайн" in main101
    and "лише за передоплатою" in main101 and "Оплата при отриманні" not in main101 and "накладеному платежі" not in main101)
chk("index.json: dropt-записи з rt=1 і ix (101 — 1, 102 — 0: id нема у списку), ціна сайту", ix["dropt-101"].get("rt") == 1 and ix["dropt-101"].get("ix") == 1 and ix["dropt-102"].get("ix") == 0 and ix["dropt-101"]["pr"] == 1890)
it_ = json.loads((out / "index-t.json").read_text(encoding="utf-8"))
irt = json.loads((out / "index-rt.json").read_text(encoding="utf-8"))
chk("index-t.json без dropt; index-rt.json (довантажується у фоні) — з dropt", not any(x["id"].startswith("dropt-") for x in it_) and {"dropt-101", "dropt-102", "dropt-103"} <= {x["id"] for x in irt})
h102 = (out / "product-dropt-102.html").read_text(encoding="utf-8")
chk("dropt-102 (нема в dropt_index_ids): <meta robots noindex,follow>; dropt-101 — без noindex", 'content="noindex,follow"' in h102 and "noindex" not in h101.split("</head>")[0])
smd = (out / "sitemap-dropt.xml").read_text(encoding="utf-8") if (out / "sitemap-dropt.xml").exists() else ""
other_sm = "".join(v.decode("utf-8") for k, v in T.items() if k.startswith("sitemap") and k != "sitemap-dropt.xml")
chk("sitemap-dropt.xml: лише індексовані dropt (101, 103); 102 (noindex) — ні; у products/priority/rt dropt-карток нема; індекс sitemap.xml посилається на сегмент",
    "/product-dropt-101.html" in smd and "/product-dropt-103.html" in smd and "/product-dropt-102.html" not in smd and "product-dropt-" not in other_sm
    and "sitemap-dropt.xml" in (out / "sitemap.xml").read_text(encoding="utf-8"))
chk("dropt-картки НЕ на головній і НЕ в кошикових add-on", "product-dropt-" not in (out / "index.html").read_text(encoding="utf-8") and "dropt-" not in (out / "cart.html").read_text(encoding="utf-8"))
chk("нових сторінок категорій Dropt нема (лише існуючі категорії Toysi)", sorted(k for k in T if k.startswith("category-")) == sorted(k for k in t0 if k.startswith("category-")))
cat_pg = next((v.decode("utf-8") for k, v in T.items() if k.startswith("category-") and b"product-dropt-101" in v), "")
order = re.findall(r'"url": "https://plutustoys.com.ua/product-([^"]+)\.html"', cat_pg)
chk("у категорії dropt-картки після Toysi (rec_order)", "dropt-101" in order and not order[0].startswith("dropt-") and [x.startswith("dropt-") for x in order] == sorted(x.startswith("dropt-") for x in order))
h103 = (out / "product-dropt-103.html").read_text(encoding="utf-8")
chk("XSS: назва й опис екрановані (нема сирих <script>alert / <img … onerror>), видно &lt;script&gt;", "<script>alert(" not in h103 and "<img src=x onerror" not in h103
    and "&lt;script&gt;alert" in h103 and "&lt;img src=x onerror=alert(1)&gt;" in h103)
lds = [json.loads(m) for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', h101, re.S)]
ld = next(x for x in lds if x.get("@type") == "Product")
ld_t = next(x for x in (json.loads(m) for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', (out / "product-1.html").read_text(encoding="utf-8"), re.S)) if x.get("@type") == "Product")
chk("JSON-LD dropt: поля ті самі, що в картки Toysi (без нових полів про доставку), offers.price = 1890", set(ld) <= set(ld_t) | {"brand"} and set(ld["offers"]) == set(ld_t["offers"])
    and ld["offers"]["price"] == "1890" and "shippingDetails" not in ld["offers"])
ld103 = re.findall(r'<script type="application/ld\+json">(.*?)</script>', h103, re.S)
chk("JSON-LD dropt-103: жодного сирого «<» усередині (XSS не виходить зі <script>)", ld103 and all("<" not in s for s in ld103))
ld103p = next(x for x in (json.loads(s) for s in ld103) if x.get("@type") == "Product")
chk("JSON-LD dropt-103: опис розкодований (у значенні «<script>», у сирому тексті — \\u003c), без «&lt;»", "<script>alert(2)" in ld103p["description"] and "&lt;" not in ld103p["description"])
h108 = (out / "product-dropt-108.html").read_text(encoding="utf-8")
ld108_raw = re.findall(r'<script type="application/ld\+json">(.*?)</script>', h108, re.S)
ld108 = next(x for x in (json.loads(s) for s in ld108_raw) if x.get("@type") == "Product")
chk("аудит, знахідка 3: JSON-LD description dropt — чисті символи (М'який, &, \"Isofix\"), без &#x27; / &amp; / &quot;", "М'який чохол & ремені \"Isofix\"" in ld108["description"]
    and not any(s in "".join(ld108_raw) for s in ("&#x27;", "&amp;", "&quot;")))
body108 = h108.split("</head>")[1]
chk("…а в тілі сторінки опис і далі екранований (М&#x27;який … &amp; … &quot;Isofix&quot;)", "М&#x27;який чохол &amp; ремені &quot;Isofix&quot;" in body108)
meta108 = re.search(r'<meta name="description" content="([^"]*)"', h108).group(1)
chk("meta description dropt — шаблон із назвою (як rt), без подвійного екранування", "Автокрісло з базою" in meta108 and "&amp;amp;" not in meta108 and "&amp;#x27;" not in meta108)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
