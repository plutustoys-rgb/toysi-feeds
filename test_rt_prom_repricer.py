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
chk("фід: delist-запис (свіжий) → noncompetitive; СЕРЕДНЯ-3 fail-closed: протермінований за 7 діб (раніше fail-open) ТЕПЕР теж не емітується, до 45 діб; битий timestamp delist-запису → теж не емітується; старший 45 діб → запис недійсний (default)",
    fp(e_del) == (None, "noncompetitive") and fp(dict(e_del, timestamp=(NOW - timedelta(days=8)).isoformat())) == (None, "noncompetitive") and fp(dict(e_del, timestamp=(NOW - timedelta(days=44)).isoformat())) == (None, "noncompetitive")
    and fp(dict(e_del, timestamp="xx")) == (None, "noncompetitive") and fp(dict(e_del, timestamp=(NOW - timedelta(days=46)).isoformat())) == (default_p, ""))
pool_ctx = {"items": {"rt-1": dict(e_del, timestamp=(NOW - timedelta(days=8)).isoformat())}, "now": NOW, "for_pool": True}
chk("пул: підтверджено неконкурентний повертається на переперевірку лише коли минуло 7 діб (8 діб → так; 2 доби → ні)",
    rt_reprice.feed_price("rt-1", COST, "Тест-Машини", 2606, default_p, pool_ctx) == (default_p, "")
    and rt_reprice.feed_price("rt-1", COST, "Тест-Машини", 2606, default_p, dict(pool_ctx, items={"rt-1": dict(e_del, timestamp=(NOW - timedelta(days=2)).isoformat())})) == (None, "noncompetitive"))

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


def write_journal(ids):
    rt_prom._atomic_write(rt_prom.LISTED_FILE, {"v": 1, "listed": {i: {"name": "x", "price": 210.0, "cat": "2606", "pics": ["https://plutustoys.com.ua/img/rt/rt-1-1.jpg"]} for i in ids}})


ALL7 = ["rt-1", "rt-2", "rt-3", "rt-4", "rt-5", "rt-6", "rt-X_7-a"]


def feed_text(reprice=None, known=ALL7):
    r = base_root()
    if known:
        write_journal(known)
    elif rt_prom.LISTED_FILE.exists():
        rt_prom.LISTED_FILE.unlink()
    st = rt_prom.append_rt_offers(r, live, selection, sup_items, 6000, set(), save_state=False, reprice=reprice)
    return ET.tostring(r, encoding="unicode"), st, r


t_none, st_none, _ = feed_text(None)
t_empty, _, _ = feed_text({"items": {}, "now": NOW})
chk("append_rt_offers: reprice=None і reprice з порожнім станом для ВЖЕ існуючих (журнал) карток → фід БІТОВО той самий (7 rt-офферів; існуючі активні картки check-before-create не чіпає)", t_none == t_empty and st_none["emitted"] == 7)
_t_new, st_new, _ = feed_text({"items": {}, "now": NOW}, known=[])
chk("check-before-create: без журналу і без записів стану НОВІ картки не створюються (skip_new_unchecked=7, emitted=0); без брами (reprice=None) — створюються як раніше", st_new.get("emitted", 0) == 0 and st_new["skip_new_unchecked"] == 7 and feed_text(None, known=[])[1]["emitted"] == 7)
fresh_e = dict(e_fresh, timestamp=(NOW - timedelta(hours=3)).isoformat())
_t, st_c, root_c = feed_text({"items": {"rt-1": fresh_e, "rt-2": dict(fresh_e, timestamp=(NOW - timedelta(hours=50)).isoformat()), "rt-3": dict(fresh_e, action="delist"), "rt-4": dict(fresh_e, action="adjust", category="no_competitor", price=210.0)}, "now": NOW}, known=[])
ids_c = sorted(o.get("id") for o in root_c.iter("offer") if o.get("id", "").startswith("rt-"))
chk("check-before-create: нова картка емітується лише зі свіжим (≤48 год) записом adjust: rt-1, rt-4 — так; rt-2 (запис 50 год), rt-3 (delist), rt-5… (нема запису) — ні", ids_c == ["rt-1", "rt-4"])
t_rp, st_rp, root_rp = feed_text({"items": {"rt-1": e_fresh, "rt-3": e_del}, "now": NOW})
prices = {o.get("id"): float(o.findtext("price")) for o in root_rp.iter("offer") if o.get("id", "").startswith("rt-") and o.get("available") == "true"}
chk("append_rt_offers із станом: rt-1 → 187.00; rt-3 (неконкурентний) не активний (заглушка), skip_noncompetitive=1; решта як було (210)", prices.get("rt-1") == 187.0 and "rt-3" not in prices and st_rp["skip_noncompetitive"] == 1 and prices["rt-2"] == default_p)
chk("Toysi-офери (500–502) у фіді не змінились", all(f'id="{500 + i}"' in t_rp for i in range(3)) and len(prices) == 6)
# неконкурентний, що був у фіді раніше → заглушка available=false (осиротілий; журнал лише читаємо/доповнюємо існуючою логікою)
fresh_files()
rt_prom._atomic_write(rt_prom.LISTED_FILE, {"v": 1, "listed": {"rt-3": {"name": "Третя іграшка Модель 3", "price": 210.0, "cat": "2606", "vendor": "", "pics": ["https://plutustoys.com.ua/img/rt/rt-1-1.jpg"], "desc": ""}}})
_, st_o, root_o = feed_text({"items": {"rt-3": e_del}, "now": NOW})
stub = next((o for o in root_o.iter("offer") if o.get("id") == "rt-3"), None)
chk("rt-3 (був у фіді, тепер неконкурентний) → заглушка available=false (осиротілий), НЕ активний офер", stub is not None and stub.get("available") == "false" and st_o["orphans"] == 1)
fresh_files()

# ───────────────────────── (5) прогін: dry-run / apply / серія / гістерезис ─────────────────────────
class FakeNet:
    def __init__(self, comps, verify=True):
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
        return bool(c.get("alive", self._verify))

    def notify(self, text):
        self.notes.append(text)


def bb(price):
    return {"score": 1.0, "price": price, "name": "BuyBox: 2 інших продавців цього ж товару", "id": None, "urlText": None, "image": None, "source": "buybox"}


def txt(price, name, score=0.9, alive=True):
    return {"score": score, "price": price, "name": name, "id": 777, "urlText": "analog", "image": None, "alive": alive}


pcp._photo_confirms_match = lambda *a, **k: False          # жодних мережевих перевірок фото
os.environ["PROM_RT_IN_FEED"] = "1"
os.environ["PROM_RT_REPRICE"] = "1"
rt_reprice.OBSERVE_FILE = tmp / "observe.jsonl"
comps = {"Перша іграшка Модель 1": bb(190.0), "Третя іграшка Модель 3": bb(100.0), "Четверта іграшка Модель 4": txt(100.0, "Аналог Модель 4", alive=False),
         "П'ята іграшка Модель 5": txt(100.0, "Аналог Модель 5"), "Шоста іграшка Модель 6": bb(100.0)}


