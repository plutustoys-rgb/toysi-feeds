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
chk("живий на Prom (200, не deleted) → знімаємо", cl("1", {"1": {"status": "on_display"}}, set(), [], pol_off) == (True, "cleared_live"))
chk("запис status=deleted → тримаємо (джерело 688 помилок), навіть із політикою all", cl("1", {"1": {"status": "deleted"}}, set(), [], pol_all) == (False, "kept_status_deleted"))
chk("мережа (indeterminate) → тримаємо навіть із політикою all", cl("1", {}, {"1"}, [], pol_all) == (False, "kept_indeterminate"))
chk("ФАЗА 1: відсутній і НІЯКИХ доказів існування → знімаємо без гейту живості", cl("1", {}, set(), [], pol_off) == (True, "cleared_never_created"))
chk("ФАЗА 2 off: відсутній, але є докази існування (будь-який) → тримаємо", all(cl("1", {}, set(), [e], pol_off) == (False, "kept_existed_flag_off") for e in ("ever_live", "ledger", "price_record", "own_link", "category_cache", "readded_before")))
chk("ФАЗА 2 canary: знімаємо ТІЛЬКИ pid із canary_pids, решту існувавших — ні",
    cl("777", {}, set(), ["own_link"], pol_can) == (True, "cleared_ever_live_flag") and cl("778", {}, set(), ["own_link"], pol_can) == (False, "kept_existed_flag_off"))
chk("ФАЗА 2 all: знімаємо будь-який існувавший", cl("1", {}, set(), ["price_record"], pol_all) == (True, "cleared_ever_live_flag"))
# докази існування (аудит #638 B1/B2): _ever_live сліпий до невидимих груп, журнал прунить 404
_ps = {"10": {"price": 55.0}, "11": {"timestamp": "t"}}
ev = pp._existence_evidence
chk("докази: запис ціни в price_state (пише лише apply_price для існуючого товару)", ev("10", _ps, {}, set(), {}, {}, {}) == ["price_record"])
chk("докази: запис без ціни — не доказ", ev("11", _ps, {}, set(), {}, {}, {}) == [])
chk("докази: own_link.prom_id, category_cache, ever_live, журнал, 'вже повертали' — кожен окремо",
    ev("1", {}, {}, set(), {"1": {"prom_id": 5}}, {}, {}) == ["own_link"] and ev("1", {}, {}, set(), {}, {"1": {"category_id": 9}}, {}) == ["category_cache"]
    and ev("1", {}, {"1": "x"}, set(), {}, {}, {}) == ["ever_live"] and ev("1", {}, {}, {"1"}, {}, {}, {}) == ["ledger"] and ev("1", {}, {}, set(), {}, {}, {"1": "t"}) == ["readded_before"])
chk("докази: порожній own_link без prom_id / категорія без category_id — не доказ", ev("1", {}, {}, set(), {"1": {}}, {"1": {}}, {}) == [])
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "pol.json"
    p.write_text(json.dumps({"ever_live": "canary", "canary_pids": [777, "778"]}), encoding="utf-8")
    pc = pp._load_readd_policy(p)
    chk("політика: файл читається (canary_pids → рядки)", pc["ever_live"] == "canary" and pc["canary_pids"] == {"777", "778"})
    chk("політика: відсутній/битий файл → off", pp._load_readd_policy(Path(d) / "нема.json")["ever_live"] == "off" and (p.write_text("{x", encoding="utf-8") or pp._load_readd_policy(p)["ever_live"] == "off"))
_pol_repo = pp._load_readd_policy()
chk("політика в репо: ніколи не «all» без результату тесту; canary — не більше ОДНОГО pid (тест одного SKU)",
    _pol_repo["ever_live"] in ("off", "canary") and (_pol_repo["ever_live"] == "off" or len(_pol_repo["canary_pids"]) == 1))

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
chk("recheck: знято 3 (pid 1,2 — Фаза 1 без доказів існування; 4 — живий); 3 тримається (докази існування, політика off); 5 — floor; 6 — status=deleted", n == 3 and set(state["_readded_at"]) == {"1", "2", "4"})
chk("recheck: _delisted_since НЕ мутовано — позначка лишається фактом історії", set(state["_delisted_since"]) == {"1", "2", "3", "4", "5", "6"})
chk("recheck: effective_delisted після — лишились 3, 5, 6", set(cp.effective_delisted(state)) == {"3", "5", "6"})
chk("recheck: _recheck_at проставлено на кожному перевіреному", set(state["_recheck_at"]) == {"1", "2", "3", "4", "5", "6"})
chk("лічильники «чому нуль»: перевірено 6; floor 1; кандидатів 5; Ф1 2; живі 1; ever_live off 1; status=deleted 1",
    m["checked"] == 6 and m["competitor_floor"] == 1 and m["candidates"] == 5 and m["cleared_never_created"] == 2 and m["cleared_live"] == 1 and m["kept_existed_flag_off"] == 1 and m["kept_status_deleted"] == 1 and m["evidence_ever_live"] >= 1)
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
    n, sent = run_recheck(st0, mk_cat(4), lambda nm: 10000.0, found={str(i): {"status": "deleted"} for i in range(1, 5)}, policy=pol_off, now=T0 + timedelta(hours=5 * k))
    allsent += sent
