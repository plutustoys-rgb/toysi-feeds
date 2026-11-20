#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_rt_name_overrides.py — 20.11.2026 (SEO 13:07/13:33): site/rt_name_overrides.json {"rt-<id>": {"from", "to"}} — точкові виправлення назв карток RoyalToys.
(1) юніт: завантажувач (поганий id/не-RT/однакові/порожні/не-рядки/службовий ключ), apply_rt_name_override (збіг from → to; розбіжність → назва лишається + один WARN);
(2) справжній файл: валідний, 13 записів, усі «rt-…», to ≠ from, правки з ТЗ;
(3) наскрізна збірка з RT-фікстурою: назва в index.json і на картці виправлена; змінена постачальником назва — лишається, WARN у stderr; Toysi-товар не чіпається."""
import contextlib
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
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


tmp = Path(tempfile.mkdtemp())
REAL = bs._RT_NAME_OVERRIDES_FILE


def load_ov(data):
    f = tmp / "ov.json"
    f.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")
    bs._RT_NAME_OVERRIDES_FILE = str(f)
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        res = bs._load_rt_name_overrides()
    return dict(res), err.getvalue()


# ── (1) юніт ──
r, e = load_ov({"_призначення": "службове", "rt-1": {"from": "Самолетик", "to": "Літачок"}, "rt-2s3": {"from": " А б ", "to": "А"},
                "123": {"from": "x", "to": "y"}, "rt-4": {"from": "Same", "to": "Same"}, "rt-5": {"from": "A"}, "rt-6": "рядок", "rt-7": {"from": "", "to": "B"}, "rt-8": {"from": 1, "to": "B"}})
chk("завантажувач бере лише валідні «rt-…» з рядками from≠to; службовий ключ ігнорує; решту 6 відкидає ГОЛОСНО",
    set(r) == {"rt-1", "rt-2s3"} and r["rt-2s3"] == ("А б", "А") and "6 записів відкинуто" in e)
r, e = load_ov("{ не json")
chk("поламаний JSON: порожньо, голосно, збірка не падає", r == {} and "rt_name_overrides.json" in e)
r, e = load_ov([1, 2])
chk("не об'єкт: порожньо, голосно", r == {} and "очікується об'єкт" in e)
bs._RT_NAME_OVERRIDES_FILE = str(tmp / "нема.json")
e0 = io.StringIO()
with contextlib.redirect_stderr(e0):
    r0 = bs._load_rt_name_overrides()
chk("нема файлу: порожньо й без шуму", r0 == {} and e0.getvalue() == "")
load_ov({"rt-1": {"from": "Самолетик Максик Червоний", "to": "Літачок Максик Червоний"}})
err = io.StringIO()
with contextlib.redirect_stderr(err):
    a1 = bs.apply_rt_name_override("rt-1", "Самолетик Максик Червоний")
    a2 = bs.apply_rt_name_override("rt-1", "Літачок новий")
    a3 = bs.apply_rt_name_override("rt-1", "Літачок новий")
    a4 = bs.apply_rt_name_override("rt-99", "Без виправлення")
chk("збіг from → to; розбіжність → назва постачальника лишається; чужий id без змін",
    a1 == "Літачок Максик Червоний" and a2 == "Літачок новий" and a3 == "Літачок новий" and a4 == "Без виправлення")
chk("розбіжність дає рівно один WARN на id", err.getvalue().count("назва постачальника змінилась") == 1 and "rt-1" in err.getvalue())

# ── (2) справжній файл ──
bs._RT_NAME_OVERRIDES_FILE = REAL
ereal = io.StringIO()
with contextlib.redirect_stderr(ereal):
    real = dict(bs._load_rt_name_overrides())
raw = json.loads(Path(REAL).read_text(encoding="utf-8"))
chk("справжній rt_name_overrides.json: 13 валідних записів, жоден не відкинуто",
    len(real) == 13 and ereal.getvalue() == "" and set(real) == {k for k in raw if not k.startswith("_")})
chk("правки з ТЗ: Соціальні сервіси / Літачок / Чарівна валізка ×2 / кілечок / Монстрик ×6; без «із безпечних матерів» ×2",
    "Соціальні сервіси" in real["rt-42389"][1] and "Літачок Максик" in real["rt-25852s33794"][1]
    and all("Чарівна валізка" in real[k][1] for k in ("rt-69816", "rt-69817")) and "80 кілечок" in real["rt-70755"][1]
    and all("Монстрик" in real[f"rt-{i}"][1] and "Монстрік" not in real[f"rt-{i}"][1] for i in range(59572, 59578))
    and all("безпечних матерів" not in real[k][1] and real[k][1].endswith(("Зелений", "Синій")) for k in ("rt-47109s59593", "rt-47109s59594"))
    and all(a != b for a, b in real.values()))
chk("кожна правка змінює лише потрібне (різниця довжин ≤ 25 знаків)", all(abs(len(a) - len(b)) <= 25 for a, b in real.values()))

# ── (3) наскрізна збірка ──
site_photo_probe.run = lambda idx: None
bs.LIQPAY_LIVE = True
now = datetime.now(timezone.utc)


def rt_item(n):
    return {"name": n, "description": "Опис товару достатньої довжини для картки сайту, понад п'ятдесят знаків, без зайвого.", "vendor": "RT", "vendor_code": "RT-1",
            "category": "Конструкторы", "images": ["https://plutustoys.com.ua/img/rt/rt-1-1.jpg"], "price": 480, "cost": 290, "stock": 7, "supplier": "royaltoys"}


(tmp / "s.json").write_text(json.dumps({"updated": now.isoformat(), "items": {"rt-9001": rt_item("Самолетик Максик Червоний"),
                                                                              "rt-9002": rt_item("Літачок інший, змінений постачальником")}}, ensure_ascii=False), encoding="utf-8")
(tmp / "m.json").write_text(json.dumps({"Конструкторы": "Конструктори"}, ensure_ascii=False), encoding="utf-8")
(tmp / "ov_e2e.json").write_text(json.dumps({"rt-9001": {"from": "Самолетик Максик Червоний", "to": "Літачок Максик Червоний"},
                                              "rt-9002": {"from": "Самолетик Максик Синій", "to": "Літачок Максик Синій"},
                                              "9003": {"from": "Toysi", "to": "Не має чіпати"}}, ensure_ascii=False), encoding="utf-8")
bs.SUPPLIER_RT_FILE, bs.RT_CATEGORY_MAP_FILE = str(tmp / "s.json"), str(tmp / "m.json")
bs.SUPPLIER_KV_FILE = str(tmp / "no_kv.json")
bs.CATEGORY_GROUPS_FILE = str(tmp / "none.json")
bs._RT_NAME_OVERRIDES_FILE = str(tmp / "ov_e2e.json")
toysi = {"9003": {"id": "9003", "name": "Toysi", "vendor": "T", "price": 100, "stock": 5, "category_name": "Машинки", "category_id": "10", "pictures": ["https://toysi.ua/p/enl_9003.jpg"],
                  "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": "https://toysi.ua/p/9003", "rt_cost": 10}}
bs.tp.fetch_toysi_catalog = lambda: dict(toysi)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
err = io.StringIO()
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
    bs.build()
idx = {x["id"]: x for x in json.loads((out / "index.json").read_text(encoding="utf-8"))}
chk("збірка: назва rt-9001 у index.json виправлена; rt-9002 (змінена постачальником) лишилась його; Toysi 9003 не зачеплено",
    idx.get("rt-9001", {}).get("n") == "Літачок Максик Червоний" and idx.get("rt-9002", {}).get("n") == "Літачок інший, змінений постачальником" and idx.get("9003", {}).get("n") == "Toysi")
pf = out / "product-rt-9001.html"
h = pf.read_text(encoding="utf-8") if pf.exists() else ""
chk("картка rt-9001: нова назва є, старої нема", "Літачок Максик Червоний" in h and "Самолетик" not in h)
chk("WARN у stderr збірки: розбіжність названо (rt-9002), відкинутий ключ «9003» теж",
    "rt-9002" in err.getvalue() and "назва постачальника змінилась" in err.getvalue() and "1 записів відкинуто" in err.getvalue())
print("\n" + ("ALL OK" if not F else f"FAIL: {len(F)}"))
sys.exit(1 if F else 0)