def run(net_, hours, apply=True, **kw):
    return rt_reprice.run_phase(live, apply=apply, net=net_, now=NOW + timedelta(hours=hours), **kw)


cp_state_before = cp.PROM_PRICE_STATE_FILE.exists()
fresh_files()
net = FakeNet(comps)
res_dry = run(net, 0, apply=False)
chk("dry-run: файл стану НЕ записано, але рішення пораховано (7 rt-id)", not rt_reprice.STATE_FILE.exists() and res_dry["checked"] == 7)
res1 = run(net, 0)
items = rt_reprice.load_state()["items"]
print("  прогін 1:", res1)
print("  стан 1:", {k: (v["action"], v["price"], v["category"], v["streak"]) for k, v in sorted(items.items())})
chk("apply: стан у ОКРЕМОМУ файлі; усі ключі — рядки rt-…, жодного Toysi-ключа, Toysi-файл стану не створено", set(items) == set(ALL7) and not any(k in items for k in catalog) and not cp.PROM_PRICE_STATE_FILE.exists() and cp_state_before is False)
chk("rt-1: buyBox 190 → adjust 187; rt-2: конкурента нема → 210 (no_competitor)", items["rt-1"]["price"] == 187.0 and items["rt-1"]["action"] == "adjust" and items["rt-2"]["price"] == default_p and items["rt-2"]["category"] == "no_competitor")
dec_floor = cp.decide_price_for_platform(COST, 100.0, "prom", "Тест-Машини", prom_category_id=2606)
cost_eff = cp.prom_effective_cost(COST)
chk("БЛОК-1(б) перше delist-рішення ще НЕ знімає: rt-3 (buyBox 100, floor) → action=adjust, streak=1, лишається в емісії; rt-5 (текстовий, сторінка підтверджує) теж streak=1; rt-4 (сторінка НЕ підтверджує) → streak 0",
    items["rt-3"]["action"] == "adjust" and items["rt-3"]["streak"] == 1 and items["rt-5"]["action"] == "adjust" and items["rt-5"]["streak"] == 1 and items["rt-4"]["streak"] == 0 and dec_floor["category"] == "floor")
P_floor = {"name": "Качечка", "cost": COST, "tcat": "Тест-Машини", "pid_int": 2606, "pictures": ["https://plutustoys.com.ua/img/rt/rt-1-1.jpg"]}
dd_fl, _ = rt_reprice.decide_one(P_floor, None, FakeNet({"Качечка": bb(60.0)}))
chk("СЕРЕДНЯ-4: рішення «floor» (cost 120, конкурент 60) → division-floor 3 %: margin_pct ≥ 3.0, ціна < дефолту; стара формула (decide_price_for_platform) давала " + f"{dec_floor['margin_pct']}%",
    dd_fl["category"] == "floor" and dd_fl["margin_pct"] >= 3.0 and dd_fl["price"] > dec_floor["price"] and dd_fl["price"] < default_p and dec_floor["margin_pct"] < 1.0)
chk("ПОВТОРНИЙ АУДИТ (3): поки зняття не підтверджено (streak=1), у СТАНІ лишається default 210 (нема попередньої ціни) — НЕ division-floor; rt-3/rt-5 не знижено на непідтвердженому конкуренті",
    items["rt-3"]["price"] == default_p and items["rt-5"]["price"] == default_p and items["rt-3"]["margin_pct"] > 40)
run(net, 10)
it2 = rt_reprice.load_state()["items"]
chk("БЛОК-1(б): 2-й delist через 10 год (<20 год) НЕ зараховується: streak лишається 1, action=adjust", it2["rt-3"]["streak"] == 1 and it2["rt-3"]["action"] == "adjust")
res3 = run(net, 21)
it3 = rt_reprice.load_state()["items"]
chk("БЛОК-1(б): 2-й delist через 21 год → rt-3 і rt-5 ЗНЯТО (action=delist, streak 2); rt-4 (verify=False) і rt-6 (дефолтна комісія) — ні", it3["rt-3"]["action"] == "delist" and it3["rt-5"]["action"] == "delist" and it3["rt-4"]["action"] == "adjust" and it3["rt-6"]["action"] == "adjust" and it3["rt-3"]["streak"] == 2)
t_f, st_f, root_f = feed_text(rt_reprice.feed_context(NOW + timedelta(hours=21)), known=ALL7)
pf = {o.get("id"): float(o.findtext("price")) for o in root_f.iter("offer") if o.get("id", "").startswith("rt-") and o.get("available") == "true"}
chk("фід за станом: rt-3/rt-5 не емітуються (skip_noncompetitive=2, осиротілі заглушки), rt-1 = 187, rt-4 лишився", "rt-3" not in pf and "rt-5" not in pf and pf["rt-1"] == 187.0 and st_f["skip_noncompetitive"] == 2 and st_f["orphans"] == 2 and "rt-4" in pf)
net.calls.clear()
run(net, 22)
chk("БЛОК-2: підтверджено неконкурентних (rt-3, rt-5) НЕ переперевіряють раніше за 7 діб (їх нема в запитах конкурента)", "Третя іграшка Модель 3" not in net.calls and "П'ята іграшка Модель 5" not in net.calls and "Перша іграшка Модель 1" in net.calls)
net.calls.clear()
run(net, 21 + 24 * 8)
chk("через 8 діб підтверджено неконкурентні ВЖЕ переперевіряються (раз на 7 діб, лише в пулі)", "Третя іграшка Модель 3" in net.calls and "П'ята іграшка Модель 5" in net.calls)
chk("сповіщення про нові зняття відправлено (нових 2)", any("нових зняттів з емісії 2" in n for n in net.notes))

# добовий кап (БЛОК-1(в)): ≤ 5 % пулу, мін. 1 (тут знижено до 1), решта чекає
fresh_files()
rt_reprice.DAILY_CAP_MIN = 1
net_c = FakeNet({n: bb(100.0) for n in (s["name"] for s in sup_items.values())})
run(net_c, 0)
res_c2 = run(net_c, 21)
itc = rt_reprice.load_state()["items"]
chk("БЛОК-1(в): добовий кап 1 → з 6 кандидатів на зняття за добу знято РІВНО 1, решта чекає (delist_deferred_by_cap=5)", sum(1 for v in itc.values() if v["action"] == "delist") == 1 and res_c2["delist_deferred_by_cap"] == 5 and rt_reprice.daily_cap(7) == 1)
run(net_c, 21 + 26)
itc3 = rt_reprice.load_state()["items"]
chk("БЛОК-1(в): через добу (>24 год від першого зняття) кап звільняється → знято вже 2 (по 1 на добу)", sum(1 for v in itc3.values() if v["action"] == "delist") == 2)
rt_reprice.DAILY_CAP_MIN = 5
chk("daily_cap: 5 % пулу, мін. 5: пул 7 → 5; пул 1500 → 75; пул 40 → 5", rt_reprice.daily_cap(7) == 5 and rt_reprice.daily_cap(1500) == 75 and rt_reprice.daily_cap(40) == 5)

