#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_toysi_scanned_discount.py — знижка Toysi зі скану кабінету (toysi_discounts.json) як друге джерело після спостережень.

ІНВАРІАНТИ:
  1. Є свіжий скан, base збігається з каталожною ціною -> знижка зі скану (і для toysi_discounted_price, і для
     toysi_cost_discounted_price): живий кейс 297194 «Велосипеди» (правило-виняток давало 0 %, реально 15 %).
  2. Спостереження із замовлень ПЕРЕВАЖАЄ скан.
  3. Скан старший за TOYSI_SCAN_MAX_AGE_DAYS / немає файлу / битий файл -> None (правило, як було).
  4. Каталожна ціна змінилась (не збігається з base у скані) -> None.
  5. Знижка поза [0, SANITY_MAX] -> None. Нуль-знижка у скані (base == price) -> 0.0, не None.
  6. TOYSI_SCAN_DISCOUNT=0 вимикає.
Файли замокані (tmp). `python test_toysi_scanned_discount.py` -> exit 0/1."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import competitor_pricing as cp

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


def _scan(tmpdir, items, age_days=0.0, raw=None):
    f = Path(tmpdir) / "toysi_discounts.json"
    if raw is not None:
        f.write_text(raw, encoding="utf-8")
    else:
        at = (datetime.now() - timedelta(days=age_days)).isoformat(timespec="seconds")
        f.write_text(json.dumps({"at": at, "items": items}), encoding="utf-8")
    return f


BIKE = {"id": "297194", "price": 291.92, "vendor": "x", "category_name": "Велосипеди", "country": "Китай", "is_import": True}
ITEMS = {"297194": {"base": 291.92, "price": 248.13, "disc": 0.15},
         "111": {"base": 100.0, "price": 100.0, "disc": 0.0},
         "222": {"base": 100.0, "price": 50.0, "disc": 0.5}}

env_on = {k: v for k, v in os.environ.items() if k != "TOYSI_SCAN_DISCOUNT"}

with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, env_on, clear=True):
    obs = Path(td) / "obs.json"  # порожній кеш спостережень
    with mock.patch.object(cp, "TOYSI_OBSERVED_DISCOUNT_FILE", obs), mock.patch.object(cp, "_observed_discount_cache", None):
        # 1
        with mock.patch.object(cp, "TOYSI_SCAN_FILE", _scan(td, ITEMS)), mock.patch.object(cp, "_scan_cache", None):
            _chk("1a scanned_discount_pct = 0.15", cp.scanned_discount_pct("297194", 291.92) == 0.15)
            _chk("1b toysi_discounted_price береться зі скану (248.13), а не правило-виняток (291.92)",
                 abs(cp.toysi_discounted_price(dict(BIKE)) - 248.13) < 0.02)
            _chk("1c собівартість = 248.13 + 15 Збірка", abs(cp.real_toysi_cost(dict(BIKE)) - 263.13) < 0.02)
            # 5
            _chk("5a нуль-знижка у скані -> 0.0, не None", cp.scanned_discount_pct("111", 100.0) == 0.0)
            _chk("5b знижка 50 % (поза межею) -> None", cp.scanned_discount_pct("222", 100.0) is None)
            # 4
            _chk("4 каталожна ціна змінилась -> None", cp.scanned_discount_pct("297194", 300.0) is None)
            _chk("4b товару нема у скані -> None", cp.scanned_discount_pct("999", 100.0) is None)
            # 6
            with mock.patch.dict(os.environ, {"TOYSI_SCAN_DISCOUNT": "0"}):
                _chk("6 TOYSI_SCAN_DISCOUNT=0 -> None", cp.scanned_discount_pct("297194", 291.92) is None)
        # 3
        with mock.patch.object(cp, "TOYSI_SCAN_FILE", _scan(td, ITEMS, age_days=4)), mock.patch.object(cp, "_scan_cache", None):
            _chk("3a скан 4 доби -> None", cp.scanned_discount_pct("297194", 291.92) is None)
            _chk("3b без скану працює правило (виняток 'велосипед' -> без знижки)", abs(cp.toysi_discounted_price(dict(BIKE)) - 291.92) < 0.02)
        with mock.patch.object(cp, "TOYSI_SCAN_FILE", Path(td) / "nope.json"), mock.patch.object(cp, "_scan_cache", None):
            _chk("3c файлу нема -> None", cp.scanned_discount_pct("297194", 291.92) is None)
        with mock.patch.object(cp, "TOYSI_SCAN_FILE", _scan(td, None, raw="{broken")), mock.patch.object(cp, "_scan_cache", None):
            _chk("3d битий файл -> None, без винятку", cp.scanned_discount_pct("297194", 291.92) is None)
        # 2
        obs.write_text(json.dumps({"297194": {"pct": 0.05, "observed_at": datetime.now().isoformat(), "source_order": "t"}}), encoding="utf-8")
        with mock.patch.object(cp, "_observed_discount_cache", None), \
             mock.patch.object(cp, "TOYSI_SCAN_FILE", _scan(td, ITEMS)), mock.patch.object(cp, "_scan_cache", None):
            _chk("2 спостереження із замовлень (5 %) переважає скан (15 %)", abs(cp.toysi_discounted_price(dict(BIKE)) - 291.92 * 0.95) < 0.02)

