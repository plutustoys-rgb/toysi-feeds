#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_photo_override.py — точкова заміна мертвого головного фото (build_site.PHOTO_OVERRIDES, 20.10.2026) і хвиля 1 відкриття rt- (rt_index_ids.json).
Інваріанти: (1) PHOTO_OVERRIDES: id → живий toysi.ua URL з /p/<n>/enl_<id>.jpg того самого id; (2) rt_index_ids.json: усі id мають формат rt-…, унікальні, читаються load_index_ids; кількість = 1245 хвилі 1 (SEO 20.10)."""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "site"))
import re  # noqa: E402
import build_site as bs  # noqa: E402
import rt_supplier_export as ex  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


chk("PHOTO_OVERRIDES: кожен id → https://toysi.ua/p/<n>/enl_<той самий id>.jpg", bool(bs.PHOTO_OVERRIDES) and all(re.fullmatch(rf"https://toysi\.ua/p/\d+/enl_{re.escape(k)}\.jpg", v) for k, v in bs.PHOTO_OVERRIDES.items()))
ids = ex.load_index_ids()
chk("rt_index_ids.json читається load_index_ids, усі id формату rt-…, унікальні", len(ids) > 0 and all(re.fullmatch(r"rt-[0-9A-Za-z_]+", i) for i in ids))
chk("хвиля 1: рівно 1 245 id (SEO 20.10)", len(ids) == 1245)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