# два послідовні прогони з різними cap/breaker
fresh_files()
rt_reprice.BREAKER_MIN_CHECKED = 5
net_b = FakeNet({n: bb(100.0) for n in (s["name"] for s in sup_items.values())})
res_b = run(net_b, 0)
itb = rt_reprice.load_state()["items"]
chk("BREAKER (прогін 1): ≥60% delist серед перевірених → усі зняття заблоковано, серії НЕ нараховуються (streak 0), ціни ≥ собівартості", res_b.get("breaker_blocked_delist", 0) >= 5 and all(v["action"] == "adjust" and v["streak"] == 0 for v in itb.values()) and all(v["price"] >= COST for v in itb.values()))
rt_reprice.BREAKER_MIN_CHECKED = 10_000
rt_reprice.MAX_DELIST_PER_RUN = 2
run(net_b, 25)
it_b2 = rt_reprice.load_state()["items"]
chk("прогін 2 (breaker вимкнено, інтервал 25 год): серії стартують (streak=1), зняття ще нема", all(v["streak"] == 1 for k, v in it_b2.items() if k != "rt-6") and not any(v["action"] == "delist" for v in it_b2.values()))
res_b3 = run(net_b, 50)
n3 = sum(1 for v in rt_reprice.load_state()["items"].values() if v["action"] == "delist")
chk("прогін 3 (попрогоновий кап 2): знято рівно 2, відкладено 4 (rt-6 дефолтна комісія — без зняття)", n3 == 2 and res_b3["delist_deferred_by_cap"] == 4)
run(net_b, 75)
n4 = sum(1 for v in rt_reprice.load_state()["items"].values() if v["action"] == "delist")
chk("прогін 4: ще 2 (добовий кап 5 не вичерпано: перші зняття були >24 год тому), разом 4", n4 == 4)
rt_reprice.MAX_DELIST_PER_RUN, rt_reprice.BREAKER_MIN_CHECKED = 100, 30

# advance(): серія / гістерезис — чиста функція
t0 = NOW
a1 = rt_reprice.advance({}, "delist", t0)
a2 = rt_reprice.advance({"action": "adjust", "streak": a1["streak"], "streak_at": a1["streak_at"]}, "delist", t0 + timedelta(hours=19))
a3 = rt_reprice.advance({"action": "adjust", "streak": 1, "streak_at": a1["streak_at"]}, "delist", t0 + timedelta(hours=21))
a4 = rt_reprice.advance({"action": "adjust", "streak": 1, "streak_at": a1["streak_at"]}, "delist", t0 + timedelta(days=9))
a5 = rt_reprice.advance({"action": "adjust", "streak": 1, "streak_at": a1["streak_at"]}, "adjust", t0 + timedelta(hours=30))
chk("advance: 1-й delist → streak 1; через 19 год не зараховується; через 21 год → streak 2, wants_delist; серія старша 7 діб починається заново; adjust обнуляє серію",
    a1["streak"] == 1 and not a1["wants_delist"] and a2["streak"] == 1 and not a2["wants_delist"] and a3["streak"] == 2 and a3["wants_delist"] and a4["streak"] == 1 and not a4["wants_delist"] and a5["streak"] == 0)
dl = {"action": "delist", "streak": 2, "status_at": t0.isoformat()}
h1 = rt_reprice.advance(dl, "adjust", t0 + timedelta(hours=30))
h2a = rt_reprice.advance(dl, "adjust", t0 + timedelta(hours=80))
h2 = rt_reprice.advance(dict(dl, ret_streak=h2a["ret_streak"], ret_at=h2a["ret_at"]), "adjust", t0 + timedelta(hours=101))
h3 = rt_reprice.advance({"action": "adjust", "streak": 1, "streak_at": t0.isoformat(), "status_at": (t0 + timedelta(hours=10)).isoformat()}, "delist", t0 + timedelta(hours=30))
chk("БЛОК-1(г) гістерезис: знята <3 діб тому картка НЕ повертається (held, action=delist); через 80 год — повертається; щойно ПОВЕРНУТА <3 діб не знімається знову (held, wants_delist=False)",
    h1["action"] == "delist" and h1["held"] and h2["action"] == "adjust" and h2["status_at"] != dl["status_at"] and h3["held"] and not h3["wants_delist"] and h3["action"] == "adjust")

# ───────────────────────── БЛОК-1(д) observe ─────────────────────────
fresh_files()
os.environ["PROM_RT_REPRICE"] = "observe"
chk("observe: gate_on()=False (фід/стан не міняються), mode()=observe", not rt_reprice.gate_on() and rt_reprice.mode() == "observe")
rt_reprice.save_state({"v": 1, "items": {"rt-1": dict(e_del)}, "links": {}, "_meta": {}})
st_before = rt_reprice.STATE_FILE.read_text(encoding="utf-8")
net_o = FakeNet(comps)
res_o = run(net_o, 0)
lines = [json.loads(x) for x in rt_reprice.OBSERVE_FILE.read_text(encoding="utf-8").splitlines()]
chk("observe: рішення лише в reports/rt_reprice_observe.jsonl (рядок на rt-id: назва, конкурент, score, рішення), стан-файл БАЙТ-в-байт той самий, сповіщень нема",
    len(lines) >= 6 and {"our_name", "competitor_name", "score", "raw_action", "price", "strict_rejected"} <= set(lines[0]) and rt_reprice.STATE_FILE.read_text(encoding="utf-8") == st_before and not net_o.notes and res_o["checked"] >= 6)
r_obs = rt_prom.maybe_add_rt_offers(base_root(), live, {}, 6000)
os.environ.pop("PROM_RT_REPRICE")
r_off = rt_prom.maybe_add_rt_offers(base_root(), live, {}, 6000)
chk("observe: rt-фід БІТОВО той самий, що з вимкненою брамою (навіть із delist-записом у стані)", ET.tostring(r_obs, encoding="unicode") == ET.tostring(r_off, encoding="unicode") and 'id="rt-1"' in ET.tostring(r_obs, encoding="unicode"))
os.environ["PROM_RT_REPRICE"] = "1"

# ───────────────────────── БЛОК-1(а) CLI --dry ─────────────────────────
import contextlib  # noqa: E402
import io  # noqa: E402
fresh_files()
rt_reprice.OBSERVE_FILE = tmp / "observe_cli.jsonl"
net_d = FakeNet({"Перша іграшка Модель 1": bb(190.0), "Третя іграшка Модель 3": bb(100.0), "П'ята іграшка Модель 5": txt(100.0, "Аналог Модель 5 3 шт")})
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = rt_reprice.main(["--dry", "--limit", "7"], net=net_d, catalog=live, now=NOW)
outp = buf.getvalue()
chk("CLI --dry: друкує пари «наша назва | конкурент | score | рішення» і підсумок; НІЧОГО не пише (ні стан, ні observe-файл, ні сповіщень)", rc == 0 and "Перша іграшка Модель 1" in outp and "BuyBox" in outp and "1.00" in outp and "підсумок" in outp
    and not rt_reprice.STATE_FILE.exists() and not rt_reprice.OBSERVE_FILE.exists() and not net_d.notes)