# ── C6 (аудит #2068): fail-closed на брудні дані ──
with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, env_on, clear=True):
    obs = Path(td) / "obs.json"
    with mock.patch.object(cp, "TOYSI_OBSERVED_DISCOUNT_FILE", obs), mock.patch.object(cp, "_observed_discount_cache", None):
        def _one(entry_items, base=100.0, pid="1", age=0.0, raw=None, at_override=None):
            f = _scan(td, entry_items, age_days=age, raw=raw)
            if at_override:
                f.write_text(json.dumps({"at": at_override, "items": entry_items}), encoding="utf-8")
            with mock.patch.object(cp, "TOYSI_SCAN_FILE", f), mock.patch.object(cp, "_scan_cache", None):
                return cp.scanned_discount_pct(pid, base)
        _chk("C6 price>base -> None", _one({"1": {"base": 100.0, "price": 120.0}}) is None)
        _chk("C6 NaN price -> None", _one({"1": {"base": 100.0, "price": float("nan")}}) is None)
        _chk("C6 base=0 -> None", _one({"1": {"base": 0, "price": 0}}, base=0) is None)
        _chk("C6 id числом -> знайдено за str(id)", _one({"1": {"base": 100.0, "price": 90.0}}, pid=1) == 0.1)
        _chk("C6 entry — список -> None", _one({"1": [100.0, 90.0]}) is None)
        _chk("C6 entry — рядок -> None", _one({"1": "x"}) is None)
        _chk("C6 OverflowError (base=10**400) -> None, без винятку", _one({"1": {"base": 10 ** 400, "price": 1}}, base=1.0) is None)
        fut = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        _chk("C6 `at` у майбутньому (+30 діб) -> None", _one({"1": {"base": 100.0, "price": 90.0}}, at_override=fut) is None)
        tz = datetime.now().astimezone().isoformat(timespec="seconds")
        _chk("C6 tz-aware `at` -> None", _one({"1": {"base": 100.0, "price": 90.0}}, at_override=tz) is None)
        _chk("C6 items — список -> None", _one([1, 2]) is None)
        _chk("C6 JSON без `at` -> None", _one(None, raw='{"items": {}}') is None)
    # C1: помилка розбору кешується під справжнім mtime — файл не перечитується щовиклику
    f = Path(td) / "toysi_discounts.json"
    f.write_text('{"items": {}}', encoding="utf-8")
    with mock.patch.object(cp, "TOYSI_SCAN_FILE", f), mock.patch.object(cp, "_scan_cache", None):
        calls = []
        orig = Path.read_text
        def _spy(self, *a, **k):
            if self == f:
                calls.append(1)
            return orig(self, *a, **k)
        with mock.patch.object(Path, "read_text", _spy):
            for _ in range(50):
                cp.scanned_discount_pct("1", 100.0)
        _chk("C1 битий файл читається 1 раз на 50 викликів", len(calls) == 1)

print("ALL OK" if not _FAILS else f"FAILED: {_FAILS}")
sys.exit(1 if _FAILS else 0)
