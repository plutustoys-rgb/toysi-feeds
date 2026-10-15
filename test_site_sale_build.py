#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_sale_build.py — 14.10.2026 (аудит «Знижки тижня»): ІНТЕГРАЦІЙНИЙ тест — повний build() на синтетичному каталозі з site/sale_week.json. Зв'язка файл → apply_sale → p["price"] → index.json / сторінки / JSON-LD / /aktsii.html / sitemap
і прибирання застарілої /aktsii.html поза вікном. Ловить те, чого не бачать юніт-перевірки (напр. build() не викликає apply_sale / застосовує до OOS).
Самодостатній: синтетичний каталог у тимчасовій теці, мережа не потрібна, Telegram-алерти підмінені."""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
for k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID", "SITE_BUILD_LIMIT", "SITE_SALE_NOW"):
    os.environ.pop(k, None)
os.environ["SITE_PICK_DATE"] = "2026-10-21"
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def item(pid, name, category, stock=5, price=300.0):
    return {"id": str(pid), "name": name, "vendor": "Test", "price": price, "stock": stock, "category_name": category, "category_id": "10",
            "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "description": "Опис товару достатньої довжини для картки й метаопису.", "barcode": ""}


catalog = {}
for i in range(1, 41):
    catalog[str(i)] = item(i, f"Іграшка номер {i}", f"Кат{i % 4}", stock=3 + i % 4, price=400.0 + i * 7)
catalog["501"] = item(501, "Закінчилась акційна", "Кат0", stock=0, price=600.0)
KY = timezone(timedelta(hours=3))
NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=KY)
START, END = datetime(2026, 10, 19, 0, 0, 0, tzinfo=KY), datetime(2026, 10, 25, 23, 59, 59, tzinfo=KY)
price = {pid: bs.price_of(it) for pid, it in catalog.items()}
cost = {pid: bs.cp.real_toysi_cost(it) for pid, it in catalog.items()}
com = bs.cp.PAYMENT_COMMISSION.get("site", 0.0)


def margin_new(pid):
    new = -(-price[pid] * 85 // 100)
    return new - cost[pid] - new * com - bs.SITE_FULFILL_PER_ORDER


ok_ids = [p for p in catalog if p != "501" and margin_new(p) >= 25][:4]
low_ids = [p for p in catalog if p != "501" and margin_new(p) < 25][:1]
assert len(ok_ids) == 4, ("fixture: замало товарів із маржею ≥25", ok_ids)
changed = "40" if "40" not in ok_ids else "39"
wk_items = {pid: {"old": price[pid], "new": -(-price[pid] * 85 // 100)} for pid in ok_ids}
wk_items[changed] = {"old": price[changed] + 1, "new": -(-(price[changed] + 1) * 85 // 100)}   # ціна змінилась між планом і збіркою → не застосовується
if low_ids:
    wk_items[low_ids[0]] = {"old": price[low_ids[0]], "new": -(-price[low_ids[0]] * 85 // 100)}   # внесок після знижки < 25 → випадає
wk_items["501"] = {"old": price["501"], "new": -(-price["501"] * 85 // 100)}                      # OOS → без знижки
sale = {"weeks": [{"week": "2026-W43", "pct": 15, "from": START.isoformat(), "until": END.isoformat(), "created_at": "2026-10-18T18:00:00+03:00", "items": wk_items}]}

out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
sale_path = out.parent / f"sale_week_{out.name}.json"
sale_path.write_text(json.dumps(sale, ensure_ascii=False), encoding="utf-8")
bs.SALE_FILE = str(sale_path)
bs.tp.fetch_toysi_catalog = lambda: catalog
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
import telegram_notify  # noqa: E402
_alerts = []
telegram_notify.send_throttled_alert = lambda *a, **k: (_alerts.append((a, k)) or True)
bs.LIMIT = 0
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None
os.environ["SITE_SALE_NOW"] = NOW.isoformat()
(out / "aktsii.html").write_text("stale aktsii", encoding="utf-8")
bs.build()
idx = {p["id"]: p for p in json.loads((out / "index.json").read_text(encoding="utf-8"))}

for pid in ok_ids:
    e = idx[pid]
    chk(f"товар {pid}: у index.json pr = акційна ({wk_items[pid]['new']}), op = стара ({price[pid]})", e["pr"] == wk_items[pid]["new"] and e.get("op") == price[pid])
chk("товар зі зміненою ціною (≠ old) — без знижки: pr = звичайна ціна, op нема", idx[changed]["pr"] == price[changed] and "op" not in idx[changed])
if low_ids:
    chk(f"товар {low_ids[0]} із внеском після знижки < 25 ₴ — випав з акції (ціна звичайна)", idx[low_ids[0]]["pr"] == price[low_ids[0]] and "op" not in idx[low_ids[0]])
chk("OOS-товар (501) у index.json нема; акційної ціни не отримав", "501" not in idx and 'class="price sale-price"' not in (out / "product-501.html").read_text(encoding="utf-8"))
chk("усі інші товари — pr = звичайна ціна сайту (price_of), без op", all(idx[p]["pr"] == price[p] and "op" not in idx[p] for p in idx if p not in ok_ids))
pid = ok_ids[0]
ph = (out / f"product-{pid}.html").read_text(encoding="utf-8")
chk(f"сторінка товару {pid}: ціна = акційна, стара перекреслена, JSON-LD price і priceValidUntil = кінець акції, data-add несе акційну ціну", f'class="price sale-price">{wk_items[pid]["new"]} ₴' in ph and f'<s class="pr-old">{price[pid]} ₴</s>' in ph and f'"price": "{wk_items[pid]["new"]}"' in ph and '"priceValidUntil": "2026-10-25"' in ph and f"&quot;price&quot;: {wk_items[pid]['new']}" in ph)
ak = (out / "aktsii.html").read_text(encoding="utf-8")
chk(f"/aktsii.html перезаписано свіжою збіркою ({len(ok_ids)} карток), без «stale»", "stale aktsii" not in ak and ak.count('class="sale-badge">−15%</span>') == len(ok_ids))
sm = chr(10).join(f.read_text(encoding="utf-8") for f in sorted(out.glob("sitemap-*.xml")))   # SEO 15.10: sitemap.xml — індекс, URL у сегментах sitemap-*.xml
chk("sitemap містить /aktsii.html", "/aktsii.html</loc>" in sm)
home = (out / "index.html").read_text(encoding="utf-8")
chk("головна: червона кнопка «Акції» (btn-sale) → aktsii.html", 'class="btn btn-sale" href="aktsii.html"' in home)
chk("жодного алерту про мало застосованих позицій (застосовано 4 ≥ 3)", not _alerts)

# ── поза вікном: файл лишився, але акції нема; стара /aktsii.html прибирається; ціни звичайні ──
out2 = Path(tempfile.mkdtemp())
bs.OUT = str(out2)
os.environ["SITE_SALE_NOW"] = (END + timedelta(seconds=1)).isoformat()
(out2 / "aktsii.html").write_text("stale aktsii", encoding="utf-8")
bs.build()
idx2 = {p["id"]: p for p in json.loads((out2 / "index.json").read_text(encoding="utf-8"))}
chk("після until: pr = звичайні ціни, op нема ні в кого", all(idx2[p]["pr"] == price[p] and "op" not in idx2[p] for p in idx2))
chk("після until: /aktsii.html видалено (застаріла), у sitemap її нема, на головній кнопка «Усі іграшки»", not (out2 / "aktsii.html").exists() and "/aktsii.html</loc>" not in (out2 / "sitemap.xml").read_text(encoding="utf-8") and 'class="btn btn-sale"' not in (out2 / "index.html").read_text(encoding="utf-8"))

# ── мало застосованих позицій при діючій акції — алерт ──
out3 = Path(tempfile.mkdtemp())
bs.OUT = str(out3)
sale["weeks"][0]["items"] = {ok_ids[0]: wk_items[ok_ids[0]], changed: wk_items[changed]}
sale_path.write_text(json.dumps(sale, ensure_ascii=False), encoding="utf-8")
os.environ["SITE_SALE_NOW"] = NOW.isoformat()
bs.build()
chk("акція діє, а застосовано 1 (< 3) — є Telegram-алерт «site_sale_few_applied»", any(a[0] and a[0][0] == "site_sale_few_applied" for a in _alerts))
os.environ.pop("SITE_SALE_NOW", None)
os.environ.pop("SITE_PICK_DATE", None)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