chk("CLI --dry: конкурент з іншим числовим токеном («… 3 шт») позначено «відкинуто строгим гейтом назви»; без --dry — відмова (код 2)", "відкинуто строгим гейтом назви" in outp and rt_reprice.main([]) == 2)
os.environ.pop("PROM_RT_REPRICE", None)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc2 = rt_reprice.main(["--dry", "--limit", "2"], net=FakeNet({}), catalog=live, now=NOW)
chk("CLI --dry працює і без брами PROM_RT_REPRICE (це перегляд)", rc2 == 0)
os.environ["PROM_RT_REPRICE"] = "1"

# ───────────────────────── БЛОК-1: строгий гейт збігу ─────────────────────────
chk("strict_name_conflict: «Качечка» vs «Качечка 3 шт» і «35 елементів» vs «24 елементи» — КОНФЛІКТ (Toysi-гейт _size_tokens_conflict їх НЕ ловить)",
    rt_reprice.strict_name_conflict("Качечка", "Качечка 3 шт") and rt_reprice.strict_name_conflict("Конструктор 35 елементів", "Конструктор 24 елементи")
    and not pcp._size_tokens_conflict("Качечка", "Качечка 3 шт") and not pcp._size_tokens_conflict("Конструктор 35 елементів", "Конструктор 24 елементи"))
chk("strict_name_conflict: однакові числа (інший порядок/формат) — не конфлікт: «Пазл 35 елементів» ~ «Пазл на 35 елементів», «20х30 см» ~ «20 x 30 см», «1,5 кг» ~ «1.5 кг»",
    not rt_reprice.strict_name_conflict("Пазл 35 елементів", "Пазл на 35 елементів") and not rt_reprice.strict_name_conflict("Коробка 20х30 см", "Коробка 20 x 30 см") and not rt_reprice.strict_name_conflict("Набір 1,5 кг", "Набір 1.5 кг"))
P = {"name": "Качечка", "cost": COST, "tcat": "Тест-Машини", "pid_int": 2606, "pictures": ["https://plutustoys.com.ua/img/rt/rt-1-1.jpg"]}
for our, comp_name in (("Качечка", "Качечка 3 шт"), ("Конструктор 35 елементів", "Конструктор 24 елементи")):
    dd, _raw = rt_reprice.decide_one(dict(P, name=our), None, FakeNet({our: txt(100.0, comp_name, score=0.95)}))
    chk(f"приклад аудиту «{our}» vs «{comp_name}» (score 0.95, ціна 100) → НЕ delist-збіг: конкурент відкинуто, action=adjust, ціна без конкурента {default_p}", dd["action"] == "adjust" and dd["strict_rejected"] and dd["competitor"] is None and dd["price"] == default_p)
dd_ok, _r = rt_reprice.decide_one(dict(P, name="Качечка 3 шт"), None, FakeNet({"Качечка 3 шт": txt(100.0, "Качечка 3 шт", score=0.95)}))
chk("контроль: той самий конкурент з ОДНАКОВИМ числовим токеном → збіг приймається (delist)", dd_ok["action"] == "delist" and not dd_ok["strict_rejected"])
dd_bb, _r = rt_reprice.decide_one(P, None, FakeNet({"Качечка": bb(100.0)}))
chk("buyBox строгим гейтом не відсікається (його «назва» службова)", dd_bb["action"] == "delist" and not dd_bb["strict_rejected"])

# ───────────────────────── СЕРЕДНЯ-5: ціна ≤ default; СЕРЕДНЯ-7: min_safe-мутація ─────────────────────────
dd_exp, _r = rt_reprice.decide_one(P, None, FakeNet({"Качечка": bb(1000.0)}))
chk("СЕРЕДНЯ-5: «дорогий» (хибний) конкурент 1000 → ціна рішення = default 210 (cap), а не 997; capped_default=True", dd_exp["price"] == default_p and dd_exp.get("capped_default") is True)
e_exp = {"price": 997.0, "timestamp": (NOW - timedelta(hours=1)).isoformat(), "action": "adjust", "competitor_price": 1000.0}
chk("СЕРЕДНЯ-5: feed_price: запис 997 зі свіжим конкурентом 1000 → у фіді ≤ default (210); floor-гард теж не підіймає вище default", fp(e_exp) == (default_p, ""))
e_cost_up = {"price": 50.0, "timestamp": (NOW - timedelta(hours=1)).isoformat(), "action": "adjust", "competitor_price": 20.0}
good, _ = fp(e_cost_up)
import inspect  # noqa: E402
src = inspect.getsource(rt_reprice.feed_price)
mut_line = "floor = max(min(floor, default_price), min_safe)"
assert mut_line in src
ns = dict(rt_reprice.__dict__)
exec(src.replace(mut_line, "floor = min(floor, default_price)"), ns)
bad_price, _ = ns["feed_price"]("rt-1", COST, "Тест-Машини", 2606, default_p, ctx(e_cost_up))
comm_g = cp.compute_total_commission("prom", "Тест-Машини", good, prom_category_id=2606)
comm_b = cp.compute_total_commission("prom", "Тест-Машини", bad_price, prom_category_id=2606)
chk(f"СЕРЕДНЯ-7: min_safe-клемп у feed_price ЛОВИТЬСЯ тестом: з клемпом net {good * (1 - comm_g):.2f} ≥ cost_eff {cost_eff:.2f}; МУТАНТ без клемпа дає net {bad_price * (1 - comm_b):.2f} < cost_eff",
    good * (1 - comm_g) >= cost_eff - 0.01 and bad_price * (1 - comm_b) < cost_eff)

# ───────────────────────── БЛОК-2: пул = емісія ─────────────────────────
def mk(n, prefix="rt-2", cost_base=100, step=7):
    sup_ = {f"{prefix}{i:02d}": rt_item(f"Іграшка серія {i} вид", cost=cost_base + i * step) for i in range(n)}
    sel_ = dict(selection, rt_n=n, items={k: si() for k in sup_})
    return sup_, sel_


