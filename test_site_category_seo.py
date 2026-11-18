#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_category_seo.py — 18.11.2026 (SEO 18.11, рішення власника «запускай обидві»).
(1) перекриття SEO категорії за slug (site/category_seo.json): title / H1 / вступ / FAQ + FAQPage лише на 1-й сторінці категорії; нема запису → категорія як раніше;
    кривий запис (<3 питань, заборонене слово, довгий title, не-рядки) — пропускається ГОЛОСНО, категорія лишається як була;
(2) сторінка-добір за назвою /zoshyty-u-kosu-liniiu.html (site/selection_pages.json): добір «зошит» + «в косу лінію / з допоміжною», ≥12 карток у наявності,
    без тексту або з <12 — не будується (голосно); посилання з категорії-батька і в sitemap; ItemList + FAQPage + BreadcrumbList; повторна збірка без тексту прибирає сторінку."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
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
tmp = Path(tempfile.mkdtemp())
bs.LIQPAY_LIVE = True
bs.SUPPLIER_KV_FILE = str(tmp / "no_kv.json")
bs.SUPPLIER_RT_FILE = str(tmp / "no_rt.json")
bs.CATEGORY_GROUPS_FILE = str(tmp / "none.json")


def toysi(pid, name, cat, stock=5, vendor="T"):
    return {"id": str(pid), "name": name, "vendor": vendor, "price": 40 + pid % 90, "stock": stock, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/p/{pid}"}


def make_catalog(n_kosa):
    cat = {}
    for i in range(1, 31):
        cat[str(i)] = toysi(i, f"Машинка {i}", "Машинки")
    for i in range(101, 101 + n_kosa):   # зошити в косу лінію: частина в категорії «Зошити», частина в «Зошит клітинка»
        cat[str(i)] = toysi(i, f"Зошит Плутус {i}, 12 аркушів, в косу лінію", "Зошити" if i % 2 else "Зошит клітинка")
    cat["150"] = toysi(150, "Зошит Плутус 150, 12 аркушів, з допоміжною лінією", "Зошити")
    cat["151"] = toysi(151, "Зошит у клітинку 151, 12 аркушів", "Зошити")        # не коса лінія
    cat["152"] = toysi(152, "Зошит Плутус 152, в косу лінію", "Зошити", stock=0)    # немає в наявності
    cat["153"] = toysi(153, "Папка кос лінія 153", "Папки")                         # не зошит
    for i in range(201, 205):
        cat[str(i)] = toysi(i, f"Тарілка дитяча на присосці {i}", "Дитячий посуд")
    return cat


catalog = make_catalog(14)
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None

GOOD_SEO = {"dytiachyy-posud": {
    "title": "Дитячий посуд і тарілки на присосці — купити | PlutusToys",
    "h1": "Дитячий посуд: тарілки, ложки, поїльники",
    "intro": "Дитячий посуд і тарілки на присосці. Тут зібрано товари, назви яких містять «на присосці».\n\nДетская тарелка и детская посуда — в одній категорії.",
    "faq": [{"q": "Що таке тарілка на присосці?", "a": "Тарілка, у назві якої вказано присоску."},
            {"q": "Чи є секційні тарілки?", "a": "Так, у назвах деяких товарів є слово «секційна»."},
            {"q": "Де шукати ложки?", "a": "У цій самій категорії."}]}}
GOOD_SEL = {"zoshyty-u-kosu-liniiu": {
    "intro": "Зошити в косу лінію та з допоміжною лінією. Тут лише зошити, у назві яких указано косу лінію.",
    "faq": [{"q": "Що таке зошит у косу лінію?", "a": "Зошит із косою лінійкою."},
            {"q": "Скільки аркушів?", "a": "У назвах товарів — 12 аркушів."},
            {"q": "Чи є з допоміжною лінією?", "a": "Так, є зошити з допоміжною лінією."}]}}


def build(seo=None, sel=None, cat=None):
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    bs.CATEGORY_SEO_FILE = str(tmp / "category_seo.json")
    bs.SELECTION_TEXTS_FILE = str(tmp / "selection_pages.json")
    for name, data in (("category_seo.json", seo), ("selection_pages.json", sel)):
        q = tmp / name
        if data is None:
            q.exists() and q.unlink()
        else:
            q.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if cat is not None:
        bs.tp.fetch_toysi_catalog = lambda: dict(cat)
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        bs.build()
    return out, err.getvalue()


def pg(out, f):
    return (out / f).read_text(encoding="utf-8")


def head_title(h):
    m = re.search(r"<title>(.*?)</title>", h, re.S)
    return m.group(1) if m else ""


# ── (1) перекриття категорії ──
out0, err0 = build()
posud0 = [q.name for q in out0.glob("category-*.html") if "Дитячий посуд" in q.read_text(encoding="utf-8")[:6000]]
chk("база (без перекриттів): категорія «Дитячий посуд» існує, title шаблонний, FAQPage нема", bool(posud0) and posud0[0] == "category-dytiachyy-posud.html"
    and head_title(pg(out0, "category-dytiachyy-posud.html")) == "Дитячий посуд — купити в Україні | PlutusToys" and "FAQPage" not in pg(out0, "category-dytiachyy-posud.html"))
base_h1 = pg(out0, "category-dytiachyy-posud.html")
out1, err1 = build(seo=GOOD_SEO)
h = pg(out1, "category-dytiachyy-posud.html")
chk("перекриття: title з файлу, H1 з файлу, вступ (2 абзаци) над сіткою, 3 питання + FAQPage", head_title(h) == GOOD_SEO["dytiachyy-posud"]["title"] and '<h1 class="page cat-h1">Дитячий посуд: тарілки, ложки, поїльники</h1>' in h
    and "Тут зібрано товари" in h and "Детская тарелка" in h and h.count("<h3>") == 3 and '"@type": "FAQPage"' in h and h.index("Тут зібрано товари") < h.index('id="cat-static"') < h.index("<h3>Що таке тарілка на присосці?"))
chk("інші категорії не змінились: «Машинки» має шаблонний title і без FAQPage", head_title(pg(out1, "category-mashynky.html")) == head_title(pg(out0, "category-mashynky.html")) and "FAQPage" not in pg(out1, "category-mashynky.html"))
chk("canonical і товари категорії лишились: canonical на себе, 4 картки", 'rel="canonical" href="https://plutustoys.com.ua/category-dytiachyy-posud.html"' in h and h.count("Тарілка дитяча на присосці") >= 4)

bad_seo = {"dytiachyy-posud": dict(GOOD_SEO["dytiachyy-posud"], faq=GOOD_SEO["dytiachyy-posud"]["faq"][:2]),
           "ihrashky-dlia-vannoi": dict(GOOD_SEO["dytiachyy-posud"], intro="Це оригінальна іграшка."),
           "samokaty-trykolisni": dict(GOOD_SEO["dytiachyy-posud"], title="Д" * 80),
           "zoshyty": {"title": 5, "h1": "x", "intro": "y", "faq": []}}
out2, err2 = build(seo=bad_seo)
chk("кривий запис (2 питання / «оригінал» / title >65 / не рядки) пропускається ГОЛОСНО, категорія лишається як була",
    err2.count("category_seo.json[") == 4 and head_title(pg(out2, "category-dytiachyy-posud.html")) == "Дитячий посуд — купити в Україні | PlutusToys" and "FAQPage" not in pg(out2, "category-dytiachyy-posud.html"))
chk("запис, що вказує на неіснуючу категорію, нічого не ламає", build(seo={"nema-takoi": GOOD_SEO["dytiachyy-posud"]})[1] == "" or "Traceback" not in build(seo={"nema-takoi": GOOD_SEO["dytiachyy-posud"]})[1])

# пагінація: перекриття лише на 1-й сторінці
big = make_catalog(14)
for i in range(300, 300 + 70):
    big[str(i)] = toysi(i, f"Тарілка дитяча секційна {i}", "Дитячий посуд")
out3, _e3 = build(seo=GOOD_SEO, cat=big)
p2 = out3 / "category-dytiachyy-posud_2.html"
chk("пагінація: сторінка 2 категорії БЕЗ перекриття (title шаблонний із «сторінка 2», без FAQPage і H1 з файлу)", p2.exists() and "FAQPage" not in p2.read_text(encoding="utf-8")
    and "сторінка 2" in head_title(p2.read_text(encoding="utf-8")) and head_title(pg(out3, "category-dytiachyy-posud.html")) == GOOD_SEO["dytiachyy-posud"]["title"])
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)

