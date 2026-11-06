#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_unique_h1.py — 06.11.2026 (SEO: 591 група карток з ІДЕНТИЧНОЮ назвою, title розведено «№id», а <h1> — ні).
Перевіряє: (1) у групі однакових назв h1 унікальні; (2) спершу — одне поле характеристик картки з різними непорожніми значеннями в усій групі («— колір: …»), інакше « №id» як у title;
(3) картки поза групами, index.json, title, JSON-LD, alt — байт-у-байт як без розведення; (4) HTML у значенні екранується; (5) h1 ≤ H1_MAX; (6) детермінізм; (7) лічильник у stderr."""
import contextlib
import html
import io
import json
import os
import random
import re
import sys
import tempfile
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["SITE_PICK_DATE"] = "2026-11-06"
for k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID", "SITE_BUILD_LIMIT"):
    os.environ.pop(k, None)
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402
import site_photo_probe  # noqa: E402
import telegram_notify  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


site_photo_probe.run = lambda idx: None                                  # без мережі
telegram_notify.send_throttled_alert = lambda *a, **k: True              # НІКОЛИ не слати реальні алерти з тесту
LEAD = "Опис товару достатньої довжини для картки сайту, понад сорок символів."


def item(pid, name, specs=(), stock=5, vendor="", cat="Брелоки"):
    desc = LEAD + "".join(f"<br><b>{k}:</b> {v}" for k, v in specs)
    return {"id": str(pid), "name": name, "vendor": vendor, "price": 200 + pid % 7, "stock": stock, "category_name": cat, "category_id": "10",
            "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "description": desc, "params": [], "barcode": ""}


catalog = {}
# A: колір різний у кожної — розведення атрибутом
for pid, col in ((201, "Сірий"), (202, "Чорно-білий"), (203, "Білий")):
    catalog[str(pid)] = item(pid, 'Брелок "Панда"', [("Бренд", "Fresh"), ("Колір", col)])
# B: ніякого розрізняльного поля (колір однаковий), одна картка без залишку — «№id»
for pid, st in ((211, 5), (212, 5), (213, 0)):
    catalog[str(pid)] = item(pid, "Набір «Фокуси»", [("Колір", "Мікс")], stock=st)
# C: назви різняться лише регістром/розділовими знаками; бренди MIC (латиниця) / МІС (кирилиця) — на око однакові → НЕ розрізняють → «№id»
catalog["221"] = item(221, 'Конструктор "Ninja" (вид A)', [("Бренд", "MIC")])
catalog["222"] = item(222, "КОНСТРУКТОР NINJA ВИД A", [("Бренд", "МІС")])
# D: HTML у значенні атрибута (в описі Toysi — як сутності) має бути екранований у h1
catalog["231"] = item(231, "Лялька Сюрприз", [("Колір", "&lt;img src=x onerror=alert(1)&gt;")])
catalog["232"] = item(232, "Лялька Сюрприз", [("Колір", 'Червоний & "синій"')])
# E: колір є не в усіх → наступне за пріоритетом поле (матеріал); комплектація задовга (> H1_MAX) не береться
catalog["241"] = item(241, "Набір для годування", [("Колір", "Жовтий"), ("Матеріал", "Пластик"), ("Комплектація", "Тарілка " + "x" * 120)])
catalog["242"] = item(242, "Набір для годування", [("Матеріал", "Силікон"), ("Комплектація", "Тарілка " + "y" * 120)])
# F: єдине розрізняльне поле задовге → «№id»
catalog["251"] = item(251, "Пісочний набір", [("Комплектація", "Відерце " + "a" * 120)])
catalog["252"] = item(252, "Пісочний набір", [("Комплектація", "Відерце " + "b" * 120)])
# G: розмір — «Габарити без упаковки»; габарити В УПАКОВЦІ ігноруються
catalog["261"] = item(261, "Ванночка для ляльок", [("Габарити в упаковці", "10 x 10 x 10 см"), ("Габарити без упаковки", "26 x 10 x 15 см")])
catalog["262"] = item(262, "Ванночка для ляльок", [("Габарити в упаковці", "11 x 10 x 10 см"), ("Габарити без упаковки", "26 x 11 x 9 см")])
GROUPS = {"A": ("201", "202", "203"), "B": ("211", "212", "213"), "C": ("221", "222"), "D": ("231", "232"), "E": ("241", "242"), "F": ("251", "252"), "G": ("261", "262")}
GROUP_IDS = {i for g in GROUPS.values() for i in g}
for i in range(300, 330):                                                 # одинокі картки (частина — з характеристиками, частина — без залишку)
    catalog[str(i)] = item(i, f"Звичайна іграшка номер {i}", [("Колір", "Синій")] if i % 3 == 0 else (), stock=0 if i % 10 == 0 else 4, cat="Машинки" if i % 2 else "Ляльки")
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.CATEGORY_GROUPS_FILE = str(Path(tempfile.mkdtemp()) / "none.json")
bs.LIMIT = 0
_real_assign = bs.assign_product_h1
_NO_H1 = ({}, {"groups": 0, "cards": 0, "attr_groups": 0, "attr_cards": 0, "id_cards": 0, "by": {}, "unresolved": 0})


def run_build(cat, no_h1=False):
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    bs.tp.fetch_toysi_catalog = lambda: dict(cat)
    bs.assign_product_h1 = (lambda items: _NO_H1) if no_h1 else _real_assign
    err = io.StringIO()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            bs.build()
    finally:
        bs.assign_product_h1 = _real_assign
    return out, err.getvalue()


out, err = run_build(catalog)
base, _ = run_build(catalog, no_h1=True)                                  # та сама збірка без розведення h1 = поведінка до зміни


def h1(o, i):
    return re.search(r'<h1 class="prod">(.*?)</h1>', (o / f"product-{i}.html").read_text(encoding="utf-8"), re.S).group(1)


H = {i: h1(out, i) for i in GROUP_IDS}
for g, ids in GROUPS.items():
    chk(f"група {g}: h1 попарно різні ({[H[i] for i in ids]})", len({H[i] for i in ids}) == len(ids))
chk(f"A: розведено кольором: {H['201']}", H["201"] == "Брелок &quot;Панда&quot; — колір: сірий" and H["202"].endswith("— колір: чорно-білий"))
chk(f"B: нема розрізняльного поля → « №id» (у т.ч. картка без залишку): {H['213']}", H["211"] == "Набір «Фокуси» №211" and H["213"] == "Набір «Фокуси» №213")
chk(f"C: регістр/розділові знаки/MIC≈МІС — одна група, бренд не розрізняє → «№id»: {H['221']} | {H['222']}", H["221"].endswith(" №221") and H["222"] == "КОНСТРУКТОР NINJA ВИД A №222")
chk(f"D: HTML у значенні екранується: {H['231']}", "<img" not in H["231"] and H["231"] == "Лялька Сюрприз — колір: &lt;img src=x onerror=alert(1)&gt;" and H["232"] == "Лялька Сюрприз — колір: червоний &amp; &quot;синій&quot;")
chk(f"E: колір не в усіх, комплектація задовга → матеріал: {H['241']}", H["241"] == "Набір для годування — матеріал: пластик" and H["242"] == "Набір для годування — матеріал: силікон")
chk(f"F: єдине поле задовге → «№id»: {H['251']}", H["251"] == "Пісочний набір №251")
chk(f"G: розмір без упаковки (не габарити в упаковці): {H['261']}", H["261"] == "Ванночка для ляльок — розмір: 26 x 10 x 15 см")
chk(f"усі h1 груп ≤ H1_MAX={bs.H1_MAX} після декодування (max {max(len(html.unescape(v)) for v in H.values())})", all(len(html.unescape(v)) <= bs.H1_MAX for v in H.values()))
# байт-у-байт: збірка без розведення відрізняється ЛИШЕ рядком <h1> у картках груп
diff_files, other_diff = [], []
for f in sorted(base.rglob("*")):
    if f.is_dir():
        continue
    rel = f.relative_to(base)
    a = f.read_bytes()
    b = (out / rel).read_bytes() if (out / rel).exists() else None
    if a == b:
        continue
    m = re.fullmatch(r"product-(\d+)\.html", rel.name)
    if m and m.group(1) in GROUP_IDS and b is not None:
        diff_files.append(m.group(1))
        if re.sub(rb'<h1 class="prod">.*?</h1>', b"H1", a, flags=re.S) != re.sub(rb'<h1 class="prod">.*?</h1>', b"H1", b, flags=re.S):
            other_diff.append(str(rel))
    elif rel.name == bs.LASTMOD_STATE_FILE and b is not None:   # стан lastmod: «t» = час збірки (нові сторінки), тож порівнюємо хеш «h» — він інший ЛИШЕ в картках груп (їх вміст справді змінився)
        sa, sb = json.loads(a), json.loads(b)
        if set(sa) != set(sb) or {k for k in sa if sa[k]["h"] != sb[k]["h"]} - {f"product-{i}.html" for i in GROUP_IDS}:
            other_diff.append(str(rel))
    else:
        other_diff.append(str(rel))
chk(f"той самий набір файлів ({len(list(base.rglob('*')))})", sorted(p.relative_to(base) for p in base.rglob("*")) == sorted(p.relative_to(out) for p in out.rglob("*")))
chk(f"відрізняються лише картки груп ({len(diff_files)} із {len(GROUP_IDS)}) і лише рядком <h1>; решта (index.json, каталог, категорії, sitemap, одинокі картки) байт-у-байт: {other_diff[:5]}",
    not other_diff and set(diff_files) == GROUP_IDS)
p201 = (out / "product-201.html").read_text(encoding="utf-8")
chk("title / JSON-LD name / alt — сира назва (h1-розведення не просочується)",
    '"name": "Брелок \\"Панда\\""' in p201 and 'alt="Брелок &quot;Панда&quot;"' in p201 and "колір" not in re.search(r"<title>(.*?)</title>", p201, re.S).group(1))
idx = (out / "index.json").read_text(encoding="utf-8")
chk("index.json (пошук/кошик) — без «— колір:» і без «№id» у назвах", "— колір:" not in idx and "№2" not in idx)
# детермінізм: результат залежить від складу групи, а не від порядку
items = [{"id": k, "name": v["name"], "desc": v["description"], "vendor": v["vendor"], "rt": 0} for k, v in catalog.items()]
ref = _real_assign(items)
same = True
for seed in range(5):
    sh = list(items)
    random.Random(seed).shuffle(sh)
    same = same and _real_assign(sh) == ref
chk("детермінізм: перемішаний порядок карток → ті самі h1 і та сама статистика", same)
chk("rt-картка у групі «№id» — код «PT-R…», не «rt-…»",
    _real_assign([{"id": "rt-9", "name": "X Y", "desc": "", "vendor": "", "rt": 1}, {"id": "rt-8", "name": "X Y", "desc": "", "vendor": "", "rt": 1}])[0] == {"rt-9": "X Y №PT-R9", "rt-8": "X Y №PT-R8"})
m = re.search(r"\[build\] h1: (\d+) груп з однаковою назвою \((\d+) карток; у наявності (\d+) груп / (\d+) карток\) — розведено атрибутом (\d+) карток у (\d+) групах \((.*?)\), «№id» (\d+) карток, не розведено (\d+)", err)
chk(f"лічильник у stderr: {m.group(0) if m else err[-300:]}", bool(m) and m.groups()[:6] == ("7", "16", "7", "15", "9", "4") and m.group(7) == "колір 2, матеріал 1, розмір 1" and m.group(8) == "7" and m.group(9) == "0")
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