big_sup, big_sel = mk(40)
rt_prom._atomic_write(rt_prom.SUPPLIER_FILE, {"updated": NOW.isoformat(timespec="seconds"), "items": big_sup})
fresh_files()
pool_big = rt_prom.repricer_pool(live, big_sel, big_sup)
chk("пул ≡ порядок емісії: 40 кандидатів, пул порядкується _rank_key (профіт за дефолтною ціною спадно, id)", len(pool_big) == 40 and list(pool_big) == sorted(pool_big, key=lambda k: (-pool_big[k]["profit"], k)))
adj = {k: {"price": 100.0, "timestamp": (NOW - timedelta(hours=2)).isoformat(), "action": "adjust", "competitor_price": 105.0} for k in big_sup}      # ціни зі стану навмисно «перемішують» профіт
R = 12
root_o = base_root(3)
rt_prom.append_rt_offers(root_o, live, big_sel, big_sup, 3 + R, set(), save_state=False, reprice={"items": adj, "now": NOW})
em = [o.get("id") for o in root_o.iter("offer") if o.get("id", "").startswith("rt-") and o.get("available") == "true"]
chk(f"БЛОК-2: ємність {R} < пулу 40: емісія = ПЕРШІ {R} пулу (той самий порядок; ціни зі стану порядок не міняють)", set(em) == set(list(pool_big)[:R]) and len(em) == R)
root_o2 = base_root(3)
rt_prom.append_rt_offers(root_o2, live, big_sel, big_sup, 3 + R, set(), save_state=False)
em2 = [o.get("id") for o in root_o2.iter("offer") if o.get("id", "").startswith("rt-") and o.get("available") == "true"]
chk("БЛОК-2: без брами (reprice=None) емісія — ті самі перші 12 пулу", set(em2) == set(em))
known20 = list(pool_big)[:20]
write_journal(known20)
nc = known20[:5]
state20 = {k: dict(adj[k]) for k in known20[5:]}
state20.update({k: {"price": 100.0, "timestamp": (NOW - timedelta(hours=2)).isoformat(), "action": "delist", "competitor_price": 90.0} for k in nc})
root20 = base_root(3)
st20 = rt_prom.append_rt_offers(root20, live, big_sel, big_sup, 3 + 20, set(), save_state=False, reprice={"items": state20, "now": NOW})
em20 = {o.get("id") for o in root20.iter("offer") if o.get("id", "").startswith("rt-") and o.get("available") == "true"}
chk("БЛОК-2 (вимога): ємність 20, 5 неконкурентних із 20 існуючих → активних 15, НОВИХ неперевірених карток 0 (skip_new_unchecked=20), 5 осиротілих-заглушок займають слоти", em20 == set(known20[5:]) and st20["skip_new_unchecked"] == 20 and st20["orphans"] == 5 and st20["emitted"] == 15)
pool20 = rt_prom.repricer_pool(live, big_sel, big_sup, reprice={"items": state20, "now": NOW})
chk("БЛОК-2: 20 нових (без запису) ВХОДЯТЬ у пул (їх спершу треба перевірити), 5 свіжо-неконкурентних — ні (переперевірка раз на 7 діб)", len(pool20) == 35 and not (set(nc) & set(pool20)) and set(list(pool_big)[20:]) <= set(pool20))
rt_prom.DELETED_FILE = tmp / "deleted.json"
rt_prom._atomic_write(rt_prom.DELETED_FILE, {"v": 1, "deleted": {known20[0]: NOW.isoformat(timespec="seconds"), known20[1]: (NOW - timedelta(days=20)).isoformat(timespec="seconds")}})
pool_t = rt_prom.repricer_pool(live, big_sel, big_sup)
root_t = base_root(3)
st_t = rt_prom.append_rt_offers(root_t, live, big_sel, big_sup, 3 + 40, set(), save_state=False)
em_t = {o.get("id") for o in root_t.iter("offer") if o.get("id", "").startswith("rt-") and o.get("available") == "true"}
chk("СЕРЕДНЯ-6: надгробок cleanup (свіжий) виключає id з пулу ТАК САМО, як з емісії; прострочений (>14 діб, TTL #2012) — id знову в обох",
    known20[0] not in pool_t and known20[0] not in em_t and st_t["skip_deleted_by_cleanup"] == 1 and known20[1] in pool_t and known20[1] in em_t and set(pool_t) == em_t)
rt_prom.DELETED_FILE = tmp / "no_deleted.json"
rt_prom._atomic_write(rt_prom.SUPPLIER_FILE, {"updated": NOW.isoformat(timespec="seconds"), "items": sup_items})

# ───────────────────────── перезапуск після TTL; битий/відсутній стан ─────────────────────────
fresh_files()
write_journal(ALL7)
old_del = dict(e_del, timestamp=(NOW - timedelta(days=46)).isoformat())
_t, st_ttl, root_ttl = feed_text({"items": {"rt-1": old_del}, "now": NOW})
p_ttl = {o.get("id"): float(o.findtext("price")) for o in root_ttl.iter("offer") if o.get("id", "").startswith("rt-") and o.get("available") == "true"}
chk("перезапуск після TTL: delist-запис 46 діб (недійсний) → існуюча картка rt-1 емітується за дефолтною ціною", p_ttl.get("rt-1") == default_p)
chk("pick_batch: delist-запис ≥7 діб береться на переперевірку, <7 діб — ні", rt_reprice.pick_batch(["rt-1", "rt-2"], {"rt-1": old_del, "rt-2": dict(e_del)}, 10, NOW) == ["rt-1"])
rt_reprice.STATE_FILE.write_text("{не json", encoding="utf-8")
ctx_bad = rt_reprice.feed_context(NOW)
t_bad, st_bad, _ = feed_text(ctx_bad)
chk("битий стан-файл (без .bak) → порожній стан: існуючі картки лишаються за дефолтною ціною, фід не падає", ctx_bad["items"] == {} and st_bad["emitted"] == 7)
rt_reprice.STATE_FILE.unlink()
t_miss, st_miss, _ = feed_text(rt_reprice.feed_context(NOW))
chk("відсутній стан-файл → те саме (емітуються існуючі)", st_miss["emitted"] == 7)
_t2, st_miss2, _ = feed_text(rt_reprice.feed_context(NOW), known=[])
chk("відсутній стан-файл + порожній журнал → нових карток не створюємо (0)", st_miss2.get("emitted", 0) == 0)

