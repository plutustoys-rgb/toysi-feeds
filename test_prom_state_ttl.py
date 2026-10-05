# -*- coding: utf-8 -*-
"""Регрес вікон стану пріцера (PR #615): override-ціни тримаються 45 діб (було 30 год → 97% фіда відкочувалось на ×1.75),
а СВІЖИМ конкурентом для canonical-floor лишається запис ≤30 год. Також: стеля для застарілого override
(cap_stale_prom_override) і ЗБІГ ціни Prom-фіда з ціною Google/Meta/Bing (_prom_page_price) для застарілого запису."""
import json, math, sys, tempfile
from datetime import datetime, timedelta
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import competitor_pricing as cp
import generate_google_feed as gg

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

chk("вікно override = 45 діб (1080 год)", cp.PROM_PRICE_STATE_MAX_AGE_HOURS == 1080)
chk("вікно СВІЖОГО конкурента = 30 год", cp.PROM_COMPETITOR_STATE_MAX_AGE_HOURS == 30)
now = datetime.now()
def rec(days, price=100.0, comp=105.0, **kw):
    d = {"price": price, "timestamp": (now - timedelta(days=days)).isoformat(), "competitor_price": comp}
    d.update(kw); return d
state = {"fresh": rec(0.1), "d12": rec(12), "d44": rec(44), "d46": rec(46), "dead": rec(0.2, competitor_alive=False),
         "nocomp": rec(0.2, comp=0), "bad": {"price": "x", "timestamp": "bad"}, "_meta": {"x": 1}}
tmp = Path(tempfile.mkdtemp()) / "state.json"
tmp.write_text(json.dumps(state), encoding="utf-8")
old = cp.PROM_PRICE_STATE_FILE
cp.PROM_PRICE_STATE_FILE = tmp
try:
    ov = cp.load_fresh_prom_price_overrides()
    comp = cp.load_fresh_prom_competitor_prices()
finally:
    cp.PROM_PRICE_STATE_FILE = old
chk("override: 0.1 / 12 / 44 доби чинні", {"fresh", "d12", "d44"} <= set(ov))
chk("override: 46 діб протухла; службові/биті ігноруються", "d46" not in ov and "_meta" not in ov and "bad" not in ov)
chk("конкурент: СВІЖИМ є лише запис ≤30 год", set(comp) == {"fresh"})
chk("competitor_alive=False і comp=0 відсіяні з конкурентів", "dead" not in comp and "nocomp" not in comp)

# стеля застарілого override
chk("стеля: свіжий конкурент — без змін", cp.cap_stale_prom_override(500.0, 300.0, True) == 500.0)
chk("стеля: застарілий ДОРОГИЙ override обрізається до дефолту", cp.cap_stale_prom_override(500.0, 300.0, False) == 300.0)
chk("стеля: застарілий дешевший override лишається", cp.cap_stale_prom_override(250.0, 300.0, False) == 250.0)

# збіг Google ↔ формула Prom-фіда для застарілого запису (division-floor 3%, стеля ×1.75)
cost, cat = 100.0, "Пазли"
default = cp.decide_price_for_platform(cost, None, "prom", cat)["price"]
high = gg._prom_page_price(cost, cat, default * 2.5, has_fresh_competitor=False)
chk("Google: застарілий дорогий override обрізано до дефолту", abs(high - default) < 0.011)
low_stale = gg._prom_page_price(cost, cat, 1.0, has_fresh_competitor=False)
fl = cp.compute_floor(cost, cp.compute_total_commission("prom", cat, 1.0), cp.MIN_PROFIT_COMPETITOR_FLOOR)
chk("Google: застарілий override нижче floor піднято до division-floor 3%", low_stale >= math.ceil(fl * 100 - 1e-6) / 100 - 0.011)
ok = gg._prom_page_price(cost, cat, default * 0.8, has_fresh_competitor=False)
chk("Google: помірний застарілий override (нижче дефолту, вище floor) лишається", abs(ok - default * 0.8) < 0.011 or ok >= default * 0.8)
fresh_hi = gg._prom_page_price(cost, cat, default * 2.5, has_fresh_competitor=True)
chk("Google: свіжий конкурент — без стелі (стара поведінка)", abs(fresh_hi - default * 2.5) < 0.011)
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ PROM_PRICE_STATE вікна/стеля/збіг — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
