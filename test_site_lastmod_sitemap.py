#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_lastmod_sitemap.py — 15.10.2026 (SEO: форсувати індексацію). Перевіряє: (1) sitemap.xml — ІНДЕКС п'яти сегментів (priority/products/oos/categories/static), кожен URL рівно в одному, без сторінок пагінації; (2) склад priority
(картки з текстами / demand / топ за показами GSC з site/sitemap_priority.json / категорії з текстами й цільові категорії груп Prom); (3) lastmod = дата РЕАЛЬНОЇ зміни сторінки (стан .htlastmod_state.json): повторна збірка без змін не рухає ні lastmod, ні mtime файла
(Last-Modified/304 живуть між збірками); змінилась одна картка — рухається лише вона; «плаваючі» поля (priceValidUntil, ?v=hash асетів) файл оновлюють, але lastmod не рухають; (4) стан не віддається Apache (.ht*) і прибирає зниклі сторінки."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["SITE_PICK_DATE"] = "2026-10-20"
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


def item(pid, name, cat, price, stock=5):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": stock, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/{pid}"}


catalog = {str(i): item(i, f"Товар номер {i}", f"Кат{i % 5}", 250 + i, stock=(5 if i % 7 else 0)) for i in range(1, 160)}
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
PRI_IDS = {"10", "11", "14"}   # 14 = 14 % 7 == 0 → без залишку, але в priority
bs._sitemap_priority = lambda: (set(PRI_IDS), {"category-kat1.html"})


def build():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        bs.build()


def locs(fname):
    return re.findall(r"<loc>([^<]+)</loc>", (out / fname).read_text(encoding="utf-8"))


def lm_map(fname):
    return dict(re.findall(r"<loc>([^<]+)</loc><lastmod>([^<]+)</lastmod>", (out / fname).read_text(encoding="utf-8")))


BASE_SEGS = [n for n in bs.SITEMAP_SEGMENTS if n not in ("rt", "k", "oos")]   # oos: з 23.11 картки без залишку поза sitemap (SEO) — файл сегмента не пишеться   # rt (RoyalToys), k (KIDDISVIT) — порожні без supplier_rt.json / supplier_kv.json (і воріт SITE_KV_LIVE): файл не пишеться, у індексі нема
build()
idx = (out / "sitemap.xml").read_text(encoding="utf-8")
chk("sitemap.xml — sitemapindex із 4 дочірніми (priority, products, categories, static), кожен файл існує і має lastmod; sitemap-oos.xml нема ні в індексі, ні на диску",
    "<sitemapindex" in idx and all(f"{bs.SITE_URL}/sitemap-{n}.xml</loc><lastmod>" in idx for n in BASE_SEGS) and all((out / f"sitemap-{n}.xml").exists() for n in BASE_SEGS)
    and "sitemap-oos.xml" not in idx and not (out / "sitemap-oos.xml").exists())
seg = {n: locs(f"sitemap-{n}.xml") for n in BASE_SEGS}
allu = [u for n in BASE_SEGS for u in seg[n]]
chk(f"кожен URL рівно в одному сегменті (разом {len(allu)}, унікальних {len(set(allu))}), без сторінок пагінації _N", len(allu) == len(set(allu)) and not any(re.search(r"_\d+\.html$", u) for u in allu))
P = bs.SITE_URL + "/"
pid = lambda i: f"{P}product-{i}.html"  # noqa: E731
chk("priority: головна (корінь «/»), картки з PRI_IDS В НАЯВНОСТІ (10, 11; 14 без залишку — поза sitemap), категорія з priority-набору", f"{P}" in seg["priority"] and pid("10") in seg["priority"] and pid("11") in seg["priority"] and pid("14") not in seg["priority"] and f"{P}category-kat1.html" in seg["priority"])
chk("products: картки в наявності, не з priority; картки без залишку (7, 14) у sitemap НЕМАЄ (ані в oos, ані в priority, навіть якщо id у PRI_IDS), але сторінки живі",
    pid("12") in seg["products"] and pid("10") not in seg["products"] and pid("7") not in allu and pid("14") not in allu
    and (out / "product-7.html").exists() and (out / "product-14.html").exists())
chk("categories: catalog.html і інші категорії (не з priority); static: categories.html і сторінки довіри", f"{P}catalog.html" in seg["categories"] and f"{P}category-kat1.html" not in seg["categories"] and f"{P}categories.html" in seg["static"])
chk("усі згенеровані картки В НАЯВНОСТІ присутні в sitemap рівно раз (без залишку — поза ним)", sum(1 for u in allu if "/product-" in u) == len(list(out.glob("product-*.html"))) - sum(1 for i in range(1, 160) if i % 7 == 0))

# ── lastmod і mtime ──
state_path = out / bs.LASTMOD_STATE_FILE
chk("стан lastmod — dot-файл .ht* (Apache не віддає назовні), записаний після збірки", bs.LASTMOD_STATE_FILE.startswith(".ht") and state_path.exists())
st = json.loads(state_path.read_text(encoding="utf-8"))
OLD = time.mktime((2026, 1, 15, 12, 0, 0, 0, 0, 0))
for k in st:
    st[k]["t"] = OLD
state_path.write_text(json.dumps(st), encoding="utf-8")
for f in out.glob("*.html"):
    os.utime(f, (OLD, OLD))