# ───────────────────────── НИЗЬКІ-8: стан старший N год / битий / відсутній → ⚠️-алерт ─────────────────────────
alerts = []
_orig_alert = rt_reprice._alert
rt_reprice._alert = lambda key, text: alerts.append((key, text))
fresh_files()
chk("check_state_health: стану нема → алерт state_missing", rt_reprice.check_state_health(NOW) == "missing" and alerts[-1][0] == "state_missing")
rt_reprice.save_state({"v": 1, "items": {}, "links": {}, "_meta": {"last_run": (NOW - timedelta(hours=40)).isoformat()}})
chk("check_state_health: стан старший 36 год → алерт state_stale", rt_reprice.check_state_health(NOW) == "stale" and alerts[-1][0] == "state_stale")
rt_reprice.save_state({"v": 1, "items": {}, "links": {}, "_meta": {"last_run": (NOW - timedelta(hours=3)).isoformat()}})
n_al = len(alerts)
chk("check_state_health: свіжий стан → без алерту", rt_reprice.check_state_health(NOW) == "" and len(alerts) == n_al)
rt_reprice.STATE_FILE.write_text("{биттий", encoding="utf-8")
_, stt = rt_reprice.load_state_status()
chk("битий стан із .bak → відновлено (restored; резервна копія старша → здоровий стан тоді 'stale', але НЕ state_lost)", stt == "restored" and rt_reprice.check_state_health(NOW) == "stale" and alerts[-1][0] == "state_stale")
rt_reprice._backup_path().unlink()
chk("битий стан БЕЗ .bak → алерт state_lost", rt_reprice.check_state_health(NOW) == "lost" and alerts[-1][0] == "state_lost")
res_l = run(FakeNet({}), 0, apply=False)
chk("run_phase при втраченому стані теж сигналізує (state_lost) і продовжує з порожнього", alerts[-1][0] == "state_lost" and res_l["checked"] == 7)
rt_reprice._alert = _orig_alert
import telegram_notify as tn  # noqa: E402
sent = []
_o_thr = tn.send_throttled_alert
tn.send_throttled_alert = lambda k, t, cooldown_sec=0: sent.append((k, t)) or True
rt_reprice._alert("x", "перевірка")
tn.send_throttled_alert = _o_thr
chk("_alert: текст починається з «⚠️ rt_reprice:» (категорія warning для health_check, як #2017), ключ із префіксом rt_reprice_", bool(sent) and sent[0][1].startswith("⚠️ rt_reprice:") and sent[0][0] == "rt_reprice_x")

# ───────────────────────── НИЗЬКІ-10: бюджет усередині _resolve_links ─────────────────────────
import time  # noqa: E402


class SlowNet(FakeNet):
    def __init__(self):
        super().__init__({})
        self.fetch_calls = []
        self.fetch_by_ext_ids = lambda ids: (self.fetch_calls.append(set(ids)) or ({i: {"id": 1} for i in ids}, set()))
        self.resolve_url_text = lambda pid: (time.sleep(0.06), "slug")[1]


from collections import Counter  # noqa: E402
sn = SlowNet()
lk, stt2 = {}, Counter()
rt_reprice._resolve_links(["rt-1", "rt-2", "rt-3"], lk, {"rt-1": {}, "rt-2": {}, "rt-3": {}}, sn, NOW.isoformat(), NOW, stt2, deadline=time.monotonic() - 1)
chk("бюджет: deadline уже минув → _resolve_links не робить жодного запиту", sn.fetch_calls == [] and lk == {})
sn2 = SlowNet()
lk2, stt3 = {}, Counter()
rt_reprice._resolve_links(["rt-1", "rt-2", "rt-3"], lk2, {"rt-1": {}, "rt-2": {}, "rt-3": {}}, sn2, NOW.isoformat(), NOW, stt3, deadline=time.monotonic() + 0.05)
chk("бюджет: deadline спливає посеред резолву → зупинка (link_budget_stop), частину збережено", stt3["link_budget_stop"] == 1 and 1 <= len(lk2) < 3)

# ───────────────────────── помилки, пропуски ─────────────────────────
class BoomNet(FakeNet):
    def find_best_competitor(self, name, cost, own_link, pictures):
        if name.startswith("Друга"):
            raise RuntimeError("boom")
        return None


fresh_files()
res_g = run(BoomNet({}), 0)
chk("збій одного rt-id не валить партію (error=1, решта 6 записано)", res_g["error"] == 1 and res_g["checked"] == 6 and "rt-2" not in rt_reprice.load_state()["items"])
fresh_files()
chk("усічений каталог → skipped=catalog", rt_reprice.run_phase({"1": {}}, apply=True, net=FakeNet({}), now=NOW)["skipped"] == "catalog")
rt_prom._atomic_write(rt_prom.SEL_FILE, dict(selection, built_at=(NOW - timedelta(hours=40)).isoformat(timespec="seconds")))
chk("відбір старший 36 год → skipped=selection_stale", run(FakeNet({}), 0)["skipped"] == "selection_stale")
rt_prom._atomic_write(rt_prom.SEL_FILE, selection)
rt_prom._atomic_write(rt_prom.SUPPLIER_FILE, {"updated": (NOW - timedelta(hours=9)).isoformat(timespec="seconds"), "items": sup_items})
chk("supplier_rt.json старший 6 год → skipped=supplier_stale", run(FakeNet({}), 0)["skipped"] == "supplier_stale")
rt_prom._atomic_write(rt_prom.SUPPLIER_FILE, {"updated": NOW.isoformat(timespec="seconds"), "items": sup_items})
chk("бюджет Toysi вичерпано → skipped=toysi_budget", rt_reprice.run_phase(live, apply=True, net=FakeNet({}), now=NOW, time_exceeded=lambda: True) == {"skipped": "toysi_budget"})
calls_n = {"n": 0}


def exceeded_after_two():
    calls_n["n"] += 1
    return calls_n["n"] > 3


fresh_files()
res_f = rt_reprice.run_phase(live, apply=True, net=FakeNet({}), now=NOW, time_exceeded=exceeded_after_two)
chk("зупинка за бюджетом посеред партії: перевірено частину, budget_stop=1, зроблене збережено", res_f.get("budget_stop") == 1 and 0 < res_f["checked"] < 7 and len(rt_reprice.load_state()["items"]) == res_f["checked"])
os.environ.pop("PROM_RT_IN_FEED")
chk("PROM_RT_IN_FEED вимкнено → skipped=rt_not_in_feed", run(FakeNet({}), 0)["skipped"] == "rt_not_in_feed")
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
chk("estimate (реалістично: 2,52 + 2×0,4 + verify 1,0 = 4,32 с/SKU): партія 300 ≈ 21,6 хв ≤ бюджет фази 25 хв; 1500 rt-id ≈ 108 хв; 5 прогонів/партія 300 покривають пул 1500", abs(rt_reprice.SEC_PER_SKU_REALISTIC - 4.32) < 1e-9 and abs(rt_reprice.estimate_minutes(300) - 21.6) < 0.01 and rt_reprice.estimate_minutes(rt_reprice.BATCH_DEFAULT) * 60 <= rt_reprice.RUN_BUDGET_S and rt_reprice.BATCH_DEFAULT * 5 >= 1500)
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

# ───────────────────────── СЕРЕДНЯ-7: повний __main__ фази (_cli_main) ─────────────────────────
order = []
_om, _op = pcp.main, pcp._run_rt_reprice_phase
pcp.main = lambda: order.append("main")
pcp._run_rt_reprice_phase = lambda: order.append("phase")
pcp._cli_main()
chk("_cli_main (те, що виконує `python prom_competitor_pricer.py`): спершу Toysi-main(), потім rt-фаза", order == ["main", "phase"])
order.clear()


def _exit1():
    order.append("main")
    raise SystemExit(1)


pcp.main = _exit1
try:
    pcp._cli_main()
    exited = False
