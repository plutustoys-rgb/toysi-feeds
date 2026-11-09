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
_CM_FIX = {
 "Товари для дітей / Ігрові килимки та намети": "",
 "Товари для дітей / Ігрові набори": "",
 "Товари для дітей / Інтерактивні іграшки": "",
 "Товари для дітей / Аксесуари для дітей": "",
 "Товари для дітей / Геймпади/ігрові контролери": "",
 "Товари для дітей / Дитячі автокрісла/бустери": "Автокрісла",
 "Товари для дітей / Дитячі скарбнички/сейфи": "Скарбнички і шкатулки",
 "Товари для дітей / Караоке мікрофони": "",
 "Товари для дітей / Конструктори/головоломки": "",
 "Товари для дітей / Радіокеровані іграшки/квадрокоптери": "",
 "Товари для дітей / Розвиток і творчість": "",
 "Товари для дітей / Ходунки/гойдалки": "",
 "Акустика / Power bank": "",
 "Акустика / Акустичні системи": "",
 "Акустика / Мікрофони": "",
 "Акустика / Навушники": "",
 "Акустика / Підсилювачі": "",
 "Акустика / Портативні колонки": "",
 "Акустика / Радіоприймачі": "",
 "Електроніка / TV-приставки/тюнери/проєктори": "",
 "Електроніка / Ігрові приставки": "",
 "Електроніка / Аксесуари для електроніки": "",
 "Електроніка / Акумуляторні батарейки": "Батарейки",
 "Електроніка / Електровимірювальні прилади": "",
 "Електроніка / Світлодіодні стрічки/вивіски": "",
 "Електроніка / Товари для ПК": "",
 "Туризм, відпочинок і спорт / Аксесуари для туризму та спорту": "",
 "Туризм, відпочинок і спорт / Велосипеди": "",
 "Туризм, відпочинок і спорт / Екшн-камери": "",
 "Туризм, відпочинок і спорт / Літній відпочинок": "",
 "Туризм, відпочинок і спорт / ЛІТО": "",
 "Новий Рік / Диско-лампи/кулі/лазерні проєктори": "",
 "Новий Рік / Новорічні гірлянди": "",
 "Новий Рік / Штучна ялинка": "Новорічний декор",
 "Товари для тварин": "Товари для тварин",
 "Рюкзаки, сумки, портмоне": "Невідома власна"
}
(tmp / "cmap_fixture.json").write_text(json.dumps(_CM_FIX, ensure_ascii=False), encoding="utf-8")
_REAL_CMAP_FILE = bs.DROPT_CATEGORY_MAP_FILE
_REAL_ITEM_FILE = bs.DROPT_ITEM_CATEGORY_FILE
_REAL_STATE_FILE = bs.DROPT_UNMAPPED_STATE_FILE
bs.DROPT_UNMAPPED_STATE_FILE = str(tmp / "unmapped_state.json")
bs.DROPT_CATEGORY_MAP_FILE = str(tmp / "cmap_fixture.json")      # читацькі тести — на фіксованій мапі (реальна перевіряється нижче окремо)
(tmp / "empty_item_cat.json").write_text("{}", encoding="utf-8")
bs.DROPT_ITEM_CATEGORY_FILE = str(tmp / "empty_item_cat.json")   # покартковий файл у читацьких тестах — порожній об'єкт, якщо тест не підставляє свій
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
cmap_real = json.loads(Path(_REAL_CMAP_FILE).read_text(encoding="utf-8"))
_own = set(bs.DROPT_OWN_CATEGORIES)
chk("реальна мапа категорій: об'єкт рядок→рядок; без слова «dropt»; цілі — порожні, власні категорії Dropt або «Гірлянди»", isinstance(cmap_real, dict)
    and all(isinstance(k, str) and isinstance(v, str) for k, v in cmap_real.items()) and not any("dropt" in v.lower() for v in cmap_real.values())
    and all(v == "" or v in _own or v == "Гірлянди" for k, v in cmap_real.items() if not k.startswith("_")))
