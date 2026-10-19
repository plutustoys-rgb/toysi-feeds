#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rt_supplier_export.py — пілот RoyalToys (19.10.2026): експорт даних для сайту. Інваріанти: (1) ціна за закритою формулою max(опт×1,5;(опт+15)×1,03) вгору до гривні; (2) лише укр. назва (нема name_ua → не публікуємо), без фото → не публікуємо, ціна ≤0/нечислова → ні; (3) stock=0 якщо не available;
(4) відбір лише за rt_pilot_ids.json (209 id, унікальні, префікс rt-); (5) порожній YML → попередній файл НЕ чіпаємо; (6) файл приватний (не в site/), атомарний, "updated" ISO UTC; (7) stock_snapshot додає rt-<id> з файлу, а старий файл (>3 діб)/відсутній → ключів rt- нема."""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import rt_supplier_export as ex  # noqa: E402
import stock_snapshot as ss  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


chk("ціна: 100 → max(150; 118,45) = 150; 20 → max(30; 36,05) = 37; 10,5 → max(15,75; 26,3) = 27", ex.site_price(100) == 150 and ex.site_price(20) == 37 and ex.site_price(10.5) == 27)
ids = ex.load_pilot_ids()
chk("rt_pilot_ids.json: 209 унікальних id з префіксом rt-", len(ids) == 209 == len(set(ids)) and all(i.startswith("rt-") for i in ids))
chk("офіс. каталог не публікує гроші в site/: OUT_FILE поза site/", "site" not in ex.OUT_FILE.parts)
V = {"name_ua": "Іграшка", "description_ua": " Опис ", "price": "200", "stock": 7, "available": True, "vendor": "V", "vendor_code": "C1", "category_name": "Конструкторы"}
it = ex.build_item("rt-1", V, ["https://x/rt-1-1.jpg"])
chk("елемент: ціна 300, cost 200, stock 7, категорія рос., опис обрізано", it["price"] == 300 and it["cost"] == 200.0 and it["stock"] == 7 and it["category"] == "Конструкторы" and it["description"] == "Опис")
chk("опис: HTML-теги прибрано, сутності розкодовано, абзаци лишились", ex.plain_text("<p>Один &amp; два</p><p>Три<br>чотири</p>") == "Один & два\nТри\nчотири" and ex.plain_text(None) == "")
chk("не available → stock 0", ex.build_item("rt-1", dict(V, available=False), ["u"])["stock"] == 0)
chk("нема name_ua → None", ex.build_item("rt-1", dict(V, name_ua=""), ["u"]) is None)
chk("без фото → None", ex.build_item("rt-1", V, []) is None)
for bad in ("0", "-5", "abc", "", "nan"):
    chk(f"ціна {bad!r} → None", ex.build_item("rt-1", dict(V, price=bad), ["u"]) is None)
chk("залишок нечисловий → 0, не падіння", ex.build_item("rt-1", dict(V, stock="x"), ["u"])["stock"] == 0)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    out = td / "supplier_rt.json"
    cat = {"1": dict(V, pictures=["http://a/1.png", "http://a/2.png"]), "2": dict(V, name_ua="", pictures=["http://a/3.png"])}
    got = []
    fake = lambda url, dest: (got.append(dest.name), True)[1]  # noqa: E731
    ok = ex.run_once(cat, ["rt-1", "rt-2", "rt-3"], out, fake, td / "img")
    d = json.loads(out.read_text(encoding="utf-8"))
    chk("run_once: записано лише валідну (rt-1); rt-2 без укр. назви і rt-3 (нема в YML) пропущено", ok and list(d["items"]) == ["rt-1"])
    chk("фото: 2 шт., URL на наш сервер /img/rt/rt-1-N.jpg", d["items"]["rt-1"]["images"] == ["https://plutustoys.com.ua/img/rt/rt-1-1.jpg", "https://plutustoys.com.ua/img/rt/rt-1-2.jpg"] and got == ["rt-1-1.jpg", "rt-1-2.jpg"])
    upd = datetime.fromisoformat(d["updated"])
    chk('"updated" ISO із поясом UTC, свіжий', upd.tzinfo is not None and abs((datetime.now(timezone.utc) - upd).total_seconds()) < 60)
    before = out.read_text(encoding="utf-8")
    chk("порожній YML → False, файл не змінено", ex.run_once({}, ["rt-1"], out, fake, td / "img") is False and out.read_text(encoding="utf-8") == before)
    chk("жодної валідної картки → False, файл не змінено", ex.run_once({"9": V}, ["rt-1"], out, fake, td / "img") is False and out.read_text(encoding="utf-8") == before)
    chk("фото не отримано (fetch=False) → картка не публікується", ex.run_once(cat, ["rt-1"], td / "o2.json", lambda u, d_: False, td / "img") is False and not (td / "o2.json").exists())
    # stock_snapshot
    chk("stock_snapshot: rt-1 → 7 з файлу", ss.rt_stock_entries(out) == {"rt-1": 7})
    old = {"updated": (datetime.now(timezone.utc) - timedelta(days=3, hours=1)).isoformat(), "items": {"rt-1": {"stock": 5}}}
    (td / "old.json").write_text(json.dumps(old), encoding="utf-8")
    chk("stock_snapshot: файл старший за 3 доби → {} (fail-closed)", ss.rt_stock_entries(td / "old.json") == {})
    chk("stock_snapshot: нема файлу / сміття → {}", ss.rt_stock_entries(td / "none.json") == {} and ss.rt_stock_entries(td / "o2.json") == {})
    (td / "bad.json").write_text("{не json", encoding="utf-8")
    chk("stock_snapshot: битий JSON → {}", ss.rt_stock_entries(td / "bad.json") == {})
    stk = td / "stock.json"
    sup = td / "sup.json"
    sup.write_text(json.dumps({"updated": datetime.now(timezone.utc).isoformat(), "items": {"rt-1": {"stock": 4}}}), encoding="utf-8")
    ex.OUT_FILE = sup
    import parser as _tp  # noqa: E402
    _tp.assert_catalog_size_sane = lambda c: None
    ss.run_once(fetch=lambda: {"100": {"stock": 5}}, out=stk)
    snap = json.loads(stk.read_text(encoding="utf-8")) if stk.exists() else {}
    chk("run_once stock_snapshot: у stock.json є Toysi-ключ і rt-1", snap.get("s", {}).get("100") == 5 and snap.get("s", {}).get("rt-1") == 4)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