build()
lm = lm_map("sitemap-products.xml")
chk("повторна збірка БЕЗ змін: lastmod усіх сторінок лишився старим (2026-01-15), а не «сьогодні»", lm and all(v == "2026-01-15" for v in lm.values()))
chk("mtime файлів лишився старим (Last-Modified = дата реальної зміни → 304 між збірками)", all(abs(f.stat().st_mtime - OLD) < 2 for f in out.glob("product-*.html")))
# змінилась ціна однієї картки → тільки вона
catalog["12"]["price"] = 999.0
build()
lm = lm_map("sitemap-products.xml")
today = time.strftime("%Y-%m-%d", time.gmtime())
moved = {u for u, v in lm.items() if v == today}
chk(f"змінилась ціна картки 12: її lastmod = сьогодні ({lm.get(pid('12'))}), а чужі картки, де її показано в «Схожих»/«Додайте до замовлення», НЕ рухаються (рухались: {sorted(moved)[:4]}), решта лишились 2026-01-15",
    moved == {pid("12")})
unmoved = [f for f in out.glob("product-*.html") if abs(f.stat().st_mtime - OLD) < 2]
chk(f"mtime зміненої картки свіжий; незмінених карток зі старим mtime: {len(unmoved)}", time.time() - (out / "product-12.html").stat().st_mtime < 600 and len(unmoved) >= len(lm) - 1)

# ── 23.11: шапка/підвал («хром») не рухають lastmod; перехід на нову маску не рухає дат ──
bs._lastmod_reset()
bs.OUT = str(out)
_cfile = "product-chrome-test.html"
_body = '<main>власний вміст картки</main>'
_h = lambda foot: '<header class="top"><a href="x.html">Шапка</a></header>' + _body + '<footer class="foot"><nav class="fnav">' + foot + '</nav></footer>'
bs._write(_cfile, _h('<a href="about.html">Про нас</a>'))
_t0 = bs._lm_state()[_cfile]["t"]
_old = time.time() - 86400 * 40
bs._lm_state()[_cfile]["t"] = _old
bs._write(_cfile, _h('<a href="about.html">Про нас</a> · <a href="brendy.html">Бренди</a>'))
chk("додали посилання «Бренди» у підвал: lastmod сторінки НЕ рухається (хром поза хешем)", abs(bs._lm_state()[_cfile]["t"] - _old) < 1)
bs._write(_cfile, _h('<a href="about.html">Про нас</a>').replace("власний вміст картки", "ІНШИЙ вміст картки"))
chk("змінився власний вміст картки: lastmod рухається", bs._lm_state()[_cfile]["t"] > _old + 86400)
# міграція: стан записано СТАРОЮ маскою (хеш разом із хромом) → тепер збігається за old-хешем і дата лишається
_html = _h('<a href="about.html">Про нас</a> · <a href="brendy.html">Бренди</a>')
import hashlib
_old_h = hashlib.sha1(bs._LASTMOD_FLOAT_RX.sub("", _html).encode("utf-8")).hexdigest()
bs._lm_state()["product-migr.html"] = {"h": _old_h, "t": _old}
bs._write("product-migr.html", _html)
chk("міграція маски: стан зі СТАРИМ хешем (з хромом) → дата сторінки не рухається, хеш оновлюється на новий", abs(bs._lm_state()["product-migr.html"]["t"] - _old) < 1 and bs._lm_state()["product-migr.html"]["h"] != _old_h)

# ── «плаваючі» поля ──
bs._lastmod_reset()
bs.OUT = str(out)
html1 = '<html>"priceValidUntil": "2026-10-27" <script src="assets/app.js?v=aaaaaaaa"></script> текст</html>'
html2 = '<html>"priceValidUntil": "2026-11-03" <script src="assets/app.js?v=bbbbbbbb"></script> текст</html>'
html3 = '<html>"priceValidUntil": "2026-11-03" <script src="assets/app.js?v=bbbbbbbb"></script> ІНШИЙ текст</html>'
bs._write("fl.html", html1)
st0 = dict(bs._lm_state()["fl.html"])
os.utime(out / "fl.html", (OLD, OLD))
bs._lm_state()["fl.html"]["t"] = OLD
bs._write("fl.html", html2)
chk("лише priceValidUntil і ?v=hash змінились: файл ОНОВЛЕНО (на диску актуальний HTML), але t/mtime — старі", (out / "fl.html").read_text(encoding="utf-8") == html2 and bs._lm_state()["fl.html"]["t"] == OLD and abs((out / "fl.html").stat().st_mtime - OLD) < 2)
bs._write("fl.html", html3)
chk("змінився справжній текст: t рухається на «зараз»", bs._lm_state()["fl.html"]["t"] > OLD + 1000 and time.time() - (out / "fl.html").stat().st_mtime < 600)

# ── зниклі сторінки прибираються зі стану ──
del catalog["13"]
build()
chk("картка, якої більше нема, зникла зі стану lastmod", "product-13.html" not in json.loads(state_path.read_text(encoding="utf-8")) and not (out / "product-13.html").exists())
chk("robots.txt вказує на sitemap.xml (індекс — адреса для GSC не змінюється)", f"Sitemap: {bs.SITE_URL}/sitemap.xml" in (out / "robots.txt").read_text(encoding="utf-8"))

# ── реальний список priority (без підміни) ──
spj = json.loads((HERE / "site" / "sitemap_priority.json").read_text(encoding="utf-8"))
chk(f"site/sitemap_priority.json: {len(spj['product_ids'])} id (топ GSC), усі лише цифри, без дублів", len(spj["product_ids"]) >= 800 and all(re.fullmatch(r"\d+", str(x)) for x in spj["product_ids"]) and len(set(spj["product_ids"])) == len(spj["product_ids"]))
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