chk("реальна мапа: брудні розділи «Товари для дітей» і «Новий Рік» НЕ мають фолбеку (тільки покартково); недитячі розділи мають",
    all(v == "" for k, v in cmap_real.items() if k.startswith(("Товари для дітей", "Новий Рік")))
    and all(cmap_real.get(k) for k in ("Акустика / Навушники", "Електроніка / Товари для ПК", "Товари для тварин", "Рюкзаки, сумки, портмоне", "Туризм, відпочинок і спорт / Літній відпочинок")))
chk("реальна мапа: ключі зі слешем (мертві, не збігаються з даними) відсутні", not any("/" in k.split(" / ", 1)[-1] for k in cmap_real if " / " in k))
item_real = json.loads(Path(_REAL_ITEM_FILE).read_text(encoding="utf-8"))
chk("реальний покартковий файл: ≥ 700 записів, ключі dropt-<цифри>, цілі — рядки без «dropt» (\"\" = свідомо не публікуємо, таких < 10 %); ≥ 700 непорожніх; усі 5 власних категорій представлені", isinstance(item_real, dict) and len(item_real) >= 700
    and all(re.fullmatch(r"dropt-[0-9]{1,12}", k) and isinstance(v, str) and "dropt" not in v.lower() for k, v in item_real.items())
    and sum(1 for v in item_real.values() if v.strip()) >= 700 and sum(1 for v in item_real.values() if not v.strip()) < 0.10 * len(item_real) and _own <= set(item_real.values()))
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
_item_doc = json.loads((HERE / "site" / "dropt_item_category.json").read_text(encoding="utf-8"))
chk("реальний site/dropt_item_category.json: об'єкт; ключі dropt-<цифри>; цілі — непорожні рядки без слова «Dropt», без дублікатів ключів", isinstance(_item_doc, dict)
    and all(re.fullmatch(r"dropt-[0-9]{1,12}", k) for k in _item_doc) and all(isinstance(v, str) and "dropt" not in v.lower() for v in _item_doc.values()))

r, e = with_item_map({"dropt-301": "Навушники та колонки", "dropt-302": "Невідома власна"}, {"dropt-301": good("dropt-301", category="Акустика / Навушники"), "dropt-302": good("dropt-302", category="Акустика / Навушники")})
chk("власна категорія Dropt («Навушники та колонки») дозволена як ціль без існування в Toysi; довільна ціль поза Toysi і власними — ні",
    set(r) == {"dropt-301"} and r["dropt-301"]["category_name"] == "Навушники та колонки" and "category-target:" in e)
r, e = load({"dropt-303": good("dropt-303", category="Товари для тварин"), "dropt-304": good("dropt-304", category="Рюкзаки, сумки, портмоне")})
chk("фолбек мапи категорій дає власну категорію новому id без покартового запису; мапа з ціллю поза Toysi/власними — ні", set(r) == {"dropt-303"} and r["dropt-303"]["category_name"] == "Товари для тварин")

# dropt_unmapped_state.json: id, відкинуті ЛИШЕ через відсутню/недоступну категорію (не через checked_at/фото/свідоме виключення), лише id, атомарно
import os as _os
_items = {"dropt-401": good("dropt-401", category="Невідома / Щось"), "dropt-402": good("dropt-402", category="Невідома / Щось"), "dropt-403": good("dropt-403", category="Невідома / Щось", checked_at=NOW_TS - 8 * 3600),
          "dropt-404": good("dropt-404", category="Невідома / Щось"), "dropt-405": good("dropt-405", category="Невідома / Щось")}
