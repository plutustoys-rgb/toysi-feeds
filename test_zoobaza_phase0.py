# -*- coding: utf-8 -*-
"""ZooBaza Фаза 0: окремий Prom-фід (префікс zb-, ціни, fail-closed) і claim замовлень (ізоляція від Toysi-роутера)."""
import os, sys, tempfile, json
from pathlib import Path
import xml.etree.ElementTree as ET
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_tmp = tempfile.mkdtemp()
os.environ["ORDERS_DB_PATH"] = os.path.join(_tmp, "orders.db")
os.environ["ZB_STATE_FILE"] = os.path.join(_tmp, "zoobaza_state.json")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["ZOOBAZA_FEED_TO_OPT"] = "1.4"   # фікстура: ціни з фіду 04.10 (×1.4); живий фід з 07.10 = ×1.5
for k in ("ZB_MIN_MARGIN", "ZB_STOCK_QTY", "ZB_PILOT_SKUS_FILE", "ZB_FEED_OUT"):
    os.environ.pop(k, None)

import zoobaza_parser as zp
import zoobaza_prom_feed as pf
import zoobaza_intake as zi
import orders_db

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

YML = """<?xml version="1.0" encoding="UTF-8"?><yml_catalog><shop><categories>
<category id="1059">Сумки та переноски</category><category id="1057">Лежаки</category><category id="1056">Будки</category><category id="1083">Корм</category>
</categories><offers>
<offer id="3487" available="true"><price>1512</price><quantity_in_stock>10</quantity_in_stock><categoryId>1059</categoryId><picture>https://x/1.jpg</picture><picture>https://x/2.jpg</picture><vendorCode>303625</vendorCode><vendor>Zoobaza</vendor><name>Сумка-переноска Спорт - чорний</name><description>Опис</description><param name="Цвет">чорний</param></offer>
<offer id="4874" available="true"><price>1067</price><quantity_in_stock>10</quantity_in_stock><categoryId>1056</categoryId><picture>https://x/3.jpg</picture><vendorCode>4823118708042</vendorCode><vendor>Zoobaza</vendor><name>Будка Меджік</name><description></description></offer>
<offer id="5000" available="false"><price>900</price><quantity_in_stock>0</quantity_in_stock><categoryId>1057</categoryId><picture>https://x/4.jpg</picture><vendorCode>5000</vendorCode><name>Лежак недоступний</name></offer>
<offer id="6000" available="true"><price>500</price><quantity_in_stock>10</quantity_in_stock><categoryId>1083</categoryId><picture>https://x/5.jpg</picture><vendorCode>6000</vendorCode><name>Корм</name></offer>
<offer id="7000" available="true"><price>700</price><quantity_in_stock>10</quantity_in_stock><categoryId>1057</categoryId><vendorCode>7000</vendorCode><name>Лежак без фото</name></offer>
</offers></shop></yml_catalog>""".encode("utf-8")
cat = zp.parse_zoobaza_xml(YML)

# ── ціна ──
chk("ціна: РРЦ опт×1,5 (опт 1080 → 1620), коли вища за підлогу маржі", pf.compute_price(1080.0) == 1620)
floor = 100 / (1 - pf.PROM_COMMISSION_PET - pf.PAYMENT_COMMISSION - 0.15)
chk("ціна: підлога чистої маржі 15% після комісії 8% і еквайрингу, коли вона вища за РРЦ", pf.compute_price(100.0, margin=0.40) > 150 and pf.compute_price(100.0) == 150)
chk("ціна цілі гривні (ceil)", isinstance(pf.compute_price(333.33), int) and pf.compute_price(333.33) >= 333.33 * 1.5)

# ── відбір ──
offers, skipped = pf.select_offers(cat, {"3487", "4874", "5000", "6000", "7000", "9999"})
ids = {o["zb_id"] for o in offers}
chk("відбір: лише придатні (сумка, будка) з префіксом zb-", ids == {"zb-3487", "zb-4874"})
chk("відсів із причинами: недоступно, корм (поза Prom-мапою), без фото, нема в каталозі",
    skipped.get("постачальник: недоступно") == ["5000"] and skipped.get("категорія поза білим списком Prom-мапи") == ["6000"]
    and skipped.get("без фото") == ["7000"] and skipped.get("нема в каталозі постачальника") == ["9999"])
chk("Prom-категорії: переноски 181201, будки 181203", {o["zb_id"]: o["prom_category"] for o in offers} == {"zb-3487": "181201", "zb-4874": "181203"})

# ── XML ──
root = pf.build_xml(offers)
offs = root.find("shop").find("offers").findall("offer")
o1 = [o for o in offs if o.get("id") == "zb-3487"][0]
chk("XML: id і vendorCode з префіксом zb-, categoryId = Prom-id (не «загальне»)",
    o1.findtext("vendorCode") == "zb-303625" and o1.findtext("categoryId") == "181201")
chk("XML: ціна з крапкою і двома нулями, цілі гривні; кількість = ZB_STOCK_QTY (1), не прапорець 10",
    o1.findtext("price") == "1620.00" and o1.findtext("quantity_in_stock") == "1")
