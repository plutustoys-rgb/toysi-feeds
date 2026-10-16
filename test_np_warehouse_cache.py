#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_np_warehouse_cache.py — кеш відділень НП для автокомпліту (nova_poshta.search_warehouses, 16.10.2026: ~15% 502 через «To many requests»).
Інваріанти: (1) кілька запитів з різними q для одного міста = ОДИН виклик getWarehouses; фільтр q працює на кеші; (2) інше місто — окремий виклик; (3) після TTL — новий виклик; (4) помилка НП при наявному (навіть застарілому) кеші → віддаємо кеш без винятку;
(5) помилка без кешу: одна повторна спроба, далі NovaPoshtaAPIError; повтор успішний → результат; (6) порожній city_ref → [] без викликів. `python test_np_warehouse_cache.py` → exit 0/1."""
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("NP_API_KEY", "x")
sys.path.insert(0, str(Path(__file__).parent))
import nova_poshta as np_  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


np_.time.sleep = lambda s: None
calls = {"n": 0, "fail": 0}
DATA = [{"Ref": f"r{i}", "Number": str(i), "Description": f"Відділення №{i}: вул. Тестова {i}"} for i in range(1, 40)]


def fake_call(model, method, props):
    calls["n"] += 1
    if calls["fail"] > 0:
        calls["fail"] -= 1
        raise np_.NovaPoshtaAPIError("API повернув помилку: ['To many requests']")
    return list(DATA)


np_._call = fake_call
np_._WH_CACHE.clear()
r1 = np_.search_warehouses("kyiv", "2")
r2 = np_.search_warehouses("kyiv", "10")
r3 = np_.search_warehouses("kyiv", "25")
chk("3 запити (q=2/10/25) для одного міста = ОДИН виклик НП; фільтр за номером працює на кеші", calls["n"] == 1 and r2 and r2[0]["number"] == "10" and r3[0]["number"] == "25" and r1[0]["number"] == "2")
np_.search_warehouses("lviv", "1")
chk("інше місто — окремий виклик", calls["n"] == 2)
ts, data = np_._WH_CACHE["kyiv"]
np_._WH_CACHE["kyiv"] = (ts - np_._WH_TTL - 5, data)
calls["fail"] = 5
r = np_.search_warehouses("kyiv", "7")
chk("TTL минув, НП відповідає помилкою (обидві спроби) → віддаємо ЗАСТАРІЛИЙ кеш без винятку", r and r[0]["number"] == "7" and calls["n"] == 4)
calls["fail"] = 0
np_._WH_CACHE["kyiv"] = (ts - np_._WH_TTL - 5, data)
n0 = calls["n"]
np_.search_warehouses("kyiv", "7")
chk("TTL минув, НП ок → оновлення кешу (1 виклик)", calls["n"] == n0 + 1 and np_._WH_CACHE["kyiv"][0] > ts)
np_._WH_CACHE.clear()
calls["n"], calls["fail"] = 0, 1
r = np_.search_warehouses("odesa", "3")
chk("без кешу: перша спроба — помилка, друга успішна → результат (2 виклики)", r and r[0]["number"] == "3" and calls["n"] == 2)
np_._WH_CACHE.clear()
calls["n"], calls["fail"] = 0, 9
try:
    np_.search_warehouses("dnipro", "3")
    ok = False
except np_.NovaPoshtaAPIError:
    ok = True
chk("без кешу і НП лежить: рівно 2 спроби, далі NovaPoshtaAPIError (сайт покаже 502, як і було)", ok and calls["n"] == 2 and "dnipro" not in np_._WH_CACHE)
calls["n"] = 0
chk("порожній city_ref → [] без викликів", np_.search_warehouses("", "1") == [] and np_.search_warehouses("  ", "1") == [] and calls["n"] == 0)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