# ── (2) сторінка-добір ──
out4, err4 = build(sel=None)
chk("нема selection_pages.json → сторінки нема, без попереджень", not (out4 / "zoshyty-u-kosu-liniiu.html").exists() and "selection" not in err4)
out5, err5 = build(sel=GOOD_SEL)
f = out5 / "zoshyty-u-kosu-liniiu.html"
h5 = f.read_text(encoding="utf-8") if f.exists() else ""
n_items = h5.count('class="card')
chk(f"є текст і ≥12 карток → сторінка збудована; добір лише зошити з косою/допоміжною лінією в наявності (карток {n_items}: 14 + 1 допоміжна = 15, без клітинки/нема в наявності/не зошита)",
    f.exists() and n_items == 15 and "Зошит у клітинку 151" not in h5 and "Зошит Плутус 152" not in h5 and "Папка кос лінія" not in h5 and "з допоміжною лінією" in h5)
chk("title і H1 за ТЗ; ItemList + FAQPage + BreadcrumbList; canonical на себе; не noindex", head_title(h5) == "Зошити в косу лінію та з допоміжною лінією — купити | PlutusToys" and '<h1 class="page">Зошити в косу лінію</h1>' in h5
    and '"@type": "ItemList"' in h5 and '"@type": "FAQPage"' in h5 and '"@type": "BreadcrumbList"' in h5 and 'rel="canonical" href="https://plutustoys.com.ua/zoshyty-u-kosu-liniiu.html"' in h5 and "noindex" not in h5)
