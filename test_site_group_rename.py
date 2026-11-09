#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_group_rename.py — 07.11.2026 (SEO: група «Побутова хімія» з косметикою/гігієною мала хибну назву → «Догляд та гігієна»).
Перевіряє: (1) у реальному site/category_groups.json група «NEW: ПОБУТОВА ХІМІЯ» має display «Догляд та гігієна» і former_files з її старим файлом;
(2) збірка: нова група (group-<slug від display>.html) існує, а СТАРИЙ файл group-pobutova-khimiia.html — сторінка-вказівник
(noindex, canonical і refresh на нову, посилання) і НЕ 404/не стерта кроком прибирання; (3) без former_files старий файл не створюється;
(4) некоректні former_files (не group-*.html, ../, великі літери) відкидаються; (5) нова назва не містить слова «хімія»."""
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["SITE_PICK_DATE"] = "2026-11-07"
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


site_photo_probe.run = lambda idx: None
telegram_notify.send_throttled_alert = lambda *a, **k: True

# (1) реальний файл
real = json.loads((HERE / "site" / "category_groups.json").read_text(encoding="utf-8"))
g0 = next(g for g in real["groups"] if g["name"] == "NEW: ПОБУТОВА ХІМІЯ")
chk("реальний category_groups.json: display «Догляд та гігієна», former_files = [group-pobutova-khimiia.html]",
    g0.get("display") == "Догляд та гігієна" and g0.get("former_files") == ["group-pobutova-khimiia.html"])
chk("нова назва групи без слова «хімі»", "хімі" not in g0["display"].lower())

LEAD = "Опис товару достатньої довжини для картки сайту, понад сорок символів."
catalog = {}
for i in range(1, 9):
    catalog[str(i)] = {"id": str(i), "name": f"Крем для рук номер {i}", "vendor": "", "price": 200 + i, "stock": 5, "category_name": "Косметичні засоби",
                       "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{i}.jpg"], "description": LEAD, "params": [], "barcode": ""}
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.LIMIT = 0


def build_with(groups_obj):
    gf = Path(tempfile.mkdtemp()) / "g.json"
    gf.write_text(json.dumps(groups_obj, ensure_ascii=False), encoding="utf-8")
    bs.CATEGORY_GROUPS_FILE = str(gf)
    out = Path(tempfile.mkdtemp())
    (out / "group-pobutova-khimiia.html").write_text("old", encoding="utf-8")        # залишок попередньої збірки — крок прибирання не має зробити з нього 404 при former_files
    bs.OUT = str(out)
    bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        bs.build()
    return out


G = {"name": "NEW: ПОБУТОВА ХІМІЯ", "subs": ["Косметичні засоби"], "display": "Догляд та гігієна"}
out = build_with({"groups": [dict(G, former_files=["group-pobutova-khimiia.html"])]})
new_files = [p.name for p in out.glob("group-*.html") if p.name != "group-pobutova-khimiia.html"]
chk(f"збірка: нова група створена ({new_files})", len(new_files) == 1 and new_files[0] != "group-pobutova-khimiia.html")
old = (out / "group-pobutova-khimiia.html")
oldt = old.read_text(encoding="utf-8") if old.exists() else ""
nf = new_files[0] if new_files else "?"
chk("старий файл групи існує і це вказівник: noindex, canonical на нову, refresh 0 → нова, посилання", old.exists() and "old" != oldt
    and "noindex" in oldt and f'<link rel="canonical" href="https://plutustoys.com.ua/{nf}"' in oldt
    and f'http-equiv="refresh" content="0; url={nf}"' in oldt and f'href="{nf}"' in oldt)
chk("вказівник не потрапив у sitemap", "group-pobutova-khimiia" not in "".join(p.read_text(encoding="utf-8", errors="replace") for p in out.glob("sitemap*.xml")))
chk("нова сторінка групи індексується (без noindex) і має h1 «Догляд та гігієна»", nf != "?" and "noindex" not in (out / nf).read_text(encoding="utf-8")
    and "Догляд та гігієна</h1>" in (out / nf).read_text(encoding="utf-8"))

out = build_with({"groups": [dict(G)]})
chk("без former_files старий файл прибрано (як було)", not (out / "group-pobutova-khimiia.html").exists())

out = build_with({"groups": [dict(G, former_files=["../x.html", "Group-A.html", "group-ok.html", 5, "group-a b.html"])]})
chk("некоректні former_files відкинуто, коректний group-ok.html — створено", (out / "group-ok.html").exists() and not (out / "x.html").exists()
    and not any(p.name.startswith("Group") for p in out.iterdir()))

# former_files, що збігається з ЖИВОЮ сторінкою (власною чи іншої групи), відкидається: сторінка лишається справжньою й у sitemap
G2 = {"name": "Інша група", "subs": ["Брелоки"], "display": "Інша група"}
for i in range(101, 109):
    catalog[str(i)] = dict(catalog["1"], id=str(i), name=f"Брелок номер {i}", category_name="Брелоки", pictures=[f"https://toysi.ua/p/enl_{i}.jpg"])
_probe = build_with({"groups": [dict(G), dict(G2)]})
_files = sorted(p.name for p in _probe.glob("group-*.html") if p.name != "group-pobutova-khimiia.html")
_own, _other = _files[0], _files[-1]
out = build_with({"groups": [dict(G, former_files=[_own, _other]), dict(G2)]})
_sm = "".join(p.read_text(encoding="utf-8", errors="replace") for p in out.glob("sitemap*.xml"))
chk("former_files = жива сторінка (власна/чужа): не вказівник — обидві групи справжні, індексуються, у sitemap",
    len(_files) == 2 and all("noindex" not in (out / f).read_text(encoding="utf-8") and f in _sm for f in _files))

# non_toy: групи недитячого асортименту (Dropt) — у <title> без слова «іграшки»; звичайні групи — як було
G3 = {"name": "Електроніка і звук", "subs": ["Косметичні засоби"], "display": "Електроніка і звук", "non_toy": True}
out = build_with({"groups": [G3]})
_nf = [p.name for p in out.glob("group-*.html") if p.name != "group-pobutova-khimiia.html"]
_t = (out / _nf[0]).read_text(encoding="utf-8") if _nf else ""
chk("група non_toy: <title> «… — в наявності | PlutusToys» без слова «іграшки»", len(_nf) == 1 and "Електроніка і звук — в наявності | PlutusToys" in _t and "іграшки в наявності" not in _t)
out = build_with({"groups": [dict(G3, non_toy=False)]})
_nf = [p.name for p in out.glob("group-*.html") if p.name != "group-pobutova-khimiia.html"]
_t = (out / _nf[0]).read_text(encoding="utf-8") if _nf else ""
chk("група без non_toy — як раніше: «— іграшки в наявності»", len(_nf) == 1 and "іграшки в наявності | PlutusToys" in _t)
_own = set(bs.DROPT_OWN_CATEGORIES)
_grp = {g["name"]: g for g in real["groups"] if g.get("non_toy")}
chk("реальний category_groups.json: три non_toy-групи (Електроніка і звук / Туризм, відпочинок і сумки / Товари для тварин), їхні підкатегорії — лише власні категорії Dropt, разом усі п'ять",
    set(_grp) == {"Електроніка і звук", "Туризм, відпочинок і сумки", "Товари для тварин"} and all(set(g["subs"]) <= _own for g in _grp.values())
    and set().union(*(set(g["subs"]) for g in _grp.values())) == _own)

if F:
    print("\n❌ ПРОВАЛЕНО:", F)
    sys.exit(1)
print("\n✅ перейменування групи: нова група, вказівник зі старого URL, фільтр former_files — коректні.")