r, e = with_item_map({"dropt-402": "Нема такої категорії", "dropt-404": "", "dropt-405": "Машинки"}, _items)
_st = json.loads(Path(bs.DROPT_UNMAPPED_STATE_FILE).read_text(encoding="utf-8"))
chk("стан нерозмічених: лише 401 (нема запису) і 402 (ціль поза категоріями); 403 (checked_at) і 404 (свідомо \"\") — ні; 405 опубліковано", set(r) == {"dropt-405"} and _st["ids"] == ["dropt-401", "dropt-402"]
    and _st["n"] == 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", _st["updated"]) and set(_st) == {"updated", "n", "ids"} and not _os.path.exists(bs.DROPT_UNMAPPED_STATE_FILE + ".tmp"))
chk("свідоме виключення (\"\" у файлі) не публікується й не рахується нерозміченим", "dropt-404" not in r and "dropt-404" not in _st["ids"])
chk("стан нерозмічених: лише id (без назв/цін/cost) і не у публічному site/", "cost" not in json.dumps(_st) and str(COST) not in json.dumps(_st) and (HERE / "site") not in Path(_REAL_STATE_FILE).resolve().parents
    and Path(_REAL_STATE_FILE).resolve().parent == HERE.resolve())
_big = {f"dropt-{900000 + i}": good(f"dropt-{900000 + i}", category="Невідома / Щось") for i in range(1500)}
r, e = with_item_map({}, _big)
_st = json.loads(Path(bs.DROPT_UNMAPPED_STATE_FILE).read_text(encoding="utf-8"))
chk("стан нерозмічених: ≤ 1200 id (файл < 20 КБ), n — справжня кількість", len(_st["ids"]) == bs.DROPT_UNMAPPED_MAX_IDS and _st["n"] == 1500 and len(json.dumps(_st)) < 20000)

# фолбек мапи: закритий, коли покартковий файл відсутній/битий; чорний список назв; реальні файли — усі виключені не публікуються
_fb = {"dropt-501": good("dropt-501", name="Годівниця для кота", category="Товари для тварин")}
r, e = with_item_map(None, _fb)
chk("покартковий файл відсутній → фолбек мапи ЗАКРИТИЙ (нічого без перегляду не публікуємо), з попередженням", not r and "dropt_item_category.json" in e)
r, e = with_item_map("{not json", _fb)
chk("покартковий файл битий → фолбек мапи закритий", not r)
r, e = with_item_map({}, _fb)
chk("покартковий файл є (порожній об'єкт) → фолбек працює для недитячого розділу", set(r) == {"dropt-501"})
_deny = {"dropt-502": good("dropt-502", name="GSM трекер Mini A8 з прослуховуванням", category="Товари для тварин"), "dropt-503": good("dropt-503", name="Лазерна вказівка високої потужності", category="Товари для тварин"),
         "dropt-504": good("dropt-504", name="Набір виживання 10 в 1", category="Товари для тварин"), "dropt-505": good("dropt-505", name="Електронний ошийник для собак", category="Товари для тварин"),
         "dropt-506": good("dropt-506", name="Набір неодимових магнітів Неокуб", category="Товари для тварин"), "dropt-507": good("dropt-507", name="Годівниця для кота", category="Товари для тварин")}
r, e = with_item_map({}, _deny)
_st2 = json.loads(Path(bs.DROPT_UNMAPPED_STATE_FILE).read_text(encoding="utf-8"))
chk("чорний список назв: через фолбек НЕ публікуються прилади прослуховування, лазери, набори виживання, ошийники, неодимові магніти; звичайний товар — так; відсічені потрапляють у «на перегляд» (dropt_unmapped_state.json), а не зникають мовчки",
    set(r) == {"dropt-507"} and {"dropt-502", "dropt-503", "dropt-504", "dropt-505", "dropt-506"} <= set(_st2["ids"]) and "dropt-507" not in _st2["ids"])
r, e = with_item_map({"dropt-502": "Електроніка та гаджети"}, {"dropt-502": _deny["dropt-502"]})
chk("покартковий запис (рішення людини) не обмежується чорним списком назв фолбека", set(r) == {"dropt-502"})
# РЕАЛЬНІ файли: кожен id з "" у покартковому файлі не публікується навіть коли його розділ має фолбек; непорожні — публікуються з правильною ціллю
_ex = [k for k, v in item_real.items() if not v.strip()]
_ok = {k: v for k, v in item_real.items() if v.strip()}
bs.DROPT_ITEM_CATEGORY_FILE = _REAL_ITEM_FILE
bs.DROPT_CATEGORY_MAP_FILE = _REAL_CMAP_FILE
try:
    _sc = set(_ok.values()) | {"Нема"}
    _items_ex = {k: good(k, category="Електроніка / Товари для ПК") for k in _ex}
    r, e = load(_items_ex, site_cats=_sc)
    chk(f"реальні файли: усі {len(_ex)} виключених (\"\") НЕ публікуються, хоч їхній розділ має фолбек", not r and "excluded" in e)
    _items_ok = {k: good(k, category="Невідомий / Розділ") for k in _ok}
    r, e = load(_items_ok, site_cats=_sc)
    chk(f"реальні файли: усі {len(_ok)} розмічених публікуються зі своєю ціллю", set(r) == set(_ok) and all(r[k]["category_name"] == _ok[k] for k in _ok))
finally:
    bs.DROPT_ITEM_CATEGORY_FILE = str(tmp / "empty_item_cat.json")
    bs.DROPT_CATEGORY_MAP_FILE = str(tmp / "cmap_fixture.json")

_names_bad = ["Ножі кухонні набір", "Нож складной", "Мисливський нож", "Ножик туристичний", "Кинджал", "Мачете", "Сокира туристична", "Кастет", "Перцевий балончик", "Тазер", "Taser pro",
              "Прихована міні камера", "Шпигунська камера", "Жучок прослушка", "Диктофон міні", "GPS маячок", "Кальян", "Лaзерна указка", "GSM розетка з прослуховуванням", "Електрошокер",
              "Колонка JBL PartyBox 320", "Навушники Redmi AirDots", "Монокуляр Bushnell", "Рюкзак Swissgear", "Фурмінатор FURminator", "Костюм Marvel Дедпул", "Карта пам'яті Amazon pro"]
_names_ok = ["Ніжна лялька", "Ножки для столу пластикові", "Ніжний плед для дітей", "Фітнес-трекер браслет", "Вакуумні пакети для зберігання", "Магніти на холодильник", "Ножиці дитячі", "Ножний насос для надувних іграшок", "Годівниця для кота"]
_nm = {f"dropt-{600 + i}": good(f"dropt-{600 + i}", name=n, category="Товари для тварин") for i, n in enumerate(_names_bad + _names_ok)}
r, e = with_item_map({}, _nm)
_nb = {f"dropt-{600 + i}" for i in range(len(_names_bad))}
_leak = sorted(_nm[k]["name"] for k in r if k in _nb)
_fp = sorted(_nm[k]["name"] for k in _nm if k not in _nb and k not in r)
chk(f"чорний список назв фолбека: усі {len(_names_bad)} небезпечних/брендових назв (форми «нож/ножі/ніж», латинські двійники, шпигунські пристрої, чужі бренди) відсічені (просочилось: {_leak}); {len(_names_ok)} безпечних («Ніжна», «Ніжки», фітнес-трекер, вакуумні пакети, ножиці…) — публікуються (хибних спрацювань: {_fp})",
    not _leak and not _fp)
r, e = with_item_map({}, {"dropt-011836": good("dropt-011836", category="Товари для тварин"), "dropt-0": good("dropt-0", category="Товари для тварин"), "dropt-11836": good("dropt-11836", category="Товари для тварин")})
chk("id з провідним нулем (dropt-011836) не приймається; без нулів — так", set(r) == {"dropt-11836"})
r, e = with_item_map({}, {"dropt-11836": good("dropt-11836", category="Товари для тварин")})
for _k in ("dropt-79998", "dropt-52540", "dropt-52541"):
    chk(f"{_k} (дитяче автокрісло-бустер / лазерні нічники) свідомо виключено у реальному файлі", item_real.get(_k) == "")

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
         "dropt-108": good("dropt-108", name="Автокрісло з базою", description=ENT_DESC),
         "dropt-109": good("dropt-109", name="Годівниця для кота", category="Товари для тварин")}


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
chk("власна категорія Dropt у повній збірці: картка 109 є, сторінка категорії «Товари для тварин» створена і містить її",
    (out / "product-dropt-109.html").exists() and "dropt-109" in ix and ix["dropt-109"].get("c") == "Товари для тварин"
    and any(f.startswith("category-") and "dropt-109" in T[f].decode("utf-8", "replace") or f.startswith("category-") and "Годівниця для кота" in T[f].decode("utf-8", "replace") for f in T))
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
chk("нових сторінок категорій Dropt нема, крім дозволених власних (тут лише «Товари для тварин»)", sorted(k for k in T if k.startswith("category-") and k not in t0) == [f"category-{bs.slugify('Товари для тварин')}.html"])
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
