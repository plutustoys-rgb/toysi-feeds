#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_site_sale_invented.py — режим «вигадана стара ціна» для «Знижки тижня» (власник 21.11.2026, варіант 2; ворота SITE_SALE_INVENTED_OLD=1, типово ВИМКНЕНО).
ІНВАРІАНТИ: ворота вимкнені → поведінка apply_sale/фідів/кандидатів як була (ціна == old, нова ціна знижена); ворота ввімкнені → ціна товару НЕ змінюється, перекреслена «стара» = ceil(ціна×100/(100−pct)),
pct — з пари (old,new) партії; акція застосовується, навіть коли ціна сайту ≠ old (ринкова −3); у фіди Google/Meta/Bing sale_price НЕ йде (load_feed_sale → {}); кандидати плану беруть реальну ціну pr, а не op;
priceValidUntil у JSON-LD не прив'язується до акції. `python test_site_sale_invented.py` → exit 0/1. Без мережі.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
for k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID", "SITE_BUILD_LIMIT", "SITE_SALE_NOW", "SITE_SALE_INVENTED_OLD"):
    os.environ.pop(k, None)
tmpd = Path(tempfile.mkdtemp(prefix="sale_inv_"))
os.environ["SITE_DIR"] = str(tmpd)
os.environ["SITE_PRICE_HISTORY_FILE"] = str(tmpd / "hist.json")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402
import site_sale as ss  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


KY = timezone(timedelta(hours=2))
sale = {"week": "2026-11-21", "pct": 15, "from": datetime(2026, 11, 21, tzinfo=KY), "until": datetime(2026, 11, 21, 23, 59, 59, tzinfo=KY),
        "items": {"1": (200, 170), "2": (100, 95), "3": (50, 43), "9": (100, 100)}, "until_by_id": {}}
COST = 100.0

# ── ворота вимкнені: стара поведінка
os.environ.pop("SITE_SALE_INVENTED_OLD", None)
chk("ворота вимкнені за замовчуванням", bs.sale_invented_on() is False and ss.invented_old_on() is False)
chk("вимкнено: ціна == old → акційна ціна 170, стара 200", bs.apply_sale("1", 200, sale, COST) == (170, 200))
chk("вимкнено: ціна сайту ≠ old (ринкова −3) → акції нема (як було)", bs.apply_sale("1", 190, sale, COST) == (190, None))

# ── ворота ввімкнені
os.environ["SITE_SALE_INVENTED_OLD"] = "1"
chk("ворота вмикаються лише значенням «1»", bs.sale_invented_on() is True and ss.invented_old_on() is True)
os.environ["SITE_SALE_INVENTED_OLD"] = "true"
chk("«true» не вмикає (лише «1»)", bs.sale_invented_on() is False)
os.environ["SITE_SALE_INVENTED_OLD"] = "1"
chk("ввімкнено: ціна 200 (== old) → ціна НЕ знижується, стара ≈ 200×100/85 = 235,3 → 235 (бейдж 15 %)", bs.apply_sale("1", 200, sale, COST) == (200, 235))
chk("ввімкнено: ціна 190 (≠ old, ринкова −3) → акція ЗАСТОСОВУЄТЬСЯ: ціна 190, стара ≈ 223,5 → 224", bs.apply_sale("1", 190, sale, COST) == (190, 224))
chk("ввімкнено: pct 5 (old 100 → new 95): ціна 100 → стара 105 (бейдж 5 %, а не 6 %)", bs.apply_sale("2", 100, sale, COST) == (100, 105))
chk("ввімкнено: pct береться з пари партії (50→43 = 14 %): ціна 80 → стара 93 (бейдж 14 %)", bs.apply_sale("3", 80, sale, COST) == (80, 93))
chk("ввімкнено: стара > ціни для звичайних цін; для дуже малих (< 20 ₴) акція не застосовується замість абсурдного бейджа", all(bs.apply_sale("1", p, sale, COST)[1] > p for p in (55, 199, 1999)) and all(bs.apply_sale("1", p, sale, COST)[1] is None for p in (1, 2, 3, 7)))
chk("ввімкнено: товару нема в партії → без змін", bs.apply_sale("777", 190, sale, COST) == (190, None))
chk("ввімкнено: пара без знижки (new == old) → без акції", bs.apply_sale("9", 100, sale, COST) == (100, None))
chk("ввімкнено: ціна 0 → без акції, без винятку", bs.apply_sale("1", 0, sale, COST) == (0, None))
chk("ввімкнено: сміття у ціні → без винятку, без акції", bs.apply_sale("1", "abc", sale, COST)[1] is None)
chk("ввімкнено: порожня партія (None) → як було", bs.apply_sale("1", 190, None, COST) == (190, None))
pr, op = bs.apply_sale("1", 190, sale, COST)
chk("ввімкнено: відсоток на бейджі збігається з pct товару (15 %)", "−15%" in bs.sale_badge_html({"old_price": op, "price": pr}))
chk("ввімкнено: відсоток на бейджі для pct 5 — 5 %", "−5%" in bs.sale_badge_html({"old_price": bs.apply_sale("2", 100, sale, COST)[1], "price": 100}))