chk("XML: 2 фото, колір як param, name_ua", len(o1.findall("picture")) == 2 and o1.find("param").get("name") == "Колір" and o1.findtext("name_ua"))
chk("XML: порожній опис → назва (Prom вимагає description)", [o for o in offs if o.get("id") == "zb-4874"][0].findtext("description") == "Будка Меджік")

# ── запуск: fail-closed і атомарність ──
out = Path(_tmp) / "feed" / "z.xml"
chk("fail-closed: порожній білий список → exit 2, файл не створено", pf.run(out=out, catalog=cat, whitelist=set()) == 2 and not out.exists())
chk("fail-closed: жодного придатного → exit 2", pf.run(out=out, catalog=cat, whitelist={"5000", "6000"}) == 2 and not out.exists())
chk("успіх: exit 0, файл є і парситься", pf.run(out=out, catalog=cat, whitelist={"3487", "4874"}) == 0 and ET.parse(out).getroot().tag == "yml_catalog")
before = out.read_bytes()
chk("збій: стара версія не чіпається", pf.run(out=out, catalog=cat, whitelist={"5000"}) == 2 and out.read_bytes() == before)
chk("dry-run: файл не перезаписано", pf.run(out=Path(_tmp) / "n.xml", catalog=cat, whitelist={"3487"}, dry_run=True) == 0 and not (Path(_tmp) / "n.xml").exists())
wl = Path(_tmp) / "wl.txt"; wl.write_text("# пілот\n3487\nzb-4874  # будка\n\n", encoding="utf-8")
chk("білий список: коментарі/порожні/зайвий префікс zb- відкидаються", pf.load_whitelist(wl) == {"3487", "4874"})
chk("ізоляція: модуль фіду не імпортує Toysi-модулів",
    not any(m in sys.modules for m in ("parser", "competitor_pricing", "generate_prom_feed", "order_router")))

# ── intake ──
chk("is_zb_code: регістр/пробіли", zi.is_zb_code(" ZB-123 ") and not zi.is_zb_code("123") and not zi.is_zb_code(None))
chk("classify: zoobaza / mixed / other / empty",
    zi.classify_items([{"toysi_code": "zb-1"}, {"toysi_code": "zb-2"}]) == "zoobaza"
    and zi.classify_items([{"toysi_code": "zb-1"}, {"toysi_code": "305457"}]) == "mixed"
    and zi.classify_items([{"toysi_code": "305457"}]) == "other" and zi.classify_items([]) == "empty")

orders_db.init_db()
def put(conn, oid, codes, **kw):
    orders_db.insert_order(conn, {"order_id": oid, "platform": kw.pop("platform", "prom"), "payment_method": "cod",
                                  "items": [{"toysi_code": c, "name": "x", "qty": 1, "price": 100} for c in codes], **kw})
with orders_db.get_connection() as conn:
    put(conn, "1", ["zb-3487"]); put(conn, "2", ["zb-3487", "305457"]); put(conn, "3", ["305457"])
    put(conn, "4", ["zb-4874"], status="prom_cancelled_before_forward"); put(conn, "5", ["zb-4874"], forwarded_to_toysi_at="2026-10-01T10:00:00")
    put(conn, "6", ["zb-4874"], platform="rozetka")
    r = zi.claim_new(conn, notify=False)
    st = {row["order_id"]: row["status"] for row in conn.execute("SELECT order_id, status FROM orders")}
    chk("claim: чисті ZooBaza → zoobaza_hold (Prom і Rozetka)", st["1"] == "zoobaza_hold" and st["6"] == "zoobaza_hold" and sorted(r["claimed"]) == ["prom_1", "rozetka_6"])
    chk("claim: змішаний кошик → zoobaza_mixed_hold (не Toysi і не ZooBaza)", st["2"] == "zoobaza_mixed_hold" and r["mixed"] == ["prom_2"])
    chk("claim: Toysi-замовлення не чіпається", st["3"] == "new")
    chk("claim: скасоване й уже переслане Toysi НЕ воскрешається", st["4"] == "prom_cancelled_before_forward" and st["5"] == "new")
    r2 = zi.claim_new(conn, notify=False)
    chk("claim ідемпотентний: повторно нічого", r2 == {"claimed": [], "mixed": []})
    state = zi.load_state()
    chk("власний стан: запис на кожне, без ПІБ/телефону", set(state) == {"prom_1", "prom_2", "rozetka_6"}
        and not any(k in json.dumps(state, ensure_ascii=False) for k in ("customer", "phone")))
    # алерт один раз на замовлення
    sent = []
    zi._alert = lambda t: sent.append(t)
    put(conn, "7", ["zb-3487"]); put(conn, "8", ["zb-3487", "1"])
    zi.claim_new(conn); zi.claim_new(conn)
    chk("алерт: рівно по одному на замовлення, змішаний — з позначкою 🔴", len(sent) == 2 and any("ЗМІШАНИЙ" in t for t in sent))
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ ZooBaza Фаза 0 — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