except SystemExit as e:
    exited = e.code == 1
chk("_cli_main: SystemExit(1) від main (circuit breaker/каталог) → rt-фаза НЕ запускається, код виходу Toysi збережено", exited and order == ["main"])
pcp.main, pcp._run_rt_reprice_phase = _om, _op
src_main = inspect.getsource(pcp).split('if __name__ == "__main__":')[-1]
chk("блок `if __name__ == \"__main__\"` викликає лише _cli_main()", src_main.strip() == "_cli_main()")

# ═════════════════════════ ПОВТОРНИЙ АУДИТ #2015: доробка ═════════════════════════
# (1) БЛОКУЮЧА: check-before-create не обходиться першим delist-рішенням
os.environ["PROM_RT_IN_FEED"] = "1"
os.environ["PROM_RT_REPRICE"] = "1"
fresh_files()
e_pending = dict(e_fresh, action="adjust", streak=1, streak_at=(NOW - timedelta(hours=2)).isoformat(), price=default_p, competitor_price=100.0)
chk("(1) new_card_checked: запис adjust із streak=1 (перше сире delist) → НОВУ картку не пускає; streak=0 → пускає; delist → ні",
    not rt_reprice.new_card_checked("rt-1", {"items": {"rt-1": e_pending}, "now": NOW}, 48.0) and rt_reprice.new_card_checked("rt-1", {"items": {"rt-1": dict(e_pending, streak=0)}, "now": NOW}, 48.0)
    and not rt_reprice.new_card_checked("rt-1", {"items": {"rt-1": dict(e_pending, action="delist", streak=2)}, "now": NOW}, 48.0))
_t, st_p, root_p = feed_text({"items": {"rt-1": e_pending, "rt-2": dict(e_pending, streak=0), "rt-3": dict(e_pending, streak=0)}, "now": NOW}, known=[])
ids_p = sorted(o.get("id") for o in root_p.iter("offer") if o.get("id", "").startswith("rt-") and o.get("available") == "true")
chk("(1) НОВА картка після 1-го delist НЕ емітується (rt-1, streak=1); після стану без серії (rt-2, rt-3) — емітується", ids_p == ["rt-2", "rt-3"] and st_p["skip_new_unchecked"] >= 1)
_t, st_p2, root_p2 = feed_text({"items": {"rt-1": e_pending}, "now": NOW}, known=["rt-1"])
ids_p2 = [o.get("id") for o in root_p2.iter("offer") if o.get("id") == "rt-1" and o.get("available") == "true"]
chk("(1) ІСНУЮЧА активна картка (є в журналі) із streak=1 лишається в емісії (правило лише для нових)", ids_p2 == ["rt-1"])
# наскрізно: run_phase → стан → фід
fresh_files()
net_e = FakeNet(comps)
run(net_e, 0)
ctx_e = rt_reprice.feed_context(NOW + timedelta(hours=1))
_t, st_e, root_e = feed_text(ctx_e, known=[])
ids_e = sorted(o.get("id") for o in root_e.iter("offer") if o.get("id", "").startswith("rt-") and o.get("available") == "true")
chk("(1) наскрізно: після 1-го прогону (rt-3, rt-5 мають сире delist, streak=1) нові картки rt-3/rt-5 НЕ створюються; решта (без серії) — так", "rt-3" not in ids_e and "rt-5" not in ids_e and "rt-1" in ids_e and "rt-2" in ids_e)

# (3) ціна утримується до підтвердження зняття (для вже активних карток)
fresh_files()
prev_price = 187.0
rt_reprice.save_state({"v": 1, "items": {"rt-3": {"price": prev_price, "timestamp": (NOW - timedelta(hours=30)).isoformat(), "action": "adjust", "streak": 0, "category": "undercut", "competitor_price": 190.0}}, "links": {}, "_meta": {}})
run(FakeNet({"Третя іграшка Модель 3": bb(100.0)}), 0)
e3 = rt_reprice.load_state()["items"]["rt-3"]
chk("(3) активна картка з попередньою ціною 187: перше сире delist → streak=1, ціна ЛИШАЄТЬСЯ 187 (не division-floor 151), маржа перерахована від утримуваної ціни", e3["streak"] == 1 and e3["price"] == prev_price and e3["action"] == "adjust" and e3["margin_pct"] > 10)
_t, _s, root_h = feed_text(rt_reprice.feed_context(NOW + timedelta(hours=1)), known=ALL7)
ph = {o.get("id"): float(o.findtext("price")) for o in root_h.iter("offer") if o.get("id", "").startswith("rt-") and o.get("available") == "true"}
chk("(3) у фіді rt-3 тримає 187 до другого підтвердження", ph["rt-3"] == prev_price)
rt_reprice.save_state({"v": 1, "items": {"rt-3": {"price": 999.0, "timestamp": (NOW - timedelta(hours=30)).isoformat(), "action": "adjust", "streak": 0}}, "links": {}, "_meta": {}})
run(FakeNet({"Третя іграшка Модель 3": bb(100.0)}), 0)
chk("(3) попередня ціна вища за default → утримуємо min(попередня, default)=210", rt_reprice.load_state()["items"]["rt-3"]["price"] == default_p)

# (4) симетрія: повернення теж через 2 підтвердження ≥20 год
fresh_files()
t_del = (NOW - timedelta(days=8)).isoformat()
rt_reprice.save_state({"v": 1, "items": {"rt-3": {"price": 151.0, "timestamp": t_del, "action": "delist", "streak": 2, "status_at": t_del, "category": "floor", "competitor_price": 100.0}}, "links": {}, "_meta": {}})
net_r = FakeNet({})
run(net_r, 0)
r1 = rt_reprice.load_state()["items"]["rt-3"]
chk("(4) повернення: 1-ше adjust після 8 діб → ще action=delist (ret_streak=1, held), у фід НЕ повертається", r1["action"] == "delist" and r1["ret_streak"] == 1 and rt_reprice.feed_price("rt-3", COST, "Тест-Машини", 2606, default_p, {"items": {"rt-3": r1}, "now": NOW}) == (None, "noncompetitive"))
chk("(4) очікуване 2-ге підтвердження переперевіряється не через 7 діб, а через ≥20 год (pick_batch): +10 год — ні, +21 год — так",
    rt_reprice.pick_batch(["rt-3"], {"rt-3": r1}, 5, NOW + timedelta(hours=10)) == [] and rt_reprice.pick_batch(["rt-3"], {"rt-3": r1}, 5, NOW + timedelta(hours=21)) == ["rt-3"])
net_r.calls.clear()
run(net_r, 21)
r2 = rt_reprice.load_state()["items"]["rt-3"]
chk("(4) 2-ге adjust через 21 год → картка ПОВЕРНУТА (action=adjust, ret_streak обнулено)", r2["action"] == "adjust" and r2["ret_streak"] == 0 and "Третя іграшка Модель 3" in net_r.calls)
rr = rt_reprice.advance({"action": "delist", "streak": 2, "status_at": t_del, "ret_streak": 1, "ret_at": NOW.isoformat()}, "delist", NOW + timedelta(hours=25))
chk("(4) delist-рішення під час очікування повернення скидає ret_streak (картка лишається знятою)", rr["action"] == "delist" and rr["ret_streak"] == 0)

