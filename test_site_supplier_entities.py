#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_supplier_entities.py — 10.11.2026 (SEO: на живих картках RT покупець бачить «м&#39;яконабивна», «Build&amp;amp;Play», «110&730;»).
Причина: фід постачальника віддає назву з HTML-сутностями, а сайт екранує назву ще раз (esc) -> сутність показується буквально.
Інваріанти:
  • _supplier_text_plain розкодовує сутності ОДИН раз; рядок без «&» і не-рядок — без змін;
  • картка RT: name/vendor розкодовані, тож на сторінці <title>/<h1> — «м'яконабивна», «Build&amp;Play» у HTML (= «Build&Play» для людини), без «&amp;#39;»/«&amp;amp;»;
  • опис RT: «&730;» (втрачений «#») стає «&#730;» (браузер покаже «˚»); валідні сутності й звичайний текст не чіпаємо;
  • назви без сутностей — без змін (байт у байт)."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


P = bs._supplier_text_plain
chk("&#39; -> ', &amp; -> &, &quot; -> \"", P("Іграшка м&#39;яконабивна") == "Іграшка м'яконабивна" and P("Build&amp;Play") == "Build&Play" and P("Набір &quot;А&quot;") == 'Набір "А"')
chk("один прохід: «&amp;amp;» -> «&amp;» (не глибше)", P("A&amp;amp;B") == "A&amp;B")
chk("без «&» і не-рядок — без змін", P("Звичайна назва 5 шт") == "Звичайна назва 5 шт" and P("") == "" and P(None) is None and P(7) == 7)
chk("голий амперсанд без сутності лишається", P("Tom & Jerry") == "Tom & Jerry")

tmp = Path(tempfile.mkdtemp())
now = datetime.now(timezone.utc)
base = {"category": "Конструкторы", "images": ["https://plutustoys.com.ua/img/rt/rt-1-1.jpg"], "price": 480, "cost": 290, "stock": 7, "supplier": "royaltoys", "vendor_code": "RT-1"}
items = {
    "rt-1": {**base, "name": "Іграшка м&#39;яконабивна набір кубиків", "vendor": "Build&amp;Play", "description": "Стілець, кут нахилу 110&730;. Ще &amp; текст."},
    "rt-2": {**base, "name": "Звичайна назва без сутностей", "vendor": "HANYE", "description": "Опис без сутностей."},
}
(tmp / "s.json").write_text(json.dumps({"updated": now.isoformat(), "items": items}, ensure_ascii=False), encoding="utf-8")
(tmp / "m.json").write_text(json.dumps({"Конструкторы": "Конструктори RT"}, ensure_ascii=False), encoding="utf-8")
bs.SUPPLIER_RT_FILE, bs.RT_CATEGORY_MAP_FILE = str(tmp / "s.json"), str(tmp / "m.json")
bs.LIQPAY_LIVE = True
with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
    res = bs._load_supplier_rt()
a, b = res.get("rt-1"), res.get("rt-2")
chk("RT: обидві картки завантажено", bool(a) and bool(b))
chk("RT: назва і бренд розкодовані", a["name"] == "Іграшка м'яконабивна набір кубиків" and a["vendor"] == "Build&Play")
chk("RT: опис — «&730;» -> «&#730;», валідна «&amp;» не чіпана", "110&#730;." in a["description"] and "&730;" not in a["description"] and "Ще &amp; текст." in a["description"])
chk("RT: назва/бренд/опис без сутностей — без змін", b["name"] == "Звичайна назва без сутностей" and b["vendor"] == "HANYE" and b["description"] == "Опис без сутностей.")
chk("після esc() у сторінці немає подвійного екранування", "&amp;#39;" not in bs.esc(a["name"]) and "&amp;amp;" not in bs.esc(a["vendor"]) and bs.esc(a["vendor"]) == "Build&amp;Play")

# ── Toysi (SEO 10.11: 131989 «Create &amp; Play», 154061 «Mega Stretch Slime &amp; Fluffy Slime»): повна збірка
def item(pid, name):
    return {"id": str(pid), "name": name, "vendor": "Test", "price": 300.0, "stock": 5, "category_name": "Машинки", "category_id": "10",
            "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "description": "Опис товару достатньої довжини для картки, щоб він не вважався тонким і мав нормальний вигляд.", "barcode": "", "supplier": "Toysi"}   # parser.py:219 додає supplier="Toysi" усім Toysi-карткам: фікстура мусить мати його, інакше тест перевіряє не той формат


catalog = {str(i): item(i, f"Іграшка номер {i}") for i in range(1, 41)}
catalog["131989"] = item(131989, 'Набір слайм "2 в 1: Mega Stretch Slime &amp; Fluffy Slime"')
catalog["154061"] = item(154061, 'Конструктор &quot;Create &amp; Play Поліція&quot; м&#39;який')
bs.tp.fetch_toysi_catalog = lambda: catalog
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
import telegram_notify  # noqa: E402
telegram_notify.send_throttled_alert = lambda *a, **k: True
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None
bs.LIMIT = 0
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
    bs.build()
h1 = (out / "product-131989.html").read_text(encoding="utf-8")
h2 = (out / "product-154061.html").read_text(encoding="utf-8")
ix = {x["id"]: x["n"] for x in json.loads((out / "index.json").read_text(encoding="utf-8"))}
chk("Toysi: <title>/<h1> без «&amp;amp;» і «&amp;#39;»/«&amp;quot;»", not re.search(r"&amp;(amp|#39|quot);", h1 + h2))
chk("Toysi: «Slime &amp; Fluffy» (один раз екрановано), лапки й апостроф чисті",
    "Mega Stretch Slime &amp; Fluffy Slime" in h1 and "Create &amp; Play Поліція" in h2 and any(x in h2 for x in ("м&#x27;який", "м&#39;який", "м'який")))
chk("Toysi: index.json n — чисті символи (клієнт екранує сам)", ix["131989"] == 'Набір слайм "2 в 1: Mega Stretch Slime & Fluffy Slime"' and ix["154061"] == "Конструктор \"Create & Play Поліція\" м'який")
chk("Toysi: звичайна назва без змін", ix["5"] == "Іграшка номер 5")

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