chk("«перевірено ≥ N, знято 0» 3 прогони поспіль (НЕ пояснено політикою: status=deleted) → ОДИН самодіагностичний Telegram із причиною",
    len(allsent) == 1 and "status=deleted 4" in allsent[0] and "prom_readd_policy.json" in allsent[0] and st0["_meta"]["delisted_recheck"]["zero_streak"] == 3)
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

# ── 5б. аудит #638 B1/B2: докази існування блокують Фазу 1; «вже повертали» не повторюється ──
pp.READD_CLEAR_CAP_PER_RUN = 300
stB = {"_delisted_since": {str(i): iso(T0 - timedelta(days=30)) for i in range(1, 6)}, "_ever_live": {}, "_meta": {},
       "1": {"price": 55.0, "timestamp": "t"},          # запис apply_price → існував
       "_readded_at": {"2": iso(T0 - timedelta(days=40))}}   # 2 вже повертали раніше (потім видалили знову → свіжіша позначка)
_orig_links, _orig_cat = None, None
def run_with_caches(state, cat_, links, ccache, policy=pol_off):
    pp.find_best_competitor = lambda name, cost, link, pics: {"price": 10000.0}
    pp.SEARCH_DELAY = 0
    pcs.fetch_prom_products_by_external_ids = lambda ids: ({}, set())
    ledger.load_ledger = lambda: set()
    pp._load_readd_policy = lambda path=None: policy
    pp.send_telegram_message = lambda t: None
    n = pp._recheck_delisted_pids(state["_delisted_since"], cat_, {}, links, ccache, limit=50, price_state=state, scan_state={}, now=T0)
    return n, state["_meta"]["delisted_recheck"]
nB, mB = run_with_caches(stB, mk_cat(5), {"3": {"prom_id": 7}}, {"4": {"category_id": 5}})
chk("B1: Фаза 1 знімає ТІЛЬКИ pid 5 (жодного доказу); 1 (запис ціни), 3 (own_link), 4 (категорія), 2 (вже повертали) — тримаються",
    nB == 1 and set(stB["_readded_at"]) == {"2", "5"} and mB["cleared_never_created"] == 1 and mB["kept_existed_flag_off"] == 4)
chk("B1/B2: лічильники доказів показують ЧОМУ не знято (price_record, own_link, category_cache, readded_before по 1)",
    mB["evidence_price_record"] == 1 and mB["evidence_own_link"] == 1 and mB["evidence_category_cache"] == 1 and mB["evidence_readded_before"] == 1)
# canary: пріоритет у черзі навіть при limit=1
b1, _ = pp._select_recheck_batch({str(i): iso(T0) for i in range(1, 6)}, {str(i): "2026-10-08T00:00" for i in range(1, 5)}, mk_cat(5), 1, priority={"5"})
chk("canary-pid першим у черзі навіть коли інші ще не перевірялись ('5' має recheck_at порожній, а решта — ні) і навіть при limit=1", b1 == ["5"])
b2, _ = pp._select_recheck_batch({str(i): iso(T0) for i in range(1, 6)}, {"5": "2026-10-08T00:00"}, mk_cat(5), 1, priority={"5"})
chk("canary-pid першим, навіть якщо його щойно перевіряли", b2 == ["5"])
# zero-streak: 6 прогонів → рівно 2 Telegram (кожен 3-й), скидання після ненульового
pp.RECHECK_ZERO_MIN_CHECKED = 3
stS = {"_delisted_since": {str(i): iso(T0 - timedelta(days=30)) for i in range(1, 5)}, "_ever_live": {str(i): "x" for i in range(1, 5)}, "_meta": {}}
sent6 = []
for k in range(6):
    pp.find_best_competitor = lambda name, cost, link, pics: {"price": 10000.0}
    pp.SEARCH_DELAY = 0; pcs.fetch_prom_products_by_external_ids = lambda ids: ({str(i): {"status": "deleted"} for i in range(1, 5)}, set()); ledger.load_ledger = lambda: set()
    pp._load_readd_policy = lambda path=None: pol_off
    pp.send_telegram_message = sent6.append
    pp._recheck_delisted_pids(stS["_delisted_since"], mk_cat(4), {}, {}, {}, limit=50, price_state=stS, scan_state={}, now=T0 + timedelta(hours=5 * k))