# (2) ротація observe без стану; --dry --offset; (5) ліміт розміру журналу
big_sup2, big_sel2 = mk(40)
rt_prom._atomic_write(rt_prom.SEL_FILE, big_sel2)
rt_prom._atomic_write(rt_prom.SUPPLIER_FILE, {"updated": NOW.isoformat(timespec="seconds"), "items": big_sup2})
fresh_files()
rt_reprice.OBSERVE_FILE = tmp / "observe_rot.jsonl"
os.environ["PROM_RT_REPRICE"] = "observe"
os.environ["PROM_RT_REPRICE_BATCH"] = "10"
for k in range(3):
    rt_reprice.run_phase(live, apply=True, net=FakeNet({}), now=NOW + timedelta(hours=6 * k))
seen_ids = [json.loads(x)["id"] for x in rt_reprice.OBSERVE_FILE.read_text(encoding="utf-8").splitlines()]
chk("(2) observe БЕЗ стану: 3 прогони по 10 з пулу 40 → 30 РІЗНИХ id (ротація за observe-журналом, не ті самі 10 зверху)", len(seen_ids) == 30 and len(set(seen_ids)) == 30 and not rt_reprice.STATE_FILE.exists())
rt_reprice.run_phase(live, apply=True, net=FakeNet({}), now=NOW + timedelta(hours=18))
seen4 = [json.loads(x)["id"] for x in rt_reprice.OBSERVE_FILE.read_text(encoding="utf-8").splitlines()]
chk("(2) 4-й прогін добирає останні 10 ще не переглянутих → усі 40 покриті", len(set(seen4)) == 40)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rt_reprice.main(["--dry", "--limit", "5"], net=FakeNet({}), catalog=live, now=NOW + timedelta(hours=30))
dry_rot = buf.getvalue()
pool40 = rt_prom.repricer_pool(live, big_sel2, big_sup2)
first_run = {pool40[i]["name"] for i in seen_ids[:10]}
last_run = {pool40[i]["name"] for i in seen4[30:40]}
chk("(2) `--dry` без offset ротується за observe-журналом: бере НАЙДАВНІШЕ переглянуті (5 із першої партії), жодного з щойно переглянутої; нічого не пише",
    sum(1 for n in first_run if n in dry_rot) == 5 and not any(n in dry_rot for n in last_run))
size_before = rt_reprice.OBSERVE_FILE.stat().st_size
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rt_reprice.main(["--dry", "--limit", "5", "--offset", "10"], net=FakeNet({}), catalog=live, now=NOW)
off_names = [pool40[k]["name"] for k in list(pool40)[10:15]]
chk("(2) `--dry --offset 10 --limit 5` показує РІВНО rt-id пулу [10:15]; файл observe не змінено", all(n in buf.getvalue() for n in off_names) and rt_reprice.OBSERVE_FILE.stat().st_size == size_before and not rt_reprice.STATE_FILE.exists())
# (5) ліміт розміру
rt_reprice.OBSERVE_FILE = tmp / "observe_cap.jsonl"
rt_reprice.OBSERVE_MAX_LINES = 25
for k in range(6):
    rt_reprice.run_phase(live, apply=True, net=FakeNet({}), now=NOW + timedelta(hours=6 * k))
n_cur = len(rt_reprice.OBSERVE_FILE.read_text(encoding="utf-8").splitlines())
p1 = rt_reprice.OBSERVE_FILE.with_suffix(rt_reprice.OBSERVE_FILE.suffix + ".1")
chk("(5) observe-журнал обмежено: після 60 записів при ліміті 25 → поточний ≤25 рядків, попередня генерація в .jsonl.1, рядки не губляться (≤2 генерації)", n_cur <= 25 and p1.exists() and len(p1.read_text(encoding="utf-8").splitlines()) <= 25)
chk("(5) observe_seen читає й .1 (історія для ротації не втрачена): бачено > ніж у поточному файлі", len(rt_reprice.observe_seen()) > len({json.loads(x)["id"] for x in rt_reprice.OBSERVE_FILE.read_text(encoding="utf-8").splitlines()}))
rt_reprice.OBSERVE_MAX_LINES = 50_000
os.environ.pop("PROM_RT_REPRICE_BATCH")
rt_prom._atomic_write(rt_prom.SEL_FILE, selection)
rt_prom._atomic_write(rt_prom.SUPPLIER_FILE, {"updated": NOW.isoformat(timespec="seconds"), "items": sup_items})

# невідоме значення брами → ⚠️-алерт
al2 = []
_oa = rt_reprice._alert
rt_reprice._alert = lambda key, text: al2.append((key, text))
res_vals = {}
for val in ("true", "yes", "ON", "2", "observe", "1", "", "0", " OBSERVE "):
    al2.clear()
    os.environ["PROM_RT_REPRICE"] = val
    res_vals[val] = (rt_reprice.warn_unknown_gate(), bool(al2))
chk("невідоме значення PROM_RT_REPRICE (true/yes/ON/2) → ⚠️-алерт gate_value і mode()=off; дозволені 1/observe/порожньо/0 (регістр і пробіли ігноруються) — без алерту",
    all(res_vals[v] == (True, True) for v in ("true", "yes", "ON", "2")) and all(res_vals[v] == (False, False) for v in ("observe", "1", "", "0", " OBSERVE ")))
al2.clear()
os.environ["PROM_RT_REPRICE"] = "true"
pcp._RT_CTX.clear()
pcp._RT_CTX.update({"catalog": catalog, "apply": True})
with contextlib.redirect_stderr(io.StringIO()):
    pcp._run_rt_reprice_phase()
chk("хук фази ловить невідоме значення (⚠️ gate_value) і НЕ запускає фазу", al2 and al2[0][0] == "gate_value" and not rt_reprice.STATE_FILE.exists())
al2.clear()
os.environ["PROM_RT_IN_FEED"] = "1"
os.environ["PROM_RT_REPRICE"] = "yes"
write_journal(ALL7)
rt_prom.maybe_add_rt_offers(base_root(), live, {}, 6000)
chk("фід-гейт теж сигналізує про невідоме значення (maybe_add_rt_offers → ⚠️ gate_value) і лишається без репрайсингу", any(k == "gate_value" for k, _t in al2))
rt_reprice._alert = _oa
pcp._RT_CTX.clear()
os.environ.pop("PROM_RT_REPRICE", None)
os.environ.pop("PROM_RT_IN_FEED", None)

if F:
    print(f"\nFAIL: {len(F)}")
    sys.exit(1)
print("\nOK: усі перевірки пройдені")
