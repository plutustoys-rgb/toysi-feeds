#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rt_prom_repricer.py — конкурентна ціна rt- (rt_reprice.py; рішення власника 22.11.2026). Офлайн: жодних викликів Prom/конкурентів/Telegram — усе підроблено.
Інваріанти: (1) брама PROM_RT_REPRICE вимкнена → фаза не працює, файл стану не створюється, фід rt- БІТОВО той самий (навіть якщо файл стану існує); (2) пул = лише rt-id, що могли б бути в емісії (≤ rt_hard_max),
не весь каталог RT; (3) ціна rt- у фіді = рішення decide_price_for_platform за станом (undercut конкурента −PRICE_STEP), не нижча за floor, мінімум 60 ₴; floor-гард і стеля застарілого — дзеркало generate_prom_feed._build_xml
(паритет перевіряється на справжньому _build_xml); (4) неконкурентні (delist) випадають з емісії, зникають як skip_noncompetitive і віддаються заглушкою як осиротілі; (5) dry-run нічого не пише, apply пише лише
rt- ключі в ОКРЕМИЙ файл; Toysi-стан/Toysi-функції не зачеплені; (6) rt- ключі не ламають місця, що ітерують стан (int()/startswith) ; (7) breaker/кап зняттів/дефолтна комісія/бюджет часу; (8) ротація партії."""
import itertools
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
for _k in ("PROM_RT_IN_FEED", "PROM_RT_MAX", "PROM_RT_REPRICE", "PROM_RT_REPRICE_BATCH", "PROM_RT_REPRICE_BUDGET_S"):
    os.environ.pop(_k, None)
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
sys.path.insert(0, str(Path(__file__).parent))
import competitor_pricing as cp  # noqa: E402
import generate_prom_feed as gp  # noqa: E402
import prom_competitor_pricer as pcp  # noqa: E402
import rt_prom  # noqa: E402
import rt_reprice  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


tmp = Path(tempfile.mkdtemp())
_cnt = itertools.count()
rt_prom.SEL_FILE = tmp / "sel.json"
rt_prom.SUPPLIER_FILE = tmp / "supplier_rt.json"
rt_prom.DUP_FILE = tmp / "rt_dup_map.json"
rt_prom.MIN_CATALOG_N = 10


def fresh_files():
    rt_prom.LISTED_FILE = tmp / f"listed_{next(_cnt)}.json"
    rt_reprice.STATE_FILE = tmp / f"rtstate_{next(_cnt)}.json"


fresh_files()
# Toysi-стан репрайсера НЕ повинен змінюватись жодною rt-функцією: спрямовуємо його у temp і стежимо, що файл не створюється
cp.PROM_PRICE_STATE_FILE = tmp / "toysi_price_state.json"

DESC = "Чудова іграшка для дітей, розвиває дрібну моторику та уяву. Безпечні матеріали, яскраві кольори."


def rt_item(name, cost=120, stock=6, **kw):
    base = {"name": name, "description": DESC, "vendor": "РТвендор", "vendor_code": "RTX-1", "category": "RT-кат", "images": ["https://plutustoys.com.ua/img/rt/rt-1-1.jpg"], "price": str(int(cost * 1.5)), "cost": float(cost),
            "stock": stock, "dims_cm": [20.0, 10.0, 5.0], "weight_kg": 0.5}
    base.update(kw)
    return base


def si(prom_id="2606", tcat="Тест-Машини", **kw):
    return dict({"prom_id": prom_id, "prom_name": "", "toysi_cat": tcat, "cls": "UNIQUE", "src": "rule", "twins": []}, **kw)


NOW = datetime.now()
sup_items = {
    "rt-1": rt_item("Перша іграшка Модель 1"), "rt-2": rt_item("Друга іграшка Модель 2"), "rt-3": rt_item("Третя іграшка Модель 3"), "rt-4": rt_item("Четверта іграшка Модель 4"),
    "rt-5": rt_item("П'ята іграшка Модель 5"), "rt-6": rt_item("Шоста іграшка Модель 6"), "rt-X_7-a": rt_item("Сьома іграшка Модель 7"),
    "rt-8": rt_item("Восьма іграшка Модель 8", cost=20),            # ціна Prom < 60 → «cheap», в пул не потрапляє
    "rt-9": rt_item("Дев'ята іграшка Модель 9", stock=1),            # залишок < 3 → не в пулі
    "rt-10": rt_item("Десята іграшка Модель 10"),                    # DUP-клас → не в пулі
}
sel_items = {k: si() for k in sup_items}
sel_items["rt-6"] = si("999999", "Невідома категорія")              # дефолтна (непідтверджена) комісія
sel_items["rt-10"] = si(cls="DUP")
selection = {"built_at": NOW.isoformat(timespec="seconds"), "rt_n": len(sup_items), "valid_ids": ["2606", "999999"], "items": sel_items}
rt_prom._atomic_write(rt_prom.SEL_FILE, selection)
rt_prom._atomic_write(rt_prom.SUPPLIER_FILE, {"updated": NOW.isoformat(timespec="seconds"), "items": sup_items})
catalog = {str(1000 + i): {"id": str(1000 + i), "name": f"Toysi {i}", "stock": 5, "price": "100", "category_name": "Тест-Машини", "category_id": "1"} for i in range(12)}
live = {k: dict(v) for k, v in catalog.items()}
os.environ.pop("PROM_RT_IN_FEED", None)


# ───────────────────────── (1) брама ─────────────────────────
chk("gate_on: за замовчуванням вимкнено; PROM_RT_REPRICE=1 вмикає", not rt_reprice.gate_on() and (os.environ.__setitem__("PROM_RT_REPRICE", "1") or rt_reprice.gate_on()) and (os.environ.pop("PROM_RT_REPRICE") or True))
chk("run_phase без брами: skipped=gate_off, файл стану НЕ створено", rt_reprice.run_phase(catalog, apply=True) == {"skipped": "gate_off"} and not rt_reprice.STATE_FILE.exists())
pcp._RT_CTX.clear()
pcp._RT_CTX.update({"catalog": catalog, "apply": True})
pcp._run_rt_reprice_phase()
chk("prom_competitor_pricer._run_rt_reprice_phase без брами — no-op (файл стану не створено)", not rt_reprice.STATE_FILE.exists())
pcp._RT_CTX.clear()

# ───────────────────────── (2) пул ─────────────────────────
pool = rt_prom.repricer_pool(live, selection, sup_items)
print("  pool:", sorted(pool))
chk("пул: лише ті, що могли б бути в емісії (rt-1…rt-6, rt-X_7-a); DUP, залишок<3 і «cheap» (ціна <60) — ні; rt-id з буквами/підкресленням не ламають", set(pool) == {"rt-1", "rt-2", "rt-3", "rt-4", "rt-5", "rt-6", "rt-X_7-a"})
p1 = pool["rt-1"]
chk("пул: cost — з supplier_rt (120.0), tcat — категорія Toysi-сусіда, pid_int=2606 (int від Prom-id, НЕ від rt-ключа), назва без службових символів, фото є",
    p1["cost"] == 120.0 and p1["tcat"] == "Тест-Машини" and p1["pid_int"] == 2606 and p1["name"] == "Перша іграшка Модель 1" and p1["pictures"])
rt_prom.RT_DEFAULT_MAX = 3
chk("пул обмежено rt_hard_max() (весь каталог RT не скануємо): ліміт 3 → 3 rt-id", len(rt_prom.repricer_pool(live, selection, sup_items)) == 3)
chk("repricer_pool(max_items=2) → 2; ліміт не може перевищити rt_hard_max()", len(rt_prom.repricer_pool(live, selection, sup_items, max_items=2)) == 2 and len(rt_prom.repricer_pool(live, selection, sup_items, max_items=99)) == 3)
rt_prom.RT_DEFAULT_MAX = 1500
chk("пул: порядок — спершу колись емітовані (журнал), далі решта", list(rt_prom.repricer_pool(live, selection, sup_items))[0] == "rt-1")
rt_prom._atomic_write(rt_prom.LISTED_FILE, {"v": 1, "listed": {"rt-X_7-a": {"name": "x", "price": 210.0}}})
chk("пул: колись емітований rt-X_7-a іде першим", list(rt_prom.repricer_pool(live, selection, sup_items))[0] == "rt-X_7-a")
fresh_files()

# ───────────────────────── (3) ціна у фіді за станом ─────────────────────────
COST = 120.0
default_p = cp.decide_price_for_platform(COST, None, "prom", "Тест-Машини", prom_category_id=2606)["price"]
chk("база: ціна «без конкурента» = cost×1.75 = 210", abs(default_p - 210.0) < 0.01)


def ctx(entry=None, sid="rt-1", now=None):
    return {"items": ({sid: entry} if entry is not None else {}), "now": now or NOW}


def fp(entry, **kw):
    return rt_reprice.feed_price("rt-1", COST, "Тест-Машини", 2606, default_p, ctx(entry, now=kw.get("now")))


dec190 = cp.decide_price_for_platform(COST, 190.0, "prom", "Тест-Машини", prom_category_id=2606)
chk("конкурент 190 → undercut: ціна = 187 (=190−PRICE_STEP), категорія undercut", dec190["category"] == "undercut" and abs(dec190["price"] - 187.0) < 0.01)
e_fresh = {"price": dec190["price"], "timestamp": (NOW - timedelta(hours=2)).isoformat(), "action": "adjust", "competitor_price": 190.0, "competitor_key": "buybox", "category": "undercut"}
chk("фід: свіжий конкурент → ціна = рішення decide_price_for_platform (187), нижче дефолту 210", fp(e_fresh) == (187.0, ""))
chk("фід: нема запису → ціна «без конкурента» (як було)", fp(None) == (default_p, ""))
chk("фід: запис старший 45 діб → дефолт; битий timestamp/ціна → дефолт", fp(dict(e_fresh, timestamp=(NOW - timedelta(days=46)).isoformat())) == (default_p, "") and fp(dict(e_fresh, timestamp="xx")) == (default_p, "")
    and fp(dict(e_fresh, price="abc")) == (default_p, "") and fp(dict(e_fresh, price=-5)) == (default_p, ""))
# застарілий (>30 год) конкурент: ціна не вище дефолту; floor — division-floor
e_old = dict(e_fresh, timestamp=(NOW - timedelta(days=5)).isoformat(), price=400.0)
chk("фід: застарілий конкурент (5 діб) і ціна 400 → стеля дефолтом 210 (cap_stale_prom_override)", fp(e_old) == (default_p, ""))
# floor-гард: запис нижче floor (зросла собівартість) піднімається до floor
low = dict(e_fresh, price=10.0)
price_low, _ = fp(low)
commission = cp.compute_total_commission("prom", "Тест-Машини", price_low, prom_category_id=2606)
min_safe = round(cp.prom_effective_cost(COST) / (1 - commission), 2)
chk(f"фід: запис з ціною 10 ₴ (нижче floor) піднімається до floor ≥ min_safe ({price_low} ≥ {min_safe}); net-виручка ≥ собівартість", price_low >= min_safe - 0.01 and price_low * (1 - commission) >= COST)
# паритет із справжнім generate_prom_feed._build_xml (Toysi override-гілка)
_orig_rtc = gp.real_toysi_cost
gp.real_toysi_cost = lambda item: float(item["price"])
try:
    def toysi_price(cost, override, comp):
        it = {"id": "5001", "name": "Паритет тест", "stock": 5, "price": str(cost), "category_id": "1", "category_name": "Тест-Машини", "vendor": "X", "pictures": ["https://a/b.jpg"], "description": "d", "vendor_code": "5001"}
        root, _st = gp._build_xml({"5001": it}, prom_category_cache={"5001": {"category_id": 2606}}, price_overrides={"5001": override}, competitor_prices=({"5001": comp} if comp else None))
        return float(root.find("shop/offers/offer/price").text)

    parity = []
    for override, comp, age_h in ((187.0, 190.0, 2), (10.0, 190.0, 2), (400.0, None, 100), (150.0, None, 100), (10.0, None, 100), (187.0, 190.0, 40)):
        has_fresh = comp is not None and age_h <= 30
        entry = {"price": override, "timestamp": (NOW - timedelta(hours=age_h)).isoformat(), "action": "adjust", "competitor_price": comp}
        parity.append((rt_reprice.feed_price("rt-1", COST, "Тест-Машини", 2606, default_p, ctx(entry))[0], toysi_price(COST, override, comp if has_fresh else None)))
    print("  паритет rt/Toysi:", parity)
    chk("ПАРИТЕТ: feed_price(rt) = ціна, яку generate_prom_feed._build_xml дає Toysi для тих самих cost/override/конкурента (6 сценаріїв: свіжий, нижче floor, стеля застарілого, застарілий конкурент)", all(abs(a - b) < 0.011 for a, b in parity))
finally:
    gp.real_toysi_cost = _orig_rtc
# неконкурентний
e_del = dict(e_fresh, action="delist", price=210.0)
chk("фід: delist-запис (свіжий) → noncompetitive (rt- не емітується); старший 7 діб → знов у ціні (страховка TTL)", fp(e_del) == (None, "noncompetitive") and fp(dict(e_del, timestamp=(NOW - timedelta(days=8)).isoformat()))[1] == "")

# ───────────────────────── build_offer_fields / append_rt_offers ─────────────────────────
SI = si()
f0, w0 = rt_prom.build_offer_fields("rt-1", sup_items["rt-1"], SI, live, {})
chk("build_offer_fields без reprice — як раніше (ціна 210)", f0 is not None and f0["price"] == default_p)
f1, w1 = rt_prom.build_offer_fields("rt-1", sup_items["rt-1"], SI, live, {}, reprice=ctx(e_fresh))
chk("build_offer_fields з reprice: ціна 187 (конкурентна), profit перерахований від нової ціни, ціна ≥ 60", f1 is not None and f1["price"] == 187.0 and f1["profit"] < f0["profit"] and f1["price"] >= rt_prom.MIN_PROM_PRICE)
chk("build_offer_fields з reprice і delist-записом → (None, 'noncompetitive')", rt_prom.build_offer_fields("rt-1", sup_items["rt-1"], SI, live, {}, reprice=ctx(e_del)) == (None, "noncompetitive"))
dec36 = cp.decide_price_for_platform(36.0, 58.0, "prom", "Тест-Машини", prom_category_id=2606)
d36 = cp.decide_price_for_platform(36.0, None, "prom", "Тест-Машини", prom_category_id=2606)["price"]
e36 = {"price": dec36["price"], "timestamp": (NOW - timedelta(hours=1)).isoformat(), "action": "adjust", "competitor_price": 58.0}
print(f"  cost 36: без конкурента {d36}, з конкурентом 58 → {dec36['price']}")
chk("мінімум 60 ₴ діє й для конкурентної ціни: без конкурента 63 ₴ (береться), з конкурентом 58 → ціна <60 → 'cheap' (не публікуємо)",
    d36 >= 60 > dec36["price"] and rt_prom.build_offer_fields("rt-1", rt_item("Перша іграшка Модель 1", cost=36), SI, live, {})[0] is not None
    and rt_prom.build_offer_fields("rt-1", rt_item("Перша іграшка Модель 1", cost=36), SI, live, {}, reprice=ctx(e36))[1] == "cheap")


def base_root(n=3):
    yml = ET.Element("yml_catalog", date="2026-11-22 10:00")
    shop = ET.SubElement(yml, "shop")
    ET.SubElement(shop, "categories")
    offers = ET.SubElement(shop, "offers")
    for i in range(n):
        o = ET.SubElement(offers, "offer", id=str(500 + i), available="true")
        ET.SubElement(o, "name_ua").text = f"Toysi товар {i}"
    return yml


def feed_text(reprice=None):
    r = base_root()
    st = rt_prom.append_rt_offers(r, live, selection, sup_items, 6000, set(), save_state=False, reprice=reprice)
    return ET.tostring(r, encoding="unicode"), st, r


t_none, st_none, _ = feed_text(None)
t_empty, _, _ = feed_text({"items": {}, "now": NOW})
chk("append_rt_offers: reprice=None і reprice з порожнім станом → фід БІТОВО той самий (7 rt-офферів)", t_none == t_empty and st_none["emitted"] == 7)
t_rp, st_rp, root_rp = feed_text({"items": {"rt-1": e_fresh, "rt-3": e_del}, "now": NOW})
prices = {o.get("id"): float(o.findtext("price")) for o in root_rp.iter("offer") if o.get("id", "").startswith("rt-")}
chk("append_rt_offers із станом: rt-1 → 187.00; rt-3 (неконкурентний) відсутній, skip_noncompetitive=1; решта як було (210)", prices.get("rt-1") == 187.0 and "rt-3" not in prices and st_rp["skip_noncompetitive"] == 1 and prices["rt-2"] == default_p)
chk("Toysi-офери (500–502) у фіді не змінились", all(f'id="{500 + i}"' in t_rp for i in range(3)) and t_rp.count('id="rt-') == 6)
# неконкурентний, що був у фіді раніше → заглушка available=false (осиротілий; журнал лише читаємо/доповнюємо існуючою логікою)
fresh_files()
rt_prom._atomic_write(rt_prom.LISTED_FILE, {"v": 1, "listed": {"rt-3": {"name": "Третя іграшка Модель 3", "price": 210.0, "cat": "2606", "vendor": "", "pics": ["https://plutustoys.com.ua/img/rt/rt-1-1.jpg"], "desc": ""}}})
_, st_o, root_o = feed_text({"items": {"rt-3": e_del}, "now": NOW})
stub = next((o for o in root_o.iter("offer") if o.get("id") == "rt-3"), None)
chk("rt-3 (був у фіді, тепер неконкурентний) → заглушка available=false (осиротілий), НЕ активний офер", stub is not None and stub.get("available") == "false" and st_o["orphans"] == 1)
fresh_files()

# ───────────────────────── (5) прогін: dry-run / apply ─────────────────────────
class FakeNet:
    def __init__(self, comps, verify=True, dc=None):
        self.comps, self._verify, self.calls, self.notes, self.verify_calls = comps, verify, [], [], 0
        self.decide_action = pcp.decide_action
        self.identity = pcp._competitor_identity_key
        self.commission_is_default = pcp._category_commission_is_default
        self.search_delay = 0.0
        self.sleep = lambda s: None
        self.jitter = lambda: None
        self.fetch_by_ext_ids = lambda ids: ({}, set())
        self.resolve_url_text = lambda pid: None

    def find_best_competitor(self, name, cost, own_link, pictures):
        self.calls.append(name)
        return self.comps.get(name)

    def verify(self, c):
        self.verify_calls += 1
        return self._verify

    def notify(self, text):
        self.notes.append(text)


def bb(price):
    return {"score": 1.0, "price": price, "name": "BuyBox: 2 інших продавців цього ж товару", "id": None, "urlText": None, "image": None, "source": "buybox"}


def txt(price, score=0.9):
    return {"score": score, "price": price, "name": "Аналог", "id": 777, "urlText": "analog", "image": None}


pcp._photo_confirms_match = lambda *a, **k: False          # жодних мережевих перевірок фото
os.environ["PROM_RT_IN_FEED"] = "1"
os.environ["PROM_RT_REPRICE"] = "1"
comps = {"Перша іграшка Модель 1": bb(190.0), "Третя іграшка Модель 3": bb(100.0), "Четверта іграшка Модель 4": txt(100.0), "П'ята іграшка Модель 5": txt(100.0),
         "Шоста іграшка Модель 6": bb(100.0)}
net = FakeNet(comps, verify=False)          # rt-4/rt-5: сторінка конкурента спершу НЕ підтверджує InStock
cp_state_before = cp.PROM_PRICE_STATE_FILE.exists()
fresh_files()
res_dry = rt_reprice.run_phase(live, apply=False, net=net, now=NOW)
chk("dry-run: файл стану НЕ записано, але рішення пораховано (перевірено 6 rt-id: 1,2,3,4,5,6 + X_7-a = 7)", not rt_reprice.STATE_FILE.exists() and res_dry["checked"] == 7)
res = rt_reprice.run_phase(live, apply=True, net=net, now=NOW)
st = rt_reprice.load_state()
items = st["items"]
print("  результат:", res)
print("  стан:", {k: (v["action"], v["price"], v["category"]) for k, v in sorted(items.items())})
chk("apply: стан записано у ОКРЕМИЙ файл; усі ключі — рядки rt-…, жодного Toysi-ключа, Toysi-файл стану не створено", set(items) == {"rt-1", "rt-2", "rt-3", "rt-4", "rt-5", "rt-6", "rt-X_7-a"}
    and all(k.startswith("rt-") for k in items) and not any(k in items for k in catalog) and not cp.PROM_PRICE_STATE_FILE.exists() and cp_state_before is False)
chk("rt-1: buyBox 190 → adjust, ціна 187 = decide_price_for_platform(120,190,'prom',…)", items["rt-1"]["action"] == "adjust" and items["rt-1"]["price"] == 187.0 and items["rt-1"]["competitor_price"] == 190.0 and items["rt-1"]["competitor_key"] == "buybox")
chk("rt-2: конкурента нема → adjust по ціні «без конкурента» 210, категорія no_competitor", items["rt-2"]["action"] == "adjust" and items["rt-2"]["price"] == default_p and items["rt-2"]["category"] == "no_competitor")
dec_floor = cp.decide_price_for_platform(COST, 100.0, "prom", "Тест-Машини", prom_category_id=2606)
chk(f"rt-3: buyBox 100 (floor {dec_floor['floor']} вище кандидата 97) → delist (неконкурентний), ціна запису = floor ≥ min_safe, не збиткова", items["rt-3"]["action"] == "delist" and dec_floor["category"] == "floor"
    and items["rt-3"]["price"] == dec_floor["price"] and items["rt-3"]["margin_pct"] >= 0)
chk("rt-4/rt-5: текстовий конкурент score 0.9, сторінка НЕ підтверджує InStock → delist заблоковано (adjust, ціна floor); verify викликано для не-buyBox", items["rt-4"]["action"] == "adjust" and items["rt-5"]["action"] == "adjust" and net.verify_calls >= 2)
chk("rt-6: непідтверджена (дефолтна) комісія → без зняття навіть при buyBox 100 (adjust)", pcp._category_commission_is_default("Невідома категорія", 999999) and items["rt-6"]["action"] == "adjust")
chk("жодна записана ціна не нижча за собівартість (120) і не нижча за 60 ₴", all(v["price"] >= COST and v["price"] >= rt_prom.MIN_PROM_PRICE for v in items.values()))
t_f, st_f, root_f = feed_text(rt_reprice.feed_context(NOW))
pf = {o.get("id"): float(o.findtext("price")) for o in root_f.iter("offer") if o.get("id", "").startswith("rt-") and o.get("available") == "true"}
chk("фід за записаним станом: rt-3 не емітується (skip_noncompetitive), rt-1 = 187.00, rt-2 = 210", "rt-3" not in pf and pf["rt-1"] == 187.0 and pf["rt-2"] == default_p and st_f.get("skip_noncompetitive") == 1)
# rt-5 з підтвердженням → delist
net._verify = True
rt_reprice.run_phase(live, apply=True, net=net, now=NOW + timedelta(hours=1))
items2 = rt_reprice.load_state()["items"]
chk("сторінка конкурента ПІДТВЕРДЖУЄ InStock → rt-4 і rt-5 теж delist (текстовий score 0.9 ≥ MATCH_MIN_SCORE_FOR_DELIST)", items2["rt-4"]["action"] == "delist" and items2["rt-5"]["action"] == "delist")
# конкурент зник → rt-3 повертається в емісію (переперевірка пулу, бо пул не залежить від стану)
net.comps.pop("Третя іграшка Модель 3")
rt_reprice.run_phase(live, apply=True, net=net, now=NOW + timedelta(hours=2))
items3 = rt_reprice.load_state()["items"]
chk("конкурента rt-3 не знайдено при переперевірці → action=adjust, ціна без конкурента: rt-3 ПОВЕРТАЄТЬСЯ в емісію", items3["rt-3"]["action"] == "adjust" and items3["rt-3"]["price"] == default_p)

# ───────────────────────── (7) breaker / кап / бюджет / помилки ─────────────────────────
fresh_files()
net_b = FakeNet({n: bb(100.0) for n in (s["name"] for s in sup_items.values())})
rt_reprice.BREAKER_MIN_CHECKED = 5
res_b = rt_reprice.run_phase(live, apply=True, net=net_b, now=NOW)
itb = rt_reprice.load_state()["items"]
chk("BREAKER: ≥60% delist серед перевірених → УСІ зняття заблоковано (жодного action=delist), ціни на безпечному floor", res_b.get("breaker_blocked_delist", 0) >= 5 and not any(v["action"] == "delist" for v in itb.values()) and all(v["price"] >= COST for v in itb.values()))
fresh_files()
rt_reprice.BREAKER_MIN_CHECKED = 10_000                      # вимкнути breaker, щоб перевірити кап
rt_reprice.MAX_DELIST_PER_RUN = 2
res_c = rt_reprice.run_phase(live, apply=True, net=FakeNet({n: bb(100.0) for n in (s["name"] for s in sup_items.values())}), now=NOW)
itc = rt_reprice.load_state()["items"]
chk("КАП зняттів за прогін: MAX_DELIST_PER_RUN=2 → рівно 2 delist, решта adjust на floor, deferred зафіксовано", sum(1 for v in itc.values() if v["action"] == "delist") == 2 and res_c["delist_deferred_by_cap"] >= 3)
rt_reprice.MAX_DELIST_PER_RUN, rt_reprice.BREAKER_MIN_CHECKED = 100, 30
fresh_files()
res_e = rt_reprice.run_phase(live, apply=True, net=FakeNet({}), now=NOW, time_exceeded=lambda: True)
chk("бюджет Toysi вичерпано → rt-фаза пропущена (skipped=toysi_budget), стан не створено", res_e == {"skipped": "toysi_budget"} and not rt_reprice.STATE_FILE.exists())
calls = {"n": 0}


def exceeded_after_two():
    calls["n"] += 1
    return calls["n"] > 3


fresh_files()
res_f = rt_reprice.run_phase(live, apply=True, net=FakeNet({}), now=NOW, time_exceeded=exceeded_after_two)
chk("зупинка за бюджетом посеред партії: перевірено частину, budget_stop=1, зроблене збережено", res_f.get("budget_stop") == 1 and 0 < res_f["checked"] < 7 and len(rt_reprice.load_state()["items"]) == res_f["checked"])


class BoomNet(FakeNet):
    def find_best_competitor(self, name, cost, own_link, pictures):
        if name.startswith("Друга"):
            raise RuntimeError("boom")
        return None


fresh_files()
res_g = rt_reprice.run_phase(live, apply=True, net=BoomNet({}), now=NOW)
chk("збій одного rt-id не валить партію (error=1, решта 6 записано)", res_g["error"] == 1 and res_g["checked"] == 6 and "rt-2" not in rt_reprice.load_state()["items"])
# каталог усічений / постачальник застарів / відбір застарів
fresh_files()
chk("усічений каталог → skipped=catalog", rt_reprice.run_phase({"1": {}}, apply=True, net=FakeNet({}), now=NOW)["skipped"] == "catalog")
old_sel = dict(selection, built_at=(NOW - timedelta(hours=40)).isoformat(timespec="seconds"))
rt_prom._atomic_write(rt_prom.SEL_FILE, old_sel)
chk("відбір старший 36 год → skipped=selection_stale", rt_reprice.run_phase(live, apply=True, net=FakeNet({}), now=NOW)["skipped"] == "selection_stale")
rt_prom._atomic_write(rt_prom.SEL_FILE, selection)
rt_prom._atomic_write(rt_prom.SUPPLIER_FILE, {"updated": (NOW - timedelta(hours=9)).isoformat(timespec="seconds"), "items": sup_items})
chk("supplier_rt.json старший 6 год → skipped=supplier_stale", rt_reprice.run_phase(live, apply=True, net=FakeNet({}), now=NOW)["skipped"] == "supplier_stale")
rt_prom._atomic_write(rt_prom.SUPPLIER_FILE, {"updated": NOW.isoformat(timespec="seconds"), "items": sup_items})
os.environ.pop("PROM_RT_IN_FEED")
chk("PROM_RT_IN_FEED вимкнено (rt- у фіді нема) → skipped=rt_not_in_feed", rt_reprice.run_phase(live, apply=True, net=FakeNet({}), now=NOW)["skipped"] == "rt_not_in_feed")
os.environ["PROM_RT_IN_FEED"] = "1"

# ───────────────────────── (8) ротація партії ─────────────────────────
ids = [f"rt-{i}" for i in range(20)]
its = {f"rt-{i}": {"timestamp": f"2026-11-{10 + i:02d}T00:00:00"} for i in range(10, 20)}      # rt-0…9 ніколи не перевірені, rt-10…19 перевірені (rt-10 найстаріший)
b = rt_reprice.pick_batch(ids, its, 8)
chk("pick_batch: «ніколи не перевірені» першими (6: rt-0…5), чверть слотів (2) гарантовано найстарішим перевіреним (rt-10, rt-11)", b == ["rt-0", "rt-1", "rt-2", "rt-3", "rt-4", "rt-5", "rt-10", "rt-11"])
b2 = rt_reprice.pick_batch(ids[:12], {f"rt-{i}": its[f"rt-{i}"] for i in (10, 11)}, 8)
chk("pick_batch: перевірених мало → слоти добирають новими (без простою), без дублів", len(b2) == 8 and len(set(b2)) == 8 and {"rt-10", "rt-11"} <= set(b2))
b3 = rt_reprice.pick_batch(ids, {f"rt-{i}": {"timestamp": f"2026-11-{1 + i % 20:02d}T00:00:00"} for i in range(20)}, 8)
chk("pick_batch: усе перевірено → 8 найстаріших за timestamp", len(b3) == 8 and b3 == sorted(ids, key=lambda s: (its.get(s) or {"timestamp": f"2026-11-{1 + int(s[3:]) % 20:02d}T00:00:00"})["timestamp"])[:8])
chk("estimate: 1500 rt-id ≈ 63 хв, партія за замовчуванням 400 ≈ 17 хв, 1600/добу (4 прогони) покриває пул 1500", abs(rt_reprice.estimate_minutes(1500) - 63.0) < 0.1 and abs(rt_reprice.estimate_minutes(400) - 16.8) < 0.1 and rt_reprice.BATCH_DEFAULT * 4 >= 1500)
os.environ["PROM_RT_REPRICE_BATCH"] = "99999"
chk("PROM_RT_REPRICE_BATCH обмежено BATCH_ABS_MAX=1500", rt_reprice.batch_size() == 1500)
os.environ.pop("PROM_RT_REPRICE_BATCH")
fresh_files()
rt_reprice.BATCH_DEFAULT = 3
seen = set()
for k in range(3):
    rt_reprice.run_phase(live, apply=True, net=FakeNet({}), now=NOW + timedelta(hours=k))
    seen = set(rt_reprice.load_state()["items"])
chk("3 прогони по 3 → усі 7 rt-id мають запис (жодного не голодує)", len(seen) == 7)
rt_reprice.BATCH_DEFAULT = 400

# ───────────────────────── (6) rt- ключі не ламають місця, що ітерують стан ─────────────────────────
rt_state = {"rt-1": {"price": 187.0, "timestamp": NOW.isoformat(), "competitor_price": 190.0}, "rt-X_7-a": {"price": 210.0, "timestamp": NOW.isoformat()}, "_meta": {}, "_delisted_since": {"rt-3": NOW.isoformat()}}
toysi_cat = {"1001": {"id": "1001", "name": "T", "stock": 5, "price": "100"}}
try:
    a = pcp._rotated_out_needing_live_lookup({}, toysi_cat, {}, rt_state, None)
    b_ = pcp._rotated_out_scan_candidates({}, toysi_cat, {"rt-1": {"competitor_price": 1.0, "price_category": "floor"}}, None)
    c = cp.effective_delisted(rt_state)
    ok_iter = a == {} and b_ == {} and c == {"rt-3": rt_state["_delisted_since"]["rt-3"]}
except Exception as e:  # noqa: BLE001
    print("  виняток:", type(e).__name__, e)
    ok_iter = False
chk("rt-ключі в Toysi-структурах (теоретично) не кидають винятків у _rotated_out_*/effective_delisted (немає int(pid) у цих місцях; невідомий pid → пропуск)", ok_iter)
cp.PROM_PRICE_STATE_FILE.write_text(json.dumps(rt_state), encoding="utf-8")
ov = cp.load_fresh_prom_price_overrides()
chk("load_fresh_prom_price_overrides зберігає rt-ключ рядком і не падає (але rt-стан Toysi-файл НЕ містить — окремий файл)", ov.get("rt-1") == 187.0)
cp.PROM_PRICE_STATE_FILE.unlink()

# ───────────────────────── Toysi-фід бітово незмінний за браму/стан ─────────────────────────
it = {"id": "5001", "name": "Toysi предмет", "stock": 5, "price": "200", "category_id": "1", "category_name": "Тест-Машини", "vendor": "X", "pictures": ["https://a/b.jpg"], "description": "d", "vendor_code": "5001"}


def toysi_xml():
    r, _s = gp._build_xml({"5001": it}, prom_category_cache={"5001": {"category_id": 2606}}, price_overrides={"5001": 300.0}, competitor_prices={"5001": 310.0})
    return ET.tostring(r, encoding="unicode").split("date=", 1)[1][22:]


os.environ.pop("PROM_RT_REPRICE")
x0 = toysi_xml()
rt_reprice.save_state({"v": 1, "items": {"rt-1": dict(e_del)}, "links": {}, "_meta": {}})
os.environ["PROM_RT_REPRICE"] = "1"
x1 = toysi_xml()
chk("Toysi-частина фіду (_build_xml) бітово та сама: брама вимкнена / увімкнена + файл rt-стану", x0 == x1)
os.environ.pop("PROM_RT_REPRICE")
# брама вимкнена, а файл стану з delist існує → rt-фід БІТОВО той самий (maybe_add_rt_offers)
fresh_files()
rt_reprice.save_state({"v": 1, "items": {"rt-1": dict(e_del), "rt-2": dict(e_fresh)}, "links": {}, "_meta": {}})
rt_prom.LISTED_FILE = tmp / f"listed_{next(_cnt)}.json"
w_off = rt_prom.maybe_add_rt_offers(base_root(), live, {}, 6000)
rt_prom.LISTED_FILE = tmp / f"listed_{next(_cnt)}.json"
os.environ["PROM_RT_REPRICE"] = "1"
w_on = rt_prom.maybe_add_rt_offers(base_root(), live, {}, 6000)
os.environ.pop("PROM_RT_REPRICE")
rt_prom.LISTED_FILE = tmp / f"listed_{next(_cnt)}.json"
os.environ.pop("PROM_RT_IN_FEED")
chk("maybe_add_rt_offers: PROM_RT_REPRICE вимкнено → rt-1 емітується (стан ігнорується); увімкнено → rt-1 (delist) випадає, rt-2 за станом", 'id="rt-1"' in ET.tostring(w_off, encoding="unicode") and 'id="rt-1"' not in ET.tostring(w_on, encoding="unicode")
    and 'id="rt-2"' in ET.tostring(w_on, encoding="unicode"))

# ───────────────────────── own_link (buyBox) для rt-: резолв лише для колись емітованих ─────────────────────────
os.environ["PROM_RT_IN_FEED"] = "1"
os.environ["PROM_RT_REPRICE"] = "1"
fresh_files()
rt_prom._atomic_write(rt_prom.LISTED_FILE, {"v": 1, "listed": {"rt-1": {"name": "x", "price": 210.0}, "rt-2": {"name": "y", "price": 210.0}, "rt-3": {"name": "z", "price": 210.0}}})


class LinkNet(FakeNet):
    def __init__(self):
        super().__init__({})
        self.fetch_calls, self.links_seen = [], {}
        self.fetch_by_ext_ids = self._fetch
        self.resolve_url_text = lambda pid: f"slug-{pid}"

    def _fetch(self, ids):
        self.fetch_calls.append(set(ids))
        return {"rt-1": {"id": 555}}, {"rt-3"}                      # rt-1 є в Prom, rt-2 — 404 (нема), rt-3 — мережева невизначеність

    def find_best_competitor(self, name, cost, own_link, pictures):
        self.links_seen[name] = own_link
        return None


ln = LinkNet()
rt_reprice.run_phase(live, apply=False, net=ln, now=NOW)
chk("dry-run: own_link розв'язується для передачі в пошук (buyBox), але НЕ зберігається (стан не створено); запит лише для колись емітованих (rt-1,rt-2,rt-3), не для всіх 7",
    ln.fetch_calls == [{"rt-1", "rt-2", "rt-3"}] and ln.links_seen["Перша іграшка Модель 1"] == {"prom_id": 555, "url_text": "slug-555"} and not rt_reprice.STATE_FILE.exists())
fresh_files()
rt_prom._atomic_write(rt_prom.LISTED_FILE, {"v": 1, "listed": {"rt-1": {"name": "x", "price": 210.0}, "rt-2": {"name": "y", "price": 210.0}, "rt-3": {"name": "z", "price": 210.0}}})
ln = LinkNet()
rt_reprice.run_phase(live, apply=True, net=ln, now=NOW)
lk = rt_reprice.load_state()["links"]
chk("apply: links зберігаються лише у rt-файлі: rt-1 → {prom_id, url_text}; rt-2 (404) → absent_at; rt-3 (невизначено) → без запису (повторимо)", lk["rt-1"]["prom_id"] == 555 and lk["rt-1"]["url_text"] == "slug-555" and "absent_at" in lk["rt-2"] and "rt-3" not in lk)
ln2 = LinkNet()
rt_reprice.run_phase(live, apply=True, net=ln2, now=NOW + timedelta(hours=2))
chk("повторний прогін: rt-1 (є link) і rt-2 (absent <24 год) не перезапитуються — лише rt-3", ln2.fetch_calls == [{"rt-3"}] and ln2.links_seen["Перша іграшка Модель 1"] == {"prom_id": 555, "url_text": "slug-555"})
ln3 = LinkNet()
rt_reprice.run_phase(live, apply=True, net=ln3, now=NOW + timedelta(hours=30))
chk("через 30 год absent-запис rt-2 перезапитується (LINK_RETRY_H=24)", bool(ln3.fetch_calls) and "rt-2" in ln3.fetch_calls[0])

# ───────────────────────── хук у prom_competitor_pricer: делегує, не валить Toysi ─────────────────────────
import contextlib  # noqa: E402
import io  # noqa: E402
fresh_files()
_orig_run = rt_reprice.run_phase
cap = {}
rt_reprice.run_phase = lambda catalog, apply=False, **kw: cap.update({"catalog": catalog, "apply": apply, "te": kw.get("time_exceeded")}) or {"ok": 1}
pcp._RT_CTX.clear()
pcp._RT_CTX.update({"catalog": catalog, "apply": False})
pcp._run_rt_reprice_phase()
chk("хук: брама ON → run_phase(каталог Toysi цього прогону, apply = фінальний args.apply (False = dry-run/SAFETY_HOLD), time_exceeded=_time_budget_exceeded)", cap.get("catalog") is catalog and cap["apply"] is False and cap["te"] is pcp._time_budget_exceeded)
rt_reprice.run_phase = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    with contextlib.redirect_stderr(io.StringIO()):
        pcp._run_rt_reprice_phase()
    hook_safe = True
except Exception:  # noqa: BLE001
    hook_safe = False
chk("хук: збій rt-фази не піднімає виняток (Toysi-робота вже зроблена, не зачеплена)", hook_safe)
rt_reprice.run_phase = _orig_run
pcp._RT_CTX.clear()
_err = io.StringIO()
with contextlib.redirect_stderr(_err):
    pcp._run_rt_reprice_phase()
chk("хук: без каталогу в контексті (main не дійшов) → пропуск без винятку, з поясненням у stderr", "каталог Toysi цього прогону недоступний" in _err.getvalue())
dn = rt_reprice.default_net()
chk("default_net: усі залежності резолвляться (імпорт без мережі) і це ТІ САМІ функції Toysi-репрайсера", dn.find_best_competitor is pcp.find_best_competitor and dn.decide_action is pcp.decide_action and dn.verify is pcp.verify_competitor_really_available
    and callable(dn.fetch_by_ext_ids) and callable(dn.resolve_url_text) and callable(dn.notify) and dn.search_delay == pcp.SEARCH_DELAY)
os.environ.pop("PROM_RT_REPRICE", None)
os.environ.pop("PROM_RT_IN_FEED", None)

if F:
    print(f"\nFAIL: {len(F)}")
    sys.exit(1)
print("\nOK: усі перевірки пройдені")
