#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_feed_sale_price.py — «Знижка тижня» у фідах Google/Meta/Bing (14.10.2026): у фіді price = стара (звичайна) ціна, g:sale_price = акційна, g:sale_price_effective_date = from/until з пояссям;
фід, згенерований ДО старту акції, вже містить майбутні дати. Інваріанти: (1) load_feed_sale: поточний/майбутній тиждень → мапа, прострочений/зіпсований/відсутній файл → {} (фід як без акції); new<old обов'язково;
(2) build_feed_items: ціна картки = old (до старту) або new (під час) → у фіді price=old, sale_price=new, дати; ціна картки інша → акція НЕ показується (не розходимось зі сторінкою); (3) без акції фід ідентичний звичайному
(жодного sale_price у XML); (4) XML усіх трьох фідів містить обидва теги з тими самими значеннями й форматом дат 2026-10-26T00:00+0200/2026-11-01T23:59+0200; (5) російський фід теж несе акцію.
`python test_feed_sale_price.py` → exit 0/1."""
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
sys.path.insert(0, str(Path(__file__).parent / "site"))

import generate_bing_feed as gb
import generate_google_feed as gg
import generate_meta_feed as gm
import site_sale as ss

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


NS = {"g": "http://base.google.com/ns/1.0"}
tmp = Path(tempfile.mkdtemp())
sale_path = tmp / "sale_week.json"
FROM, UNTIL = "2026-10-26T00:00:00+02:00", "2026-11-01T23:59:59+02:00"
sale_path.write_text(json.dumps({"week": "2026-W44", "pct": 15, "from": FROM, "until": UNTIL, "items": {"1001": {"old": 400, "new": 340}, "1002": {"old": 100, "new": 100}, "1003": {"old": 50, "new": 60}}}), encoding="utf-8")
DATES = "2026-10-26T00:00+0200/2026-11-01T23:59+0200"

before_start = datetime.fromisoformat("2026-10-25T20:00:00+02:00")
m = ss.load_feed_sale(now=before_start, path=sale_path)
chk("до старту: мапа з дат, формат дат Google (без секунд, офсет +0200)", set(m) == {"1001"} and m["1001"] == {"old": 400, "new": 340, "dates": DATES})
chk("new ≥ old (1002: 100→100, 1003: 50→60) відкидається", "1002" not in m and "1003" not in m)
during = datetime.fromisoformat("2026-10-28T12:00:00+02:00")
chk("під час акції мапа та сама", ss.load_feed_sale(now=during, path=sale_path) == m)
chk("після закінчення (until минув) → {} (фід без акції)", ss.load_feed_sale(now=datetime.fromisoformat("2026-11-02T00:00:00+02:00"), path=sale_path) == {})
chk("файлу нема → {}", ss.load_feed_sale(path=tmp / "nope.json") == {})
bad = tmp / "bad.json"
bad.write_text("не json", encoding="utf-8")
chk("зіпсований файл → {}", ss.load_feed_sale(path=bad) == {})
nz = tmp / "nz.json"
nz.write_text(json.dumps({"from": "2026-10-26T00:00:00", "until": "2026-11-01T23:59:59", "items": {"1": {"old": 10, "new": 5}}}), encoding="utf-8")
chk("дати без пояса (наївні) → {} (двозначно, не публікуємо)", ss.load_feed_sale(now=datetime.fromisoformat("2026-10-25T20:00:00+02:00"), path=nz) == {})
chk("ключ items відсутній / нечислові ціни → {} без винятку", ss.load_feed_sale(now=before_start, path=(lambda p: (p.write_text(json.dumps({"from": FROM, "until": UNTIL, "items": {"1": {"old": "x"}}}), encoding="utf-8"), p)[1])(tmp / "x.json")) == {})


def item(pid, price=200.0):
    return {"id": pid, "name": "Плюшевий ведмідь", "vendor": "Test", "price": price, "stock": 5, "category_name": "М'які іграшки",
            "pictures": ["https://toysi.ua/p/enl_%s.jpg" % pid], "description": "Опис товару достатньої довжини для фіда.", "barcode": ""}


ss.load_feed_sale = lambda now=None, path=None: m           # у фідах — мапа з акцією


def build(card_price):
    its, st = gg.build_feed_items({"1001": item("1001")}, {}, {}, {}, None, {"1001": card_price})
    return its, st


its, st = build(400)
chk("ціна картки = old (до старту) → price=400, sale_price=340, дати", len(its) == 1 and its[0]["price"] == "400.00 UAH" and its[0]["sale_price"] == "340.00 UAH" and its[0]["sale_dates"] == DATES and st["on_sale"] == 1)
its, st = build(340)
chk("ціна картки = new (під час акції) → у фіді price=400 (звичайна), sale_price=340", its[0]["price"] == "400.00 UAH" and its[0]["sale_price"] == "340.00 UAH")
its, st = build(377)
chk("ціна картки інша (377: змінилась) → акцію НЕ показуємо, price=377 (не розходимось зі сторінкою)", its[0]["price"] == "377.00 UAH" and its[0]["sale_price"] is None and st["on_sale"] == 0)
its_off, _ = gg.build_feed_items({"1001": item("1001")}, {}, {}, {}, None, {"1001": 500})
chk("товар без акції: sale_price None, price як раніше", its_off[0]["sale_price"] is None and its_off[0]["price"] == "500.00 UAH")

# XML усіх трьох фідів
its, _ = build(340)
for label, mod in (("Google", gg), ("Meta", gm), ("Bing", gb)):
    root = mod._build_xml(its)
    e = root.find("channel/item")
    p, sp, sd = e.find("g:price", NS), e.find("g:sale_price", NS), e.find("g:sale_price_effective_date", NS)
    chk(f"{label}: XML має price=400.00 UAH, sale_price=340.00 UAH, sale_price_effective_date={DATES}", p is not None and p.text == "400.00 UAH" and sp is not None and sp.text == "340.00 UAH" and sd is not None and sd.text == DATES)
its_plain, _ = gg.build_feed_items({"1001": item("1001")}, {}, {}, {}, None, {"1001": 500})
for label, mod in (("Google", gg), ("Meta", gm), ("Bing", gb)):
    raw = ET.tostring(mod._build_xml(its_plain), encoding="unicode")
    chk(f"{label}: без акції в XML НЕМАЄ sale_price", "sale_price" not in raw)
# порядок тегів: price → sale_price (Google читає незалежно, але тримаємо поруч)
root = gg._build_xml(its)
tags = [c.tag.split("}")[-1] for c in root.find("channel/item")]
chk("sale_price і дати йдуть одразу за price", tags[tags.index("price") + 1:tags.index("price") + 3] == ["sale_price", "sale_price_effective_date"])
# російський фід несе акцію
ru_items, _ = gg.build_ru_items(its, {its[0]["pid"]: {"name": "Плюшевый мишка", "description": "Описание"}})
rraw = ET.tostring(gg._build_xml(ru_items), encoding="unicode")
chk("російський фід: sale_price та дати збережено", "340.00 UAH" in rraw and DATES in rraw)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
