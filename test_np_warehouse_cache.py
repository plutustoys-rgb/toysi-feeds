#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_np_warehouse_cache.py — регрес-тест локального кешу довідника відділень НП
(nova_poshta_warehouse_cache.py, 2026-09-17, за офіційною рекомендацією developers.novaposhta.ua
"оновлювати довідник щоночі").

Мережа НЕ потрібна: nova_poshta_warehouse_cache._call замокано (сторінки заздалегідь задані).
Усе на ТИМЧАСОВОМУ файлі БД (NP_WAREHOUSE_CACHE_PATH), не в репо.
`python test_np_warehouse_cache.py` → exit 0/1.
"""
import sys
import os
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import nova_poshta_warehouse_cache as cache
from nova_poshta import NovaPoshtaAPIError

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


_DB = tempfile.mktemp(suffix=".db")
cache.PAGE_DELAY_SEC = 0       # без реальних пауз у тесті
cache.PAGE_RETRY_DELAY_SEC = 0

# --- 1: lookup_ref на порожньому/неіснуючому кеші → None, без винятку ---
_chk("lookup_ref на неіснуючому файлі кешу → None", cache.lookup_ref("будь-який-ref", db_path=_DB) is None)
_chk("cache_age_hours на неіснуючому файлі → None", cache.cache_age_hours(db_path=_DB) is None)

# --- 2: успішна синхронізація (2 повні сторінки + 1 неповна = кінець) ---
_PAGE1 = [{"Ref": f"ref-{i}", "CityRef": "city-A", "Number": str(i), "Description": f"Відділення №{i}"}
          for i in range(1, 4)]
_PAGE2 = [{"Ref": f"ref-{i}", "CityRef": "city-B", "Number": str(i), "Description": f"Відділення №{i}"}
          for i in range(4, 7)]
_PAGE3 = []  # порожня — умова завершення

_calls = []


def _mock_call_ok(model, method, props):
    _calls.append(props.get("Page"))
    page = int(props.get("Page"))
    return {"1": _PAGE1, "2": _PAGE2, "3": _PAGE3}.get(str(page), [])


cache._call = _mock_call_ok
cache.PAGE_LIMIT = 3  # маленький ліміт, щоб _PAGE1/_PAGE2 (по 3) вважались "повними" сторінками
result = cache.sync_full_directory(db_path=_DB, page_limit=3)
_chk("sync: 6 записів завантажено", result["records"] == 6)
_chk("sync: без помилок", result["errors"] == [])
_chk("sync: пройшло 3 сторінки (2 повні + порожня)", _calls == ["1", "2", "3"])

# --- 3: lookup_ref після синхронізації — реальні дані з кешу ---
w = cache.lookup_ref("ref-2", db_path=_DB)
_chk("lookup ref-2: city_ref=city-A", w is not None and w["city_ref"] == "city-A")
_chk("lookup ref-2: number=2", w is not None and w["number"] == "2")
w5 = cache.lookup_ref("ref-5", db_path=_DB)
_chk("lookup ref-5 (з другої сторінки): city_ref=city-B", w5 is not None and w5["city_ref"] == "city-B")
_chk("lookup неіснуючого ref → None", cache.lookup_ref("ref-999", db_path=_DB) is None)

# --- 4: cache_age_hours після успішної синхронізації — маленьке додатне число ---
age = cache.cache_age_hours(db_path=_DB)
_chk("cache_age_hours після sync: 0 <= age < 0.01 год", age is not None and 0 <= age < 0.01)

# --- 5: UPSERT — повторна синхронізація ОНОВЛЮЄ запис, не дублює ---
_PAGE1_UPDATED = [{"Ref": "ref-1", "CityRef": "city-A-NEW", "Number": "1", "Description": "Оновлено"}]


def _mock_call_updated(model, method, props):
    page = int(props.get("Page"))
    return {"1": _PAGE1_UPDATED}.get(str(page), [])


cache._call = _mock_call_updated
cache.sync_full_directory(db_path=_DB, page_limit=3)
w_upd = cache.lookup_ref("ref-1", db_path=_DB)
_chk("UPSERT: city_ref оновлено", w_upd is not None and w_upd["city_ref"] == "city-A-NEW")
import sqlite3
with sqlite3.connect(_DB) as conn:
    count = conn.execute("SELECT COUNT(*) FROM warehouses WHERE ref='ref-1'").fetchone()[0]
_chk("UPSERT: жодного дубля рядка", count == 1)

# --- 6: збій сторінки (усі ретраї вичерпано) → sync зупиняється, last_full_sync_at НЕ оновлюється ---
_DB2 = tempfile.mktemp(suffix=".db")


def _mock_call_fail_page2(model, method, props):
    page = int(props.get("Page"))
    if page == 1:
        return _PAGE1
    raise NovaPoshtaAPIError("To many requests")


cache._call = _mock_call_fail_page2
result2 = cache.sync_full_directory(db_path=_DB2, page_limit=3)
_chk("збій сторінки: помилка зафіксована", len(result2["errors"]) == 1)
_chk("збій сторінки: 3 записи з успішної page1 усе одно збережені", result2["records"] == 3)
_chk("збій сторінки: last_full_sync_at НЕ проставлено (чесний вік кешу)",
     cache.cache_age_hours(db_path=_DB2) is None)
_chk("збій сторінки: page1-дані все одно читаються з lookup_ref",
     cache.lookup_ref("ref-1", db_path=_DB2) is not None)


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Локальний кеш довідника відділень НП — синхронізація+lookup+UPSERT+часткова помилка")
sys.exit(0)