chk("zero-streak: 6 прогонів поспіль нуль → рівно 2 Telegram (на 3-му і 6-му), не щоразу", len(sent6) == 2 and stS["_meta"]["delisted_recheck"]["zero_streak"] == 6)
pp.send_telegram_message = lambda t: None
pp._load_readd_policy = lambda path=None: {"ever_live": "all", "canary_pids": set()}
pcs.fetch_prom_products_by_external_ids = lambda ids: ({}, set())
pp._recheck_delisted_pids(stS["_delisted_since"], mk_cat(4), {}, {}, {}, limit=50, price_state=stS, scan_state={}, now=T0 + timedelta(days=2))
chk("zero-streak скидається після прогону, де щось знято", stS["_meta"]["delisted_recheck"]["zero_streak"] == 0)
pp.RECHECK_ZERO_MIN_CHECKED = 100
# ghost-блок не перевіряє вже ефективно виключених
psE = {"_delisted_since": {"20": iso(T0 - timedelta(days=2))}, "_readded_at": {}}
tcE, _ = pp._ghost_check_candidates(psE, {"20": {}, "21": {}}, set(), {"20": "x", "21": "x"}, now=T0)
chk("ghost-блок: уже ефективно виключений (20) не перевіряється повторно; 21 — перевіряється", "20" not in tcE and "21" in tcE)

# ── 5в. аудит #638 (повторний): R-A canary одноразовий, R-B apply-loop, R-D проводка canary, R-E шум алерту ──
chk("R-A: canary ОДНОРАЗОВИЙ — pid, якого вже повертали, за canary повторно НЕ знімається; політика all — знімається",
    cl("777", {}, set(), ["own_link", "readded_before"], pol_can) == (False, "kept_existed_flag_off")
    and cl("777", {}, set(), ["own_link", "readded_before"], pol_all) == (True, "cleared_ever_live_flag"))
# повна послідовність canary: повернули → грація → відмова (нова позначка) → recheck знову НЕ повертає
stC = {"_delisted_since": {"777": iso(T0 - timedelta(days=30))}, "_ever_live": {"777": "x"}, "_meta": {}, "777": {"price": 50.0}}
catC = mk_cat(1); catC["777"] = catC.pop("1"); catC["777"]["id"] = "777"
r1, _ = run_with_caches(stC, catC, {}, {}, policy=pol_can)
chk("canary: перше повернення — знято", r1 == 1 and "777" in stC["_readded_at"])
stC["_delisted_since"]["777"] = iso(T0 + timedelta(hours=130))     # відмова після грації → свіжа позначка
pp_now = T0 + timedelta(hours=135)
pp.find_best_competitor = lambda name, cost, link, pics: {"price": 10000.0}
r2 = pp._recheck_delisted_pids(stC["_delisted_since"], catC, {}, {}, {}, limit=50, price_state=stC, scan_state={}, now=pp_now)
chk("canary: після відмови (свіжа позначка) повторного повернення НЕМАЄ — цикл кожні ~3 доби закрито", r2 == 0 and "777" in cp.effective_delisted(stC))
chk("R-E: нуль, пояснений політикою (усі кандидати існували раніше, pid не в canary), НЕ рахується в zero_streak",
    stC["_meta"]["delisted_recheck"]["zero_explained_by_policy"] is True and stC["_meta"]["delisted_recheck"]["zero_streak"] == 0)
# R-B: _note_readded
psN = {"_delisted_since": {"5": iso(T0 - timedelta(days=3))}}
chk("R-B: _note_readded пише повернення, коли позначка є й повернення ще нема", pp._note_readded(psN, "5", iso(T0)) is True and psN["_readded_at"]["5"] == iso(T0))
chk("R-B: повторний виклик НЕ перезаписує (грація не перезапускається щоразу)", pp._note_readded(psN, "5", iso(T0 + timedelta(hours=9))) is False and psN["_readded_at"]["5"] == iso(T0))
chk("R-B: після НОВОЇ позначки (свіжіша за повернення) — пише знову; pid без позначки — не пише",
    (psN["_delisted_since"].__setitem__("5", iso(T0 + timedelta(days=1))) or pp._note_readded(psN, "5", iso(T0 + timedelta(days=2)))) is True and pp._note_readded(psN, "999", iso(T0)) is False)
# R-D: проводка canary в _recheck_delisted_pids — при малому limit перевіряється ПЕРШИМ
stD = {"_delisted_since": {str(i): iso(T0 - timedelta(days=30)) for i in range(1, 9)}, "_ever_live": {str(i): "x" for i in range(1, 9)}, "_meta": {},
       "_recheck_at": {}}
seen_names = []
pp.find_best_competitor = lambda name, cost, link, pics: (seen_names.append(name) or {"price": 10000.0})
pp.SEARCH_DELAY = 0; pcs.fetch_prom_products_by_external_ids = lambda ids: ({}, set()); ledger.load_ledger = lambda: set()
pp._load_readd_policy = lambda path=None: {"ever_live": "canary", "canary_pids": {"8"}}
pp.send_telegram_message = lambda t: None
pp._recheck_delisted_pids(stD["_delisted_since"], mk_cat(8), {}, {}, {}, limit=1, price_state=stD, scan_state={}, now=T0)
chk("R-D: canary-pid (8) перевіряється ПЕРШИМ при limit=1 серед ніколи-не-перевірених (проводка policy→priority)", seen_names == ["Т8"] and "8" in stD["_readded_at"])

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
