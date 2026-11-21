#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_selection_tarilky_erudyt.py — 20.11.2026 (SEO 13:10, ТЗ_добірки_тарілки_і_ерудит): дві сторінки-добірки за назвою:
dytiachi-tarilky («Дитячий посуд» І «тарілк» у назві; батько — категорія dytiachyy-posud) і nastilna-hra-erudyt («ерудит» у назві, будь-яка категорія, крім уцінки; батько — nastilni-ihry).
(1) справжній site/selection_pages.json: обидва записи валідні (вступ, ≥3 FAQ) і проходять завантажувач без попереджень; (2) предикати: позитивні/негативні назви; (3) наскрізна збірка: ≥12 карток → сторінка
будується (H1, title, FAQPage, ItemList, крихти, посилання з категорії-батька, у sitemap), <12 → не будується; уцінка й noindex-картки до добору не входять."""
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


REAL_TEXTS = str(HERE / "site" / "selection_pages.json")
SP = {s: (h, t, par, fn) for s, h, t, par, _pn, fn in bs.SELECTION_PAGES}

# ── (1) справжні тексти ──
err = io.StringIO()
with contextlib.redirect_stderr(err):
    real = bs._load_selection_texts(REAL_TEXTS)
chk("selection_pages.json: dytiachi-tarilky і nastilna-hra-erudyt завантажені без попереджень (вступ 60–150 слів, ≥3 FAQ)",
    all(k in real and 60 <= len(real[k]["intro"].split()) <= 150 and len(real[k]["faq"]) >= 3 for k in ("dytiachi-tarilky", "nastilna-hra-erudyt")) and err.getvalue() == "")
chk("метадані сторінок за ТЗ: H1, title, батько",
    SP["dytiachi-tarilky"][:3] == ("Дитячі тарілки", "Дитячі тарілки на присосці й силіконові | PlutusToys", "dytiachyy-posud")
    and SP["nastilna-hra-erudyt"][:3] == ("Настільна гра Ерудит", "Настільна гра Ерудит українською — купити | PlutusToys", "nastilni-ihry")
    and len(SP["nastilna-hra-erudyt"][1]) <= bs.TITLE_MAX and len(SP["dytiachi-tarilky"][1]) <= bs.TITLE_MAX)

# ── (2) предикати ──
pt, pe = SP["dytiachi-tarilky"][3], SP["nastilna-hra-erudyt"][3]
mk = lambda n, c="Дитячий посуд": {"name": n, "category": c}  # noqa: E731
chk("тарілки: «Дитячий посуд» + «тарілк» — так; інша категорія (Пісочні набори), «Чашка» у «Дитячому посуді» — ні",
    pt(mk("Силіконова тарілка-килимок Зая")) and pt(mk("ТАРІЛКА секційна")) and not pt(mk("Тарілка для піску", "Пісочні набори")) and not pt(mk("Чашка з кришкою")))
chk("ерудит: кирилиця «ерудит» у назві в будь-якій категорії — так; латиниця «Erudit» і «Скрабл» — ні",
    pe(mk("Настільна гра \"Ерудит\" укр", "Розважальні")) and pe(mk("Гра \"ЕРУДИТ. Збери слово\"", "Логічні")) and not pe(mk("Erudit board game", "Логічні")) and not pe(mk("Скрабл", "Логічні")))

# ── (3) наскрізна збірка ──
site_photo_probe.run = lambda idx: None
tmp = Path(tempfile.mkdtemp())
bs.LIQPAY_LIVE = True
bs.SUPPLIER_KV_FILE = str(tmp / "no_kv.json")
bs.SUPPLIER_RT_FILE = str(tmp / "no_rt.json")
bs.CATEGORY_GROUPS_FILE = str(tmp / "none.json")
bs.SELECTION_TEXTS_FILE = REAL_TEXTS


def toysi(pid, name, cat, price=150, stock=5):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": stock, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/p/{pid}", "rt_cost": 10}


def build(cat):
    bs.tp.fetch_toysi_catalog = lambda: dict(cat)
    bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    e = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(e):
        bs.build()
    return out, e.getvalue()


cat = {}
for i in range(1, 15):
    cat[str(i)] = toysi(i, f"Силіконова тарілка-килимок Зая {i}", "Дитячий посуд")
cat["20"] = toysi(20, "Чашка непроливайка", "Дитячий посуд")
cat["21"] = toysi(21, "Тарілка для піску", "Пісочні набори")
cat["22"] = toysi(22, "Уцінка. Тарілка секційна", "Дитячий посуд")
for i in range(100, 114):
    cat[str(i)] = toysi(i, f"Настільна гра \"Ерудит\" укр {i}", "Розважальні" if i % 2 else "Настільні ігри")
cat["120"] = toysi(120, "Уцінка. Настільна гра \"Ерудит\" PREMIUM", "Розважальні")
for i in range(130, 135):
    cat[str(i)] = toysi(i, f"Настільна гра Мафія {i}", "Настільні ігри")
out, e = build(cat)
tp_, er = out / "dytiachi-tarilky.html", out / "nastilna-hra-erudyt.html"
ht = tp_.read_text(encoding="utf-8") if tp_.exists() else ""
he = er.read_text(encoding="utf-8") if er.exists() else ""
chk("14 тарілок → dytiachi-tarilky.html збудовано: H1, title, FAQPage + ItemList; у добір не потрапили чашка, піщана тарілка й уцінка",
    '<h1 class="page">Дитячі тарілки</h1>' in ht and "Дитячі тарілки на присосці й силіконові | PlutusToys" in ht and '"@type": "FAQPage"' in ht and '"@type": "ItemList"' in ht
    and ht.count('class="card') == 14 and "Чашка" not in ht and "піску" not in ht and "Уцінка" not in ht)
chk("14 «Ерудит» → nastilna-hra-erudyt.html збудовано: H1, title, FAQPage + ItemList; уцінка й «Мафія» не входять; картки з різних категорій",
    '<h1 class="page">Настільна гра Ерудит</h1>' in he and "Настільна гра Ерудит українською — купити | PlutusToys" in he and '"@type": "FAQPage"' in he and '"@type": "ItemList"' in he
    and he.count('class="card') == 14 and "Мафія" not in he and "Уцінка" not in he)
cd, cn = out / "category-dytiachyy-posud.html", out / "category-nastilni-ihry.html"
chk("категорії-батьки посилаються на нові сторінки",
    cd.exists() and 'href="dytiachi-tarilky.html"' in cd.read_text(encoding="utf-8") and cn.exists() and 'href="nastilna-hra-erudyt.html"' in cn.read_text(encoding="utf-8"))
chk("крихти ведуть у категорію-батька; обидві сторінки є в sitemap",
    'href="category-dytiachyy-posud.html"' in ht and 'href="category-nastilni-ihry.html"' in he
    and all(f"/{s}.html" in "".join(q.read_text(encoding="utf-8") for q in out.glob("sitemap-*.xml")) for s in ("dytiachi-tarilky", "nastilna-hra-erudyt")))
# менше порогу
for i in range(1, 15):
    if i > 6:
        del cat[str(i)]
for i in range(100, 114):
    if i > 105:
        del cat[str(i)]
out2, e2 = build(cat)
chk("менше 12 карток: сторінок нема, попередження названо, категорії-батьки без посилань",
    not (out2 / "dytiachi-tarilky.html").exists() and not (out2 / "nastilna-hra-erudyt.html").exists() and "selection: dytiachi-tarilky" in e2 and "selection: nastilna-hra-erudyt" in e2
    and 'href="dytiachi-tarilky.html"' not in (out2 / "category-dytiachyy-posud.html").read_text(encoding="utf-8"))
print("\n" + ("ALL OK" if not F else f"FAIL: {len(F)}"))
sys.exit(1 if F else 0)