bad = []
for pct in (5, 7, 10, 12, 15):
    old_, new_ = 1000, -(-1000 * (100 - pct) // 100)
    sl = dict(sale, items={"z": (old_, new_)})
    for price_ in list(range(1, 60)) + [77, 99, 150, 199, 250, 499, 999, 1999, 4999]:
        pr_, inv_ = bs.apply_sale("z", price_, sl, COST)
        shown = max(1, round(100 * (inv_ - pr_) / inv_)) if inv_ else None
        if pr_ != price_ or (inv_ is not None and (inv_ <= pr_ or abs(shown - pct) > 1 or (price_ >= 200 and shown != pct))) or (inv_ is None and price_ >= 30):
            bad.append((pct, price_, inv_, shown))
chk("ввімкнено: для pct 5/7/10/12/15 і цін 1…4999 бейдж == pct (з 200 ₴; нижче — ±1 п.п.), стара > ціни, ціна не змінена, від 30 ₴ акція завжди застосована (" + str(len(bad)) + " винятків)", not bad)
if bad:
    print("   винятки:", bad[:8])

# ── фіди: sale_price не віддаємо
wk = {"weeks": [{"week": "2026-11-21", "pct": 15, "from": "2030-01-01T00:00:00+02:00", "until": "2030-01-01T23:59:59+02:00", "items": {"1": {"old": 200, "new": 170, "pct": 15}}}]}
sf = tmpd / "sale_week.json"
sf.write_text(json.dumps(wk), encoding="utf-8")
now_f = datetime(2029, 12, 31, 12, 0, tzinfo=KY)
os.environ.pop("SITE_SALE_INVENTED_OLD", None)
chk("ворота вимкнені: load_feed_sale віддає акцію для фідів (як було)", ss.load_feed_sale(now_f, sf).get("1", {}).get("new") == 170)
os.environ["SITE_SALE_INVENTED_OLD"] = "1"
chk("ввімкнено: load_feed_sale → {} (Google/Meta/Bing: лише реальна ціна, без sale_price — умова SEO)", ss.load_feed_sale(now_f, sf) == {})

# ── кандидати плану: реальна ціна pr, а не op
import competitor_pricing as cp  # noqa: E402
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=KY)
cat = {"1001": {"price": 220, "stock": 10, "name": "Іграшка", "category_name": "Конструктори", "category_id": "1", "vendor": "Brand", "id": "1001"}}
idx = [{"id": "1001", "pr": 400, "op": 470, "n": "т", "p": "u"}]
ss.STABLE_DAYS = 0
os.environ.pop("SITE_SALE_INVENTED_OLD", None)
c_off = ss.candidates(idx, cat, {}, NOW)
os.environ["SITE_SALE_INVENTED_OLD"] = "1"
c_on = ss.candidates(idx, cat, {}, NOW)
chk("кандидати: ворота вимкнені — база = op (470, звичайна ціна товару в знижці, як було)", len(c_off) == 1 and c_off[0]["old"] == 470)
chk("кандидати: ввімкнено — база = pr (400, реальна ціна), op розрахункова не береться", len(c_on) == 1 and c_on[0]["old"] == 400)

# ── JSON-LD: priceValidUntil не прив'язується до акції у вигаданому режимі
bs.SALE_ACTIVE = dict(sale, until_by_id={"1": datetime(2026, 11, 21, 23, 59, 59, tzinfo=KY)})
p = {"id": "1", "name": "Іграшка", "old_price": 224, "price": 190, "stock": 5, "vendor": "Brand", "vendor_code": "", "barcode": "", "category": "Конструктори", "photo": "https://plutustoys.com.ua/img/t/1.jpg", "desc": "Опис"}
os.environ.pop("SITE_SALE_INVENTED_OLD", None)
v_off = bs.offer_extras(p)["priceValidUntil"]
os.environ["SITE_SALE_INVENTED_OLD"] = "1"
v_on = bs.offer_extras(p)["priceValidUntil"]
chk("JSON-LD: вимкнено — priceValidUntil = кінець акції (21.11), як було", v_off == "2026-11-21")
chk("JSON-LD: ввімкнено — priceValidUntil = звичайна дата (не кінець вигаданої акції)", v_on == bs.price_valid_until().isoformat() and v_on != "2026-11-21")
bs.SALE_ACTIVE = None
os.environ.pop("SITE_SALE_INVENTED_OLD", None)

# ── маркер режиму для фідів (аудит #1931, знахідка 1): фід безпечний, навіть коли env у процесі фіда не виставлено
mk = tmpd / "sale_mode_marker.json"
bs.SALE_MODE_MARKER = str(mk)
ss.SALE_MODE_MARKER = mk
os.environ.pop("SITE_SALE_INVENTED_OLD", None)
bs._write_sale_mode_marker()
chk("збірка при вимкненій брамі пише маркер {invented:false}", json.loads(mk.read_text(encoding="utf-8")) == {"invented": False})
chk("маркер false + env нема → фід віддає акцію (як було)", ss.load_feed_sale(now_f, sf).get("1", {}).get("new") == 170)
os.environ["SITE_SALE_INVENTED_OLD"] = "1"
bs._write_sale_mode_marker()
chk("збірка при ввімкненій брамі пише маркер {invented:true}", json.loads(mk.read_text(encoding="utf-8")) == {"invented": True})
os.environ.pop("SITE_SALE_INVENTED_OLD", None)          # процес фіда БЕЗ env, але збірка сайту була в режимі «вигаданої»
chk("маркер true + env у процесі фіда НЕ виставлено → фід БЕЗ sale_price (захист від розсинхрону env)", ss.load_feed_sale(now_f, sf) == {})
mk.write_text("{не json", encoding="utf-8")
chk("зіпсований маркер → діє env (вимкнено → акція в фіді як було), без винятку", ss.load_feed_sale(now_f, sf).get("1", {}).get("new") == 170)
mk.unlink()
chk("нема маркера → діє env, без винятку", ss.load_feed_sale(now_f, sf).get("1", {}).get("new") == 170)
chk("маркер не в публічній теці site/ (SITE_DIR)", not str(bs.SALE_MODE_MARKER).startswith(str(tmpd / "site")) and os.path.basename(os.path.dirname(os.path.abspath(bs.__file__))) == "site")

print()
if F:
    print(f"FAIL: {len(F)}")
sys.exit(1 if F else 0)
