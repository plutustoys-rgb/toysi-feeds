#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_np_warehouse_autocomplete_cache.py — кеш відділень НП для АВТОКОМПЛІТУ сайту (nova_poshta.search_warehouses/_city_warehouses, 16.10.2026: ~15% 502 через «To many requests»). НЕ плутати з test_np_warehouse_cache.py (нічний SQLite-кеш довідника).
Інваріанти: (1) кілька запитів з різним q для одного міста = ОДИН виклик getWarehouses; фільтр за номером/назвою на кеші; (2) інше місто — окремий виклик; (3) після TTL — новий виклик; (4) помилка НП при наявному кеші → віддаємо застарілий + негативний TTL (~60 с без нових викликів);
(5) без кешу: тимчасова помилка → одна повторна спроба; детермінована («City not found») → БЕЗ повтору; далі NovaPoshtaAPIError; (6) порожня відповідь НП = збій: у кеш не пишемо, добрий кеш не затираємо; (7) city_ref не GUID → [] без викликів; (8) замок на МІСТО: повільне місто A не блокує місто B. `python test_np_warehouse_autocomplete_cache.py` → exit 0/1."""
import os
import sys
import threading
import time
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


real_sleep = time.sleep
np_.time.sleep = lambda s: None
calls = {"n": 0, "mode": "ok", "per": {}}
DATA = [{"Ref": f"r{i}", "Number": str(i), "Description": f"Відділення №{i}: вул. Тестова {i}"} for i in range(1, 40)]
KYIV, LVIV, ODESA = "8d5a980d-391c-11dd-90d9-001a92567626", "db5c88f5-391c-11dd-90d9-001a92567626", "abcdef12-3456-7890-abcd-ef1234567890"


def fake_call(model, method, props):
    calls["n"] += 1
    calls["per"][props["CityRef"]] = calls["per"].get(props["CityRef"], 0) + 1
    m = calls["mode"]
    if m == "throttle":
        raise np_.NovaPoshtaAPIError("API повернув помилку: ['To many requests']")
    if m == "notfound":
        raise np_.NovaPoshtaAPIError("API повернув помилку: ['City not found']")
    if m == "empty":
        return []
    if m == "once_throttle":
        calls["mode"] = "ok"
        raise np_.NovaPoshtaAPIError("API повернув помилку: ['To many requests']")
    return [dict(x) for x in DATA]


np_._call = fake_call
np_._WH_CACHE.clear()
r1 = np_.search_warehouses(KYIV, "2")
r2 = np_.search_warehouses(KYIV, "10")
r3 = np_.search_warehouses(KYIV, "25")
chk("3 запити (q=2/10/25) для одного міста = ОДИН виклик НП; фільтр за номером працює на кеші", calls["n"] == 1 and r1[0]["number"] == "2" and r2[0]["number"] == "10" and r3[0]["number"] == "25")
chk("фільтр за частиною назви і ліміт 12", len(np_.search_warehouses(KYIV, "тестова")) == 12 and np_.search_warehouses(KYIV, "вул. тестова 7")[0]["number"] == "7")
np_.search_warehouses(LVIV, "1")
chk("інше місто — окремий виклик", calls["n"] == 2)
ts, data = np_._WH_CACHE[KYIV]
np_._WH_CACHE[KYIV] = (ts - np_._WH_TTL - 5, data)
calls["mode"] = "throttle"
n0 = calls["n"]
r = np_.search_warehouses(KYIV, "7")
chk("TTL минув, НП «To many requests» (2 спроби) → застарілий кеш без винятку", r and r[0]["number"] == "7" and calls["n"] == n0 + 2)
n1 = calls["n"]
np_.search_warehouses(KYIV, "8")
np_.search_warehouses(KYIV, "9")
chk("негативний TTL: наступні запити ~60 с НЕ б'ють НП (0 нових викликів)", calls["n"] == n1)
np_._WH_CACHE[KYIV] = (ts - np_._WH_TTL - 5, data)
calls["mode"] = "ok"
n2 = calls["n"]
np_.search_warehouses(KYIV, "7")
chk("TTL минув, НП ок → оновлення кешу (1 виклик)", calls["n"] == n2 + 1 and np_._WH_CACHE[KYIV][0] > ts)
np_._WH_CACHE.clear()
calls["n"], calls["mode"] = 0, "once_throttle"
r = np_.search_warehouses(ODESA, "3")
chk("без кешу: тимчасова помилка → одна повторна спроба → результат (2 виклики)", r and r[0]["number"] == "3" and calls["n"] == 2)
np_._WH_CACHE.clear()
calls["n"], calls["mode"] = 0, "notfound"
try:
    np_.search_warehouses(ODESA, "3")
    ok = False
except np_.NovaPoshtaAPIError:
    ok = True
chk("детермінована помилка («City not found»): БЕЗ повтору (1 виклик), виняток, кеш не створено", ok and calls["n"] == 1 and ODESA not in np_._WH_CACHE)
calls["n"], calls["mode"] = 0, "throttle"
try:
    np_.search_warehouses(ODESA, "3")
    ok = False
except np_.NovaPoshtaAPIError:
    ok = True
chk("без кешу і НП throttle: 2 спроби, далі NovaPoshtaAPIError (сайт покаже 502, як і було)", ok and calls["n"] == 2 and ODESA not in np_._WH_CACHE)
np_._WH_CACHE.clear()
calls["mode"] = "ok"
np_.search_warehouses(KYIV, "1")
good = np_._WH_CACHE[KYIV]
np_._WH_CACHE[KYIV] = (good[0] - np_._WH_TTL - 5, good[1])
calls["mode"] = "empty"
r = np_.search_warehouses(KYIV, "5")
chk("порожня відповідь НП (throttle) НЕ затирає добрий кеш: віддано застарілі дані з 39 відділень", r and r[0]["number"] == "5" and len(np_._WH_CACHE[KYIV][1]) == 39)
np_._WH_CACHE.clear()
calls["n"] = 0
try:
    np_.search_warehouses(LVIV, "1")
    ok = False
except np_.NovaPoshtaAPIError:
    ok = True
chk("порожнє без кешу → NovaPoshtaAPIError, LVIV не в кеші", ok and LVIV not in np_._WH_CACHE)
calls["n"], calls["mode"] = 0, "ok"
chk("city_ref не GUID («x», сміття, порожній) → [] без викликів НП", np_.search_warehouses("x", "1") == [] and np_.search_warehouses("не-guid!", "1") == [] and np_.search_warehouses("", "1") == [] and calls["n"] == 0)
# замок на місто: повільне А не блокує Б
np_._WH_CACHE.clear()
slow = threading.Event()
orig = np_._call


def slow_call(model, method, props):
    if props["CityRef"] == KYIV:
        slow.wait(3)
    return [dict(x) for x in DATA]


np_._call = slow_call
t = threading.Thread(target=lambda: np_.search_warehouses(KYIV, "1"))
t.start()
real_sleep(0.2)
t0 = time.time()
np_.search_warehouses(LVIV, "1")
dt = time.time() - t0
slow.set()
t.join()
chk(f"замок на МІСТО: Київ (повільний промах) не блокує Львів (відповідь за {dt:.2f} с, не 3 с)", dt < 1.0)
np_._call = orig
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
