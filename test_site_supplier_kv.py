#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_supplier_kv.py — 17.11.2026 (Код → Shopify-code: картки KIDDISVIT `kv-<id>` на сайті за схемою RT; рішення власника: «додаєш їхні товари у всі фіди, подивимось як вони працюють із замовленнями»).
(1) завантажувач `_load_supplier_kv`: ворота SITE_KV_LIVE (за замовчуванням вимкнено), LIQPAY live, свіжість ≤3 доби, мапа категорій (шлях/бренд; невідомий шлях = НЕ публікуємо), id лише `kv-<цифри>` (kvu- не приймаємо), index не True → noindex;
(2) повна збірка: product-k<id>.html (НЕ product-kv-…), фото /img/p/k<id>-N.jpg, код PT-K<id>, жодного «kiddisvit»/закупівлі cost у видачі, sitemap-k.xml лише для index=true, index.json (rt=1, sup=kv, ix), index-rt.json, головна без kv;
(3) app.js/кошик (браузер): kv- = лише передоплата, окрема посилка, href k<id> → внутрішній id kv-<id>."""
import contextlib
import functools
import http.server
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["SITE_PICK_DATE"] = "2026-10-10"
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


site_photo_probe.run = lambda idx: None
DEFAULT_KV_FILE = bs.SUPPLIER_KV_FILE
tmp = Path(tempfile.mkdtemp())
now = datetime.now(timezone.utc)
COST_MARK = "123.45"   # закупівля має бути унікальною, щоб шукати витік у згенерованому сайті
GOOD = {"name": "Фігурка колекційна «Герой» 10 см", "description": "Колекційна фігурка з вінілу, висота 10 см. Оплата при отриманні на відділенні. Підходить для колекціонування й гри.",
        "vendor": "Funko", "vendor_code": "KV-5001", "barcode": "4823099900015", "category": "Герої фільмів та серіалів", "category_path": ["Funko", "Герої фільмів та серіалів"],
        "images": ["https://plutustoys.com.ua/img/kv/kv-501-1.jpg", "https://plutustoys.com.ua/img/kv/kv-501-2.jpg"], "price": 959, "cost": float(COST_MARK), "rrp": 999.0, "stock": 7,
        "dims_cm": [10, 10, 15], "weight_kg": None, "index": True}
CMAP = {"paths": {"Funko > Герої фільмів та серіалів": "Фігурки героїв", "Ляльки > Пупси": "Пупси", "Конструктори": "Конструктори KV"}, "brands": {"squishmallows": "М'які іграшки"}}


def im(k, n=1):
    """Фото НАШОГО сервера для картки k (kv-<id>): лише такі завантажувач і приймає (аудит #1694)."""
    return [f"https://plutustoys.com.ua/img/kv/{k}-{i}.jpg" for i in range(1, n + 1)]


def write_files(items, updated=None, cmap=None):
    (tmp / "s.json").write_text(json.dumps({"updated": (updated or now).isoformat(), "catalog_n": len(items), "items": items}, ensure_ascii=False), encoding="utf-8")
    (tmp / "m.json").write_text(json.dumps(cmap if cmap is not None else CMAP, ensure_ascii=False), encoding="utf-8")
    bs.SUPPLIER_KV_FILE, bs.KV_CATEGORY_MAP_FILE = str(tmp / "s.json"), str(tmp / "m.json")


def load(items, **kw):
    write_files(items, **kw)
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        res = bs._load_supplier_kv()
    return res, err.getvalue()


# ── (0) файл приватний, ворота вимкнені за замовчуванням ──
site_dir = (HERE / "site").resolve()
kv_path = Path(DEFAULT_KV_FILE).resolve()
chk("supplier_kv.json за замовчуванням лежить у корені репо, а НЕ в site/ (DocumentRoot: там закупівля cost)", kv_path == (HERE / "supplier_kv.json").resolve() and site_dir not in kv_path.parents)
chk("ворота SITE_KV_LIVE за замовчуванням ВИМКНЕНО (змінної нема → False)", os.environ.get("SITE_KV_LIVE") is None and bs.SITE_KV_LIVE is False)
bs.LIQPAY_LIVE = True
r, e = load({"kv-501": GOOD})
chk("ворота вимкнені (SITE_KV_LIVE не 1) → порожньо, навіть із чинним файлом", r == {})
bs.SITE_KV_LIVE = True

# ── (1) завантажувач ──
r, e = load({"kv-501": GOOD})
it = r.get("kv-501", {})
chk("валідна картка: id kv-501, категорія з мапи, фото https, ціна сайту з файлу, закупівля, залишок, штрихкод, постачальник kiddisvit",
    it.get("category_name") == "Фігурки героїв" and it.get("pictures") == GOOD["images"] and it.get("site_price") == 959 and it.get("rt_cost") == float(COST_MARK) and it.get("stock") == 7
    and it.get("barcode") == "4823099900015" and it.get("supplier") == "kiddisvit" and it.get("vendor") == "Funko")
chk("price_of бере ціну з файлу (закрита формула Коду; не перераховуємо)", bs.price_of(it) == 959)
chk("опис без речень про накладений платіж/оплату при отриманні", "отриман" not in it["description"] and "накладен" not in it["description"].lower() and "Колекційна фігурка" in it["description"])
chk("index=true і опис ≥50 знаків → rt_noindex False", it.get("rt_noindex") is False)
r, e = load({"kv-502": dict(GOOD, index=False, images=im("kv-502")), "kv-503": {k: v for k, v in dict(GOOD, images=im("kv-503")).items() if k != "index"}, "kv-504": dict(GOOD, description="Коротко.", images=im("kv-504"))})
chk("index=false, поля index нема (fail-safe), тонкий опис <50 знаків → noindex", all(r[k]["rt_noindex"] is True for k in ("kv-502", "kv-503", "kv-504")))
r, e = load({"kv-505": dict(GOOD, vendor="Squishmallows", images=im("kv-505")), "kv-506": dict(GOOD, category_path=["Ляльки", "Пупси"], vendor="BABY born", images=im("kv-506")), "kv-507": dict(GOOD, category_path=["Конструктори"], vendor="JAKI", images=im("kv-507"))})
chk("бренд (Squishmallows) має пріоритет над шляхом; шлях без бренду → категорія з мапи", r["kv-505"]["category_name"] == "М'які іграшки" and r["kv-506"]["category_name"] == "Пупси" and r["kv-507"]["category_name"] == "Конструктори KV")
# SEO 18.11: корінь «Catalog > » у шляху постачальника не заважає мапі; бренд — також з полів brand/brend; невідомий шлях без «Catalog» теж не публікується
r, e = load({"kv-601": dict(GOOD, category_path=["Catalog", "Ляльки", "Пупси"], vendor="", images=im("kv-601")),
             "kv-602": dict(GOOD, category_path=["Catalog", "Конструктори"], vendor="JAKI", images=im("kv-602")),
             "kv-603": dict(GOOD, category_path=["Невідома"], source_category_path=["Catalog", "Ляльки", "Пупси"], vendor="", images=im("kv-603")),
             "kv-604": dict(GOOD, category_path=["Невідома"], vendor="", brand="Squishmallows", images=im("kv-604")),
             "kv-605": dict(GOOD, category_path=["Catalog", "Невідома", "Гілка"], vendor="", images=im("kv-605")),
             "kv-606": dict(GOOD, category_path=[], category="Catalog > Ляльки > Пупси", vendor="", images=im("kv-606"))})
chk("«Catalog > » зрізається при пошуку категорії (path / source_category_path / плоске category); бренд з поля brand; невідоме лишається поза",
    r.get("kv-601", {}).get("category_name") == "Пупси" and r.get("kv-602", {}).get("category_name") == "Конструктори KV" and r.get("kv-603", {}).get("category_name") == "Пупси"
    and r.get("kv-604", {}).get("category_name") == "М'які іграшки" and "kv-605" not in r and r.get("kv-606", {}).get("category_name") == "Пупси" and "category:Catalog > Невідома > Гілка" in e)
r, e = load({"kv-1": dict(GOOD, category_path=["Невідома", "Гілка"], images=im("kv-1")), "kv-2": dict(GOOD, images=[]), "kv-3": dict(GOOD, price=0, images=im("kv-3")), "kvu-4": GOOD, "bad id": GOOD, "kv-5": dict(GOOD, name=" ", images=im("kv-5")),
             "kv-6": dict(GOOD, stock=-1, images=im("kv-6")), "rt-7": GOOD, "kv-8": dict(GOOD, images=["http://x/1.jpg"])})
chk("невідомий шлях/нема фото/ціна 0/kvu-/поганий id/нема назви/мінусовий залишок/rt-id/http-фото — НЕ публікуються, у stderr є пояснення", r == {} and "supplier_kv.json: не опубліковано" in e and "category:Невідома > Гілка" in e)
# аудит #1694: ціна/залишок — лише цілі; фото — лише наші файли цього id; опис — безпечний HTML; ім'я постачальника не просочується
r, e = load({"kv-11": dict(GOOD, price=True, images=im("kv-11")), "kv-12": dict(GOOD, price=959.9, images=im("kv-12")), "kv-13": dict(GOOD, stock=True, images=im("kv-13")), "kv-14": dict(GOOD, price="1 200", images=im("kv-14")),
             "kv-15": dict(GOOD, price=959.0, images=im("kv-15")), "kv-16": dict(GOOD, price="959", stock="7", images=im("kv-16"))})
chk("ціна true / 959.9 / «1 200» і залишок true — НЕ публікуються (не округлюємо закриту ціну); 959.0 і «959» — публікуються як 959", set(r) == {"kv-15", "kv-16"} and r["kv-15"]["site_price"] == 959 and r["kv-16"]["site_price"] == 959 and r["kv-16"]["stock"] == 7)
r, e = load({"kv-21": dict(GOOD, images=["https://cdn.kiddisvit.ua/upload/21.jpg"]), "kv-22": dict(GOOD, images=["https://plutustoys.com.ua/img/kv/kv-999-1.jpg"]), "kv-23": dict(GOOD, images=["https://example.com/img/kv/kv-23-1.jpg"]),
             "kv-24": dict(GOOD, images=["https://cdn.kiddisvit.ua/x.jpg"] + im("kv-24", 2))})
chk("фото чужого хоста / чужого id / чужого домену з нашим шляхом — картка НЕ публікується; змішаний список лишає лише наші файли", set(r) == {"kv-24"} and r["kv-24"]["pictures"] == im("kv-24", 2) and "images" in e)
r, e = load({"kv-31": dict(GOOD, images=im("kv-31"), description="<script>alert(1)</script><img src=x onerror=alert(2)> Розмір <5 см, a<b. <b>Жирний</b><br>рядок <p onclick=x>абзац</p>")})
d31 = r["kv-31"]["description"]
chk("опис: <script>/<img onerror> екрановані; <b>, <br>, <p> без атрибутів лишаються; <p onclick=…> екранований; «<5 см» не ламає розмітку", "<script" not in d31 and "<img" not in d31 and "&lt;script&gt;" in d31 and "<b>Жирний</b>" in d31 and "<br>" in d31 and "<p onclick" not in d31 and "&lt;5 см" in d31)
r, e = load({"kv-41": dict(GOOD, images=im("kv-41"), vendor="KIDDISVIT", vendor_code="kv-77", description="Фігурка від KIDDISVIT, артикул kv-41. Добра."), "kv-42": dict(GOOD, images=im("kv-42"), name="Фігурка KIDDISVIT Герой"),
             "kv-43": dict(GOOD, images=im("kv-43"), vendor="KIDDI SMART")})
chk("vendor/vendor_code/опис з іменем постачальника чи kv- — обнулені/вирізані; назва з ним — картка не публікується; «KIDDI SMART» (окремий бренд) лишається",
    "kv-42" not in r and r["kv-41"]["vendor"] == "" and r["kv-41"]["vendor_code"] == "" and "kiddisvit" not in r["kv-41"]["description"].lower() and "kv-41" not in r["kv-41"]["description"] and r["kv-43"]["vendor"] == "KIDDI SMART")
# обходи фільтра (повторний аудит #1694): сутності в назві, розділювачі, кирилиця/латиниця, id з розділювачем, цифри id у описі, незакриті теги
LEAKY = ["Kiddi&#115;vit фігурка", "Фігурка Кідді Світ", "Фігурка Kiddi-Svit", "Фігурка kiddi_svit", "Фігурка Кiддiсвiт", "Фігурка КІДДІСВІТ", "Фігурка kv_12", "Фігурка Kv-12", "Фігурка kvu-2", "Фігурка Kid\u200bdisvit", "Фігурка Kid\u00addisvit", "Фігурка Kiddi\u2011svit", "Фігурка Kiddi\u00a0svit", "Фігурка ＫＩＤＤＩＳＶＩＴ", "Фігурка Kiddi.svit"]
r, e = load({f"kv-{60 + i}": dict(GOOD, name=n, images=im(f"kv-{60 + i}")) for i, n in enumerate(LEAKY)})
chk(f"назва з іменем постачальника у будь-якому написанні ({len(LEAKY)} варіантів) → картка НЕ публікується: {sorted(r)}", r == {})
r, e = load({"kv-71": dict(GOOD, images=im("kv-71"), vendor="Kiddi-Svit", vendor_code="KV-71", description="Артикул kv-501 від Кідді Світ. Добра фігурка.", name="Фігурка «Герой»"),
             "kv-72": dict(GOOD, images=im("kv-72"), vendor="KIDDI SMART", vendor_code="KS-72", name="Фігурка KIDDI SMART")})
chk("vendor/код у різних написаннях обнулюються; з опису вирізано ім'я й УСІ цифри id (не лишається «01»); «KIDDI SMART» лишається",
    r["kv-71"]["vendor"] == "" and r["kv-71"]["vendor_code"] == "" and "501" not in r["kv-71"]["description"] and "01 " not in r["kv-71"]["description"] and "Світ" not in r["kv-71"]["description"] and "Добра фігурка" in r["kv-71"]["description"]
    and r["kv-72"]["vendor"] == "KIDDI SMART" and "kv-72" in r)
r, e = load({"kv-81": dict(GOOD, images=im("kv-81"), description="<ul><li>один<li>два</ul></b><b>жирний")})
d81 = r["kv-81"]["description"]
chk("опис <ul><li>один<li>два</ul></b><b>жирний → рівно по одній парі <ul>, двох <li>, одній <b>", d81.count("<ul>") == 1 and d81.count("</ul>") == 1 and d81.count("<li>") == 2 and d81.count("</li>") == 2 and d81.count("<b>") == 1 and d81.count("</b>") == 1)
# хибні збіги (повторний аудит #1694): звичайні «KV 250», «кв 3 м», «Квадрат», «Світ», «Кітті» НЕ чіпаємо ні в назві, ні в описі
r, e = load({"kv-91": dict(GOOD, images=im("kv-91"), name="Модель KV 250 машинка", description="Площа 20 кв 2 кімнати, Модель KV 250. Дитячий світ, Кітті, Квадрат 2, Kvasy, Rkv 12."),
             "kv-92": dict(GOOD, images=im("kv-92"), name="Тканина кв 3 м Кітті Дитячий світ")})
chk("«KV 250», «кв 3 м», «Кітті», «Дитячий світ» у назві → картки публікуються; опис із такими фрагментами не спотворюється", set(r) == {"kv-91", "kv-92"} and "Площа 20 кв 2 кімнати, Модель KV 250." in r["kv-91"]["description"] and "Kvasy, Rkv 12" in r["kv-91"]["description"])
# ім'я, розірване дозволеним тегом, і невидимі символи в описі; ліміт довжини
r, e = load({"kv-93": dict(GOOD, images=im("kv-93"), description="Фігурка Kiddi<b>svit</b> та Kid\u200bdisvit; артикул kv-93 відомий. Добра.")})
d93 = r["kv-93"]["description"]
chk("опис: ім'я, розірване <b>…</b> або невидимим символом, і id kv-93 вирізані; решта тексту на місці; баланс тегів", "svit" not in d93.lower() and "disvit" not in d93.lower() and "kv-93" not in d93 and "Добра." in d93 and d93.count("<b>") == d93.count("</b>"))
r, e = load({"kv-94": dict(GOOD, images=im("kv-94"), name="Фігурка Kid\u200b«Герой»", vendor="Fun\u200bko")})
chk("невидимі символи з назви й бренду прибрані назавжди", r["kv-94"]["name"] == "Фігурка Kid«Герой»" and r["kv-94"]["vendor"] == "Funko")
import time
_t0 = time.time()
r, e = load({"kv-95": dict(GOOD, images=im("kv-95"), description="<ul><li>" * 12500)})
_dt = time.time() - _t0
chk(f"патологічний опис 100 тис. символів без крапок обробляється швидко ({_dt:.2f} с < 3 с): ліміт довжини перед очищенням", _dt < 3 and "kv-95" in r and len(r["kv-95"]["description"]) < 100000)
r, e = load({"kv-501": GOOD}, updated=now - timedelta(days=4))
chk("файл старіший за 3 доби → порожньо, голосно", r == {} and "застарів" in e)
bs.LIQPAY_LIVE = False
r, e = load({"kv-501": GOOD})
chk("без ввімкненої онлайн-оплати картки не публікуємо (лише передоплата)", r == {} and "онлайн-оплата не ввімкнена" in e)
bs.LIQPAY_LIVE = True
bs.SUPPLIER_KV_FILE = str(tmp / "nope.json")
err = io.StringIO()
with contextlib.redirect_stderr(err):
    res = bs._load_supplier_kv()
chk("ворота відкриті, але файлу нема → порожньо, голосно", res == {} and "supplier_kv.json нема" in err.getvalue())
(tmp / "bad.json").write_text("{not json", encoding="utf-8")
bs.SUPPLIER_KV_FILE = str(tmp / "bad.json")
err = io.StringIO()
with contextlib.redirect_stderr(err):
    res = bs._load_supplier_kv()
chk("поламаний JSON → порожньо, голосно, збірка не падає", res == {} and "НЕ прочитано" in err.getvalue())

# ── публічні id ──
chk("public_slug: kv-501 → k501 ЗАВЖДИ (і без воріт SITE_PUBLIC_IDS_LIVE); Toysi і rt- без змін до воріт", bs.public_slug("kv-501") == "k501" and bs.public_slug("176003") == "176003" and bs.public_slug("rt-69616") == ("y69616" if bs.PUBLIC_IDS_LIVE else "rt-69616"))
chk("product_file: product-k501.html; код для покупця PT-K501 (без «kv»)", bs.product_file("kv-501") == "product-k501.html" and bs.site_product_code("kv-501") == "PT-K501")
chk("public_photo: /img/kv/kv-501-2.jpg → /img/p/k501-2.jpg; чужий id у шляху і чужий хост не переписуємо",
    bs.public_photo("kv-501", "https://plutustoys.com.ua/img/kv/kv-501-2.jpg") == "https://plutustoys.com.ua/img/p/k501-2.jpg"
    and bs.public_photo("kv-501", "https://plutustoys.com.ua/img/kv/kv-999-2.jpg") == "https://plutustoys.com.ua/img/kv/kv-999-2.jpg"
    and bs.public_photo("kv-501", "https://example.com/img/kv/kv-501-2.jpg") == "https://example.com/img/kv/kv-501-2.jpg")
chk("текстові ключі: kv-<цифри> приймаються (Копірайтер), kv-abc і kvu-1 — ні; Toysi і rt- як були", bs._product_text_key_ok("kv-501") and not bs._product_text_key_ok("kv-abc") and not bs._product_text_key_ok("kvu-1") and bs._product_text_key_ok("176003") and bs._product_text_key_ok("rt-69616"))

# ── (2) повна збірка ──
items = {"kv-501": GOOD,
         "kv-502": dict(GOOD, name="Пупс «Малюк» 30 см", vendor="BABY born", category_path=["Ляльки", "Пупси"], category="Пупси", vendor_code="KV-5002", barcode="4823099900022", price=1200, images=["https://plutustoys.com.ua/img/kv/kv-502-1.jpg"], index=False),
         "kv-503": dict(GOOD, name="Пупс «Сплячий» 28 см", vendor="BABY born", category_path=["Ляльки", "Пупси"], category="Пупси", vendor_code="KV-5003", barcode="4823099900039", price=1100, images=["https://plutustoys.com.ua/img/kv/kv-503-1.jpg"], stock=0)}
write_files(items)
# збірка при ВИМКНЕНИХ воротах (SITE_KV_LIVE не 1) з чинним файлом: жодної kv-картки, жодного sitemap-k.xml (аудит #1694: порівняння з вимкненими воротами)
bs.SITE_KV_LIVE = False


def toysi(pid, name, cat):
    return {"id": str(pid), "name": name, "vendor": "T", "price": 200 + pid % 200, "stock": 5, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/p/{pid}"}


catalog = {str(i): toysi(i, f"Іграшка {i}", "Фігурки героїв" if i % 2 else "Машинки") for i in range(1, 41)}
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.CATEGORY_GROUPS_FILE = str(tmp / "none.json")
out_off = Path(tempfile.mkdtemp())
bs.OUT = str(out_off)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()
chk("ворота вимкнені, файл чинний: немає product-k*.html, sitemap-k.xml і жодного kv- в index*.json/sitemap", not list(out_off.glob("product-k*.html")) and not (out_off / "sitemap-k.xml").exists()
    and all("kv-" not in (out_off / n).read_text(encoding="utf-8") for n in ("index.json", "index-t.json", "index-rt.json", "sitemap.xml")))
bs.SITE_KV_LIVE = True
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()
idx = json.loads((out / "index.json").read_text(encoding="utf-8"))
ix = {x["id"]: x for x in idx}
chk("index.json: kv-501 з ціною 959, u=k501, rt=1, ix=1, sup=kv; kv-502 ix=0; kv-503 (залишок 0) НЕ в індексі",
    ix.get("kv-501", {}).get("pr") == 959 and ix["kv-501"].get("u") == "k501" and ix["kv-501"].get("rt") == 1 and ix["kv-501"].get("ix") == 1 and ix["kv-501"].get("sup") == "kv"
    and ix.get("kv-502", {}).get("ix") == 0 and "kv-503" not in ix)
chk("у звичайного товару Toysi і RT нема поля sup", "sup" not in ix["5"])
ALLOWED_KEYS = {"id", "u", "n", "pr", "p", "c", "s", "op", "rt", "ix", "sup", "dup"}
chk("index.json: набір ключів ⊆ дозволеного (нема contribution/cost/внеску, з якого видно закупівлю)", all(set(x) <= ALLOWED_KEYS for x in idx) and set(ix["kv-501"]) <= ALLOWED_KEYS)
chk("фото в index.json: /img/p/k501-1.jpg (не /img/kv/ і не адреса постачальника)", ix["kv-501"]["p"] == "https://plutustoys.com.ua/img/p/k501-1.jpg")
pg = out / "product-k501.html"
h = pg.read_text(encoding="utf-8") if pg.exists() else ""
chk("сторінка product-k501.html є, ціна 959, фото /img/p/k501-1.jpg, код PT-K501", pg.exists() and "959" in h and "img/p/k501-1.jpg" in h and "PT-K501" in h)
chk("сторінки з внутрішнім id (product-kv-501.html) НЕ створено", not (out / "product-kv-501.html").exists())
main = h.split("<footer")[0]
chk("картка kv: «Оплата карткою онлайн», НЕМАЄ «Оплата при отриманні» і «при накладеному платежі»", "Оплата карткою онлайн" in main and "Оплата при отриманні" not in main and "при накладеному платежі" not in main)
chk("штрихкод у JSON-LD (gtin) і бренд Funko на місці", '"gtin' in h and "4823099900015" in h and "Funko" in h)
chk("у видачі немає імені постачальника (KIDDISVIT), рядка kv- чи закупівлі", all("kiddisvit" not in q.read_text(encoding="utf-8", errors="ignore").lower() and COST_MARK not in q.read_text(encoding="utf-8", errors="ignore") for q in out.glob("*.html")) and COST_MARK not in (out / "index.json").read_text(encoding="utf-8"))
non_prod = [q for q in out.glob("*.html") if not q.name.startswith(("product-", "category-", "catalog"))]
chk(f"kv-картка не на головній/в акціях/подарунках/add-on кошика (перевірено {len(non_prod)} сторінок поза product/category/catalog; головна наповнюється з index-t.json, куди kv не входить — перевірено окремо)", len(non_prod) > 5 and not any(("product-k501" in q.read_text(encoding="utf-8") or "kv-501" in q.read_text(encoding="utf-8")) for q in non_prod))
h502 = (out / "product-k502.html").read_text(encoding="utf-8")
chk("kv-502 (index=false): <meta robots noindex,follow>; kv-501 — без noindex", 'content="noindex,follow"' in h502 and "noindex" not in h.split("</head>")[0])
h503 = out / "product-k503.html"
chk("kv-503 (залишок 0): сторінка жива (OutOfStock), без оплати при отриманні", h503.exists() and "OutOfStock" in h503.read_text(encoding="utf-8") and "при отриманні" not in h503.read_text(encoding="utf-8").split("<footer")[0])
smk = (out / "sitemap-k.xml").read_text(encoding="utf-8") if (out / "sitemap-k.xml").exists() else ""
small = "".join(q.read_text(encoding="utf-8") for q in out.glob("sitemap-*.xml"))
chk("sitemap-k.xml: лише індексована kv-501 (рівно раз); kv-502 (noindex) і kv-503 (OOS) — ні; у rt/products/priority kv нема", "/product-k501.html" in smk and "/product-k502.html" not in small and small.count("/product-k501.html") == 1 and "k501" not in (out / "sitemap-products.xml").read_text(encoding="utf-8"))
sm_index = (out / "sitemap.xml").read_text(encoding="utf-8")
chk("sitemap.xml (індекс) містить sitemap-k.xml", "sitemap-k.xml" in sm_index)
home = (out / "index.html").read_text(encoding="utf-8")
chk("kv-картки НЕ потрапляють на головну і в кошикові add-on", "product-k501" not in home and "product-k501" not in (out / "cart.html").read_text(encoding="utf-8"))
irt = json.loads((out / "index-rt.json").read_text(encoding="utf-8"))
it_ = json.loads((out / "index-t.json").read_text(encoding="utf-8"))
chk("index-rt.json містить kv-501 (пошук/категорії довантажать у фоні); index-t.json — без kv", any(x["id"] == "kv-501" for x in irt) and not any(x["id"].startswith("kv-") for x in it_))
cat_files = [p for p in out.glob("category-*.html") if "product-k501" in p.read_text(encoding="utf-8")]
chk("картка потрапила в сторінку категорії «Фігурки героїв»", len(cat_files) >= 1)
# закупівля не лишилась у файлах поряд із сайтом
chk("жоден файл out/ не містить закупівлі (123.45) — ні HTML, ні JSON, ні XML", not any(COST_MARK in q.read_text(encoding="utf-8", errors="ignore") for q in out.rglob("*") if q.is_file() and q.suffix in (".html", ".json", ".xml", ".txt")))

# ── (3) app.js: статичні перевірки ──
js = (HERE / "site" / "assets" / "app.js").read_text(encoding="utf-8")
chk("app.js: slugId k<цифри> → kv-<цифри> (href → внутрішній id)", re.search(r'\^k\(\[0-9\]\+\)\$/\.exec\(sl\)\)\) return "kv-"\+m\[1\]', js) is not None)
chk("app.js: kv- = лише передоплата (applyRtPayment і явний payment=prepaid при замовленні)", js.count("/^(rt|kv)-/") >= 2)
chk("app.js: окрема посилка для kv- (parcelGroups: t, r, k, z)", "g={t:[], r:[], k:[], z:[]}" in js and "/^kv-/.test(id)" in js and "[g.t, g.r, g.k, g.z]" in js)

# ── (3б) браузер: кошик ──
shutil.copytree(HERE / "site" / "assets", out / "assets", dirs_exist_ok=True)
H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
from playwright.sync_api import sync_playwright  # noqa: E402

STATE = "(()=>{const c=document.querySelector('input[name=payment][value=cod]'), p=document.querySelector('input[name=payment][value=prepaid]'), n=document.getElementById('rt-pay-note'); return {cod_disabled: !!(c&&c.disabled), prepaid_checked: !!(p&&p.checked), note_hidden: !!(n&&n.hidden), note: n?n.textContent:''}})()"
KVI = {"id": "kv-501", "name": "Фігурка", "price": 959, "photo": "", "qty": 1}
PLI = {"id": "5", "name": "T", "price": 300, "photo": "", "qty": 1}
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for label, cart, want in (("з kv-позицією", {"kv-501": KVI}, "kv"), ("без kv (звичайний кошик)", {"5": PLI}, "plain"), ("змішаний kv+Toysi", {"kv-501": KVI, "5": PLI}, "mixed")):
        page = br.new_page(viewport={"width": 390, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.route("**/googletagmanager.com/**", lambda r: r.abort())
        page.add_init_script(f"localStorage.setItem('pt_cart_v1', JSON.stringify({json.dumps(cart)}))")
        page.goto(f"{base}/cart.html", wait_until="load")
        page.wait_for_timeout(400)
        s = page.evaluate(STATE)
        if want == "plain":
            chk(f"кошик {label}: накладений платіж активний, пояснення нема", s["cod_disabled"] is False and s["note_hidden"] is True)
        else:
            chk(f"кошик {label}: накладений платіж неактивний, обрана картка онлайн, пояснення видиме", s["cod_disabled"] is True and s["prepaid_checked"] is True and s["note_hidden"] is False)
            chk(f"кошик {label}: згадка про окремі посилки лише у змішаному", ("окремими посилками" in s["note"]) == (want == "mixed"))
        dl = page.evaluate("(()=>{const e=document.getElementById('sum-delivery'); return e?e.textContent:''})()")
        chk(f"кошик {label}: доставка «{dl}» — за кожну посилку лише у змішаному", ("за кожну посилку" in dl) == (want == "mixed"))
        chk(f"кошик {label}: без помилок JS", errs == [])
        page.close()
    # kv + rt — дві різні посилки (окремі склади)
    RTI = {"id": "rt-101", "name": "Конструктор", "price": 480, "photo": "", "qty": 1}
    page = br.new_page(viewport={"width": 390, "height": 900})
    page.route("**/googletagmanager.com/**", lambda r: r.abort())
    page.add_init_script(f"localStorage.setItem('pt_cart_v1', JSON.stringify({json.dumps({'kv-501': KVI, 'rt-101': RTI})}))")
    page.goto(f"{base}/cart.html", wait_until="load")
    page.wait_for_timeout(400)
    dl = page.evaluate("(()=>{const e=document.getElementById('sum-delivery'); return e?e.textContent:''})()")
    chk(f"кошик kv+rt: два склади = дві посилки («{dl}»)", "за кожну посилку" in dl)
    page.close()
    br.close()
srv.shutdown()

print("\n" + ("ALL OK" if not F else f"FAIL: {len(F)}"))
sys.exit(1 if F else 0)