chk("крихти: Головна › Зошити (category-zoshyty.html) › H1", 'href="category-zoshyty.html"' in h5)
cz = pg(out5, "category-zoshyty.html")
chk("категорія-батько category-zoshyty.html лінкує на сторінку-добір", 'href="zoshyty-u-kosu-liniiu.html"' in cz and "Зошити в косу лінію" in cz)
sm = "".join(q.read_text(encoding="utf-8") for q in out5.glob("sitemap-*.xml"))
chk("sitemap: /zoshyty-u-kosu-liniiu.html рівно раз", sm.count("/zoshyty-u-kosu-liniiu.html") == 1)
out6, err6 = build(sel=None, cat=make_catalog(14))
chk("без selection_pages.json сторінки нема і в батька нема посилання", not (out6 / "zoshyty-u-kosu-liniiu.html").exists() and "zoshyty-u-kosu-liniiu.html" not in pg(out6, "category-zoshyty.html"))
out7, err7 = build(sel=GOOD_SEL, cat=make_catalog(8))
chk("лише 8+1 карток (<12) → сторінку НЕ будуємо, голосне попередження, у батька нема посилання", not (out7 / "zoshyty-u-kosu-liniiu.html").exists() and "zoshyty-u-kosu-liniiu — лише 9 карток" in err7
    and "zoshyty-u-kosu-liniiu.html" not in pg(out7, "category-zoshyty.html"))
out8, err8 = build(sel={"zoshyty-u-kosu-liniiu": dict(GOOD_SEL["zoshyty-u-kosu-liniiu"], faq=GOOD_SEL["zoshyty-u-kosu-liniiu"]["faq"][:2])}, cat=make_catalog(14))
chk("<3 питань → запис пропущено голосно, сторінки нема", not (out8 / "zoshyty-u-kosu-liniiu.html").exists() and "selection_pages.json[zoshyty-u-kosu-liniiu]" in err8)
out9, err9 = build(sel={"zoshyty-u-kosu-liniiu": dict(GOOD_SEL["zoshyty-u-kosu-liniiu"], intro="Офіційні зошити.")}, cat=make_catalog(14))
chk("заборонене слово («офіційні») → запис пропущено голосно", not (out9 / "zoshyty-u-kosu-liniiu.html").exists() and "заборонене слово" in err9)

# повторна збірка у ту саму теку прибирає застарілу сторінку
same = Path(tempfile.mkdtemp())
for run, sel in enumerate((GOOD_SEL, None)):
    bs.OUT = str(same)
    bs.CATEGORY_SEO_FILE = str(tmp / "category_seo.json")
    bs.SELECTION_TEXTS_FILE = str(tmp / "selection_pages.json")
    (tmp / "selection_pages.json").unlink(missing_ok=True)
    if sel:
        (tmp / "selection_pages.json").write_text(json.dumps(sel, ensure_ascii=False), encoding="utf-8")
    bs.tp.fetch_toysi_catalog = lambda: dict(make_catalog(14))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        bs.build()
    if run == 0:
        assert (same / "zoshyty-u-kosu-liniiu.html").exists()
chk("повторна збірка без тексту прибирає застарілу zoshyty-u-kosu-liniiu.html", not (same / "zoshyty-u-kosu-liniiu.html").exists())

# shipped-стан: файлів нема → поведінка не змінилась
chk("shipped: site/category_seo.json і site/selection_pages.json (якщо є) проходять завантажувачі без попереджень",
    all(not (HERE / "site" / n).exists() or (bs._load_category_seo(str(HERE / "site" / n)) is not None) for n in ("category_seo.json",))
    and bs._load_selection_texts(str(HERE / "site" / "selection_pages.json")) is not None)
print("\n" + ("ALL OK" if not F else f"FAIL: {len(F)}"))
sys.exit(1 if F else 0)
