# -*- coding: utf-8 -*-
"""delisted-коло Prom (історія: #97 → #98 → 0175329 → #255): позначка = факт історії, повернення з грацією, Фаза 1 (never-created),
політика Фази 2 (за замовчуванням off), ротація черги за _recheck_at (мертві не їдять партію), лічильники «чому нуль».
Запуск: python test_delisted_readd.py  (офлайн, без мережі й Telegram)."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ.setdefault("PROM_API_KEY", "x")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import competitor_pricing as cp
import prom_competitor_pricer as pp

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

T0 = datetime(2026, 10, 8, 12, 0, 0)
iso = lambda dt: dt.isoformat()

# ── 1. effective_delisted: позначка — факт історії, не перемикач ──
st = {"_delisted_since": {"1": iso(T0 - timedelta(days=5)), "2": iso(T0 - timedelta(days=5)), "3": iso(T0 - timedelta(days=5))},
      "_readded_at": {"2": iso(T0 - timedelta(days=1)), "3": iso(T0 - timedelta(days=9))}}
eff = cp.effective_delisted(st)
chk("effective_delisted: 1 виключений; 2 повернений (readded новіше за позначку); 3 — readded СТАРІШЕ за позначку → знову виключений", set(eff) == {"1", "3"})
chk("effective_delisted: _delisted_since НЕ мутується (історія лишається)", set(st["_delisted_since"]) == {"1", "2", "3"})
chk("effective_delisted: порожній/відсутній стан не падає", cp.effective_delisted({}) == {} and cp.effective_delisted(None) == {})

# ── 2. _select_recheck_batch: мертві не їдять партію; ротація за _recheck_at ──
def it(stock=5, price=100.0):
    return {"id": "x", "name": "n", "price": price, "stock": stock, "category_name": "Різне", "pictures": []}
cat = {str(i): it() for i in range(1, 11)}
cat["11"] = it(stock=0)
delisted = {str(i): iso(T0 - timedelta(days=100 - i)) for i in range(1, 14)}      # 12, 13 — нема в каталозі; 11 — склад 0
batch1, c1 = pp._select_recheck_batch(delisted, {}, cat, 4)
chk("партія: мертві (нема в каталозі ×2, склад 0 ×1) відкинуті ДО зрізу й пораховані; партія повна з живих", len(batch1) == 4 and c1["dead_not_in_catalog"] == 2 and c1["dead_stock0"] == 1 and c1["alive"] == 10)
recheck = {p: iso(T0)[:16] for p in batch1}
batch2, _ = pp._select_recheck_batch(delisted, recheck, cat, 4)
chk("ротація: наступна партія — ІНШІ pid (перевірені йдуть у кінець черги)", not (set(batch1) & set(batch2)) and len(batch2) == 4)
recheck.update({p: iso(T0)[:16] for p in batch2})
batch3, _ = pp._select_recheck_batch(delisted, recheck, cat, 4)
chk("ротація: за три партії покрито всі 10 живих (жоден не голодує)", len(set(batch1) | set(batch2) | set(batch3)) == 10)
batch4, _ = pp._select_recheck_batch(delisted, {}, cat, 3, scan_hint=lambda p: p in {"9", "10"})
chk("scan_hint: при однаковій давності спершу ті, кому скан каже «не floor»", set(batch4[:2]) == {"9", "10"})

# ── 3. _classify_recheck_candidate: три стани, які гейт 60c91aa змішував ──
pol_off = {"ever_live": "off", "canary_pids": set()}
pol_can = {"ever_live": "canary", "canary_pids": {"777"}}
pol_all = {"ever_live": "all", "canary_pids": set()}
cl = pp._classify_recheck_candidate
chk("живий на Prom (200, не deleted) → знімаємо", cl("1", {"1": {"status": "on_display"}}, set(), {}, set(), pol_off) == (True, "cleared_live"))
chk("запис status=deleted → тримаємо (джерело 688 помилок)", cl("1", {"1": {"status": "deleted"}}, set(), {}, set(), pol_all) == (False, "kept_status_deleted"))
chk("мережа (indeterminate) → тримаємо навіть із політикою all", cl("1", {}, {"1"}, {}, set(), pol_all) == (False, "kept_indeterminate"))
chk("ФАЗА 1: відсутній, ніколи не створювався (нема в ever_live і в журналі) → знімаємо без гейту живості", cl("1", {}, set(), {}, set(), pol_off) == (True, "cleared_never_created"))
chk("ФАЗА 2 off: відсутній, але ever_live → тримаємо (код приїхав, вмикається після тесту)", cl("1", {}, set(), {"1": "x"}, set(), pol_off) == (False, "kept_ever_live_flag_off"))
chk("ФАЗА 2 off: відсутній, у журналі штовхнутих → тримаємо", cl("1", {}, set(), {}, {"1"}, pol_off) == (False, "kept_ever_live_flag_off"))
chk("ФАЗА 2 canary: знімаємо ТІЛЬКИ pid із canary_pids, решту ever_live — ні",
    cl("777", {}, set(), {"777": "x"}, set(), pol_can) == (True, "cleared_ever_live_flag") and cl("778", {}, set(), {"778": "x"}, set(), pol_can) == (False, "kept_ever_live_flag_off"))
chk("ФАЗА 2 all: знімаємо будь-який ever_live", cl("1", {}, set(), {"1": "x"}, set(), pol_all) == (True, "cleared_ever_live_flag"))
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "pol.json"
    p.write_text(json.dumps({"ever_live": "canary", "canary_pids": [777, "778"]}), encoding="utf-8")
    pc = pp._load_readd_policy(p)
    chk("політика: файл читається (canary_pids → рядки)", pc["ever_live"] == "canary" and pc["canary_pids"] == {"777", "778"})
    chk("політика: відсутній/битий файл → off", pp._load_readd_policy(Path(d) / "нема.json")["ever_live"] == "off" and (p.write_text("{x", encoding="utf-8") or pp._load_readd_policy(p)["ever_live"] == "off"))
chk("політика ЗА ЗАМОВЧУВАННЯМ у репо — off (Фаза 2 вимкнена до результату тесту)", pp._load_readd_policy()["ever_live"] == "off")

# ── 4. _recheck_delisted_pids end-to-end (мережа замінена) ──
import prom_catalog_sync as pcs
import prom_pushed_ledger as ledger
class FakeCompetitor:
    pass
def run_recheck(price_state, cat_, comp_price, found, pushed=frozenset(), policy=None, limit=50, now=T0, scan_state=None):
    pp.find_best_competitor = lambda name, cost, link, pics: ({"price": comp_price(name)} if comp_price(name) is not None else None)
    pp.SEARCH_DELAY = 0
    pcs.fetch_prom_products_by_external_ids = lambda ids: ({p: found[p] for p in ids if p in found}, set())
    ledger.load_ledger = lambda: set(pushed)
    if policy is not None:
        pp._load_readd_policy = lambda path=None: policy
    sent = []
    pp.send_telegram_message = sent.append
    n = pp._recheck_delisted_pids(price_state["_delisted_since"], cat_, {}, {}, {}, limit=limit, price_state=price_state, scan_state=scan_state or {}, now=now)
    return n, sent

def mk_cat(n, name_prefix="Т"):
    return {str(i): {"id": str(i), "name": f"{name_prefix}{i}", "price": 100.0, "stock": 5, "category_name": "Різне", "pictures": []} for i in range(1, n + 1)}

cat4 = mk_cat(6)
state = {"_delisted_since": {str(i): iso(T0 - timedelta(days=30)) for i in range(1, 7)}, "_ever_live": {"3": "x", "4": "x"}, "_meta": {}}
# 1,2 → never-created, конкурентні; 3 → ever_live, конкурентний (policy off → тримаємо); 4 → ever_live живий на Prom; 5 → конкурент нижче за floor; 6 → status deleted
comp = lambda nm: {"Т5": 1.0}.get(nm, 10000.0)           # Т5 — конкурент за 1 грн (< floor), інші — далеко вище
n, sent = run_recheck(state, cat4, comp, found={"4": {"status": "on_display"}, "6": {"status": "deleted"}}, policy=pol_off)
m = state["_meta"]["delisted_recheck"]
chk("recheck: знято 3 (pid 1,2 — Фаза 1; 4 — живий); 3 тримається (ever_live, політика off); 5 — floor; 6 — status=deleted", n == 3 and set(state["_readded_at"]) == {"1", "2", "4"})
chk("recheck: _delisted_since НЕ мутовано — позначка лишається фактом історії", set(state["_delisted_since"]) == {"1", "2", "3", "4", "5", "6"})
chk("recheck: effective_delisted після — лишились 3, 5, 6", set(cp.effective_delisted(state)) == {"3", "5", "6"})
chk("recheck: _recheck_at проставлено на кожному перевіреному", set(state["_recheck_at"]) == {"1", "2", "3", "4", "5", "6"})
chk("лічильники «чому нуль»: перевірено 6; floor 1; кандидатів 5; Ф1 2; живі 1; ever_live off 1; status=deleted 1",
    m["checked"] == 6 and m["competitor_floor"] == 1 and m["candidates"] == 5 and m["cleared_never_created"] == 2 and m["cleared_live"] == 1 and m["kept_ever_live_flag_off"] == 1 and m["kept_status_deleted"] == 1)
# політика canary: pid 3 знімається
state = {"_delisted_since": {str(i): iso(T0 - timedelta(days=30)) for i in range(1, 7)}, "_ever_live": {"3": "x", "4": "x"}, "_meta": {}}
n, _ = run_recheck(state, cat4, comp, found={"4": {"status": "on_display"}, "6": {"status": "deleted"}}, policy={"ever_live": "canary", "canary_pids": {"3"}})
chk("recheck + canary: pid 3 (ever_live) повернено за політикою", "3" in state["_readded_at"] and state["_meta"]["delisted_recheck"]["cleared_ever_live_flag"] == 1)
# кап повернень «відсутніх на Prom»
pp.READD_CLEAR_CAP_PER_RUN = 2
state = {"_delisted_since": {str(i): iso(T0 - timedelta(days=30)) for i in range(1, 7)}, "_ever_live": {}, "_meta": {}}
n, _ = run_recheck(state, mk_cat(6), lambda nm: 10000.0, found={}, policy=pol_off)
chk("кап READD_CLEAR_CAP_PER_RUN: повернуто рівно 2 з 6 кандидатів, 4 пораховано як clear_cap_hit", n == 2 and state["_meta"]["delisted_recheck"]["clear_cap_hit"] == 4)
pp.READD_CLEAR_CAP_PER_RUN = 300
# нічого не знято, але перевірено багато → streak і Telegram після 3 поспіль
pp.RECHECK_ZERO_MIN_CHECKED = 3
st0 = {"_delisted_since": {str(i): iso(T0 - timedelta(days=30)) for i in range(1, 5)}, "_ever_live": {str(i): "x" for i in range(1, 5)}, "_meta": {}}
allsent = []
for k in range(3):
    n, sent = run_recheck(st0, mk_cat(4), lambda nm: 10000.0, found={}, policy=pol_off, now=T0 + timedelta(hours=5 * k))
    allsent += sent
chk("«перевірено ≥ N, знято 0» 3 прогони поспіль → ОДИН самодіагностичний Telegram із причиною (ever_live і політика off)",
    len(allsent) == 1 and "ever_live за політикою off 4" in allsent[0] and "prom_readd_policy.json" in allsent[0] and st0["_meta"]["delisted_recheck"]["zero_streak"] == 3)
pp.RECHECK_ZERO_MIN_CHECKED = 100

# ── 5. грація проти ever_live-блоку + облік повернень ──
top = {"10": {}, "11": {}, "12": {}, "13": {}}
ev = {"10": "x", "11": "x", "12": "x"}                      # 13 — never-created
live = set()                                                  # на Prom жодного з них немає
ps = {"_delisted_since": {"10": iso(T0 - timedelta(days=30)), "11": iso(T0 - timedelta(days=30)), "13": iso(T0 - timedelta(days=30))},
      "_readded_at": {"10": iso(T0 - timedelta(hours=10)),      # у грації
                      "11": iso(T0 - timedelta(hours=100)),     # поза грацією (72), ще не відмова (120)
                      "13": iso(T0 - timedelta(hours=130))}}    # never-created, відмова (>120)
to_check, stats = pp._ghost_check_candidates(ps, top, live, ev, now=T0)
chk("грація: pid 10 (повернений 10 год тому) НЕ потрапляє в перевірку привидів", "10" not in to_check)
chk("поза грацією, ever_live, відсутній (pid 11) → перевірка привидів; pid 12 (ever_live, ніколи не повертали) → теж", {"11", "12"} <= set(to_check))
chk("ВІДМОВА: never-created, повернений 130 год тому й так не з'явився (pid 13) → на підтвердження відмови (свіжа позначка), хоча ghost-блок його не торкається", "13" in to_check and stats["giveup_to_confirm"] >= 1)
chk("облік: total 3; у грації чекають 1; не створено після грації 2", stats["total"] == 3 and stats["in_grace_waiting"] == 1 and stats["not_created_after_grace"] == 2)
ps2 = {"_delisted_since": {"10": iso(T0 - timedelta(days=30))}, "_readded_at": {"10": iso(T0 - timedelta(hours=1))}}
_, st2 = pp._ghost_check_candidates(ps2, {"10": {}}, {"10"}, {"10": "x"}, now=T0)
chk("облік: повернений pid, що з'явився на Prom → created_on_prom", st2["created_on_prom"] == 1)
# нова позначка після повернення → знову виключений
ps3 = {"_delisted_since": {"10": iso(T0)}, "_readded_at": {"10": iso(T0 - timedelta(hours=5))}}
chk("нове видалення після повернення (свіжіша позначка) → знову виключений (effective_delisted)", "10" in cp.effective_delisted(ps3))

# ── 6. select_top_items бачить ЕФЕКТИВНІ позначки (повернений SKU знову претендує на вітрину) ──
import generate_prom_feed_top as top_mod
with tempfile.TemporaryDirectory() as d:
    sf = Path(d) / "state.json"
    sf.write_text(json.dumps({"_delisted_since": {"1": iso(T0 - timedelta(days=3)), "2": iso(T0 - timedelta(days=3))}, "_readded_at": {"2": iso(T0 - timedelta(days=1))}}), encoding="utf-8")
    cp.load_prom_price_state = lambda: json.loads(sf.read_text(encoding="utf-8"))
    dl = cp.load_delisted_pids()
    chk("load_delisted_pids (споживач generate_prom_feed_top) віддає лише ефективно виключені: 1 так, 2 (повернений) — ні", set(dl) == {"1"})

# ── 7. структура: політику Фази 2 підключено, лічильники пишуться, redact не ламає нові ключі ──
src = (Path(__file__).resolve().parent / "prom_competitor_pricer.py").read_text(encoding="utf-8")
chk("main викликає recheck із price_state і scan_state та зберігає стан після КОЖНОГО прогону", "price_state=price_state, scan_state=scan_state" in src and "оновлюються на КОЖНОМУ прогоні" in src)
import price_state_redact as psr
red = psr.redact_price_state({"_delisted_since": {"1": "t"}, "_readded_at": {"1": "t2"}, "_recheck_at": {"1": "t3"}, "_meta": {"delisted_recheck": {"checked": 1}}})
chk("redact: нові ключі _readded_at/_recheck_at/_meta.delisted_recheck проходять (в них немає cost/margin)", red["_readded_at"] == {"1": "t2"} and red["_recheck_at"] == {"1": "t3"} and red["_meta"]["delisted_recheck"]["checked"] == 1)

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ delisted-readd — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
