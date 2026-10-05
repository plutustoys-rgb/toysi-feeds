# -*- coding: utf-8 -*-
"""Регрес PROM_PRICE_STATE_MAX_AGE_HOURS: рішення пріцера тримається 30 діб (було 30 год → 97% фіда відкочувалось
на ×1.75, див. коментар у competitor_pricing.py). Перевіряє межі вікна для override-цін і цін конкурентів."""
import json, sys, tempfile
from datetime import datetime, timedelta
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import competitor_pricing as cp

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

chk("вікно = 30 діб (720 год)", cp.PROM_PRICE_STATE_MAX_AGE_HOURS == 720)
now = datetime.now()
def rec(days, price=100.0, comp=105.0, **kw):
    d = {"price": price, "timestamp": (now - timedelta(days=days)).isoformat(), "competitor_price": comp}
    d.update(kw); return d
state = {"fresh": rec(0.1), "d12": rec(12), "d29": rec(29), "d31": rec(31), "dead": rec(2, competitor_alive=False),
         "nocomp": rec(2, comp=0), "bad": {"price": "x", "timestamp": "bad"}, "_meta": {"x": 1}}
tmp = Path(tempfile.mkdtemp()) / "state.json"
tmp.write_text(json.dumps(state), encoding="utf-8")
old = cp.PROM_PRICE_STATE_FILE
cp.PROM_PRICE_STATE_FILE = tmp
try:
    ov = cp.load_fresh_prom_price_overrides()
    comp = cp.load_fresh_prom_competitor_prices()
finally:
    cp.PROM_PRICE_STATE_FILE = old
chk("override: 0.1 доби, 12 діб, 29 діб чинні", {"fresh", "d12", "d29"} <= set(ov))
chk("override: 31 доба вже протухла", "d31" not in ov)
chk("override: службові/биті записи ігноруються", "_meta" not in ov and "bad" not in ov)
chk("конкурент: чинні 0.1/12/29 діб", {"fresh", "d12", "d29"} <= set(comp))
chk("конкурент: 31 доба протухла; competitor_alive=False і comp=0 відсіяні",
    "d31" not in comp and "dead" not in comp and "nocomp" not in comp)
chk("конкурентні ціни узгоджені з override за вікном (пара price/competitor)", set(comp) <= set(ov))
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ PROM_PRICE_STATE TTL — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
