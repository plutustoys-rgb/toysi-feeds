#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_source_freshness.py — регрес-тест детектора тиші (source_freshness.py, аудит Д3,
2026-09-18: RozetkaPay/ПриватБанк/EVA мовчки жують старий файл, Task Scheduler звітує
LastTaskResult=0, ніхто не бачить, що вхід зупинився).

Файлова система — тимчасова тека. `python test_source_freshness.py` → exit 0/1.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import source_freshness as sf

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


_TMP_DIR = Path(tempfile.mkdtemp())
_STATE = _TMP_DIR / "_dzherela_stan.json"
_REPORT = _TMP_DIR / "_mertvi_vhody.md"


# 1: немає жодного файлу за патерном → stale, reason=no_files
_empty_glob = str(_TMP_DIR / "nema_takoi_teky" / "*.xlsx")
r1 = sf.check_and_record("Джерело-порожнє", _empty_glob, max_stale_days=3, path=_STATE)
_chk("немає файлів: stale=True", r1["stale"] is True)
_chk("немає файлів: reason=no_files", r1["reason"] == "no_files")

# 2: свіжий файл (щойно створений) → не stale
_fresh_dir = _TMP_DIR / "fresh"
_fresh_dir.mkdir()
(_fresh_dir / "2026-09-18_registry.xlsx").write_text("x")
r2 = sf.check_and_record("Джерело-свіже", str(_fresh_dir / "*.xlsx"), max_stale_days=3, path=_STATE)
_chk("свіжий файл: stale=False", r2["stale"] is False)
_chk("свіжий файл: age_days ~0", r2["age_days"] < 0.01)

# 3: старий файл (mtime зсунуто назад) → stale
_stale_dir = _TMP_DIR / "stale"
_stale_dir.mkdir()
old_file = _stale_dir / "2026-08-27_registry.xlsx"
old_file.write_text("x")
old_ts = time.time() - 10 * 86400  # 10 днів тому
os.utime(old_file, (old_ts, old_ts))
r3 = sf.check_and_record("Джерело-старе", str(_stale_dir / "*.xlsx"), max_stale_days=3, path=_STATE)
_chk("старий файл (10 днів, поріг 3): stale=True", r3["stale"] is True)
_chk("старий файл: age_days ~10", 9.9 <= r3["age_days"] <= 10.1)
_chk("старий файл: newest = правильне ім'я", r3["newest"] == "2026-08-27_registry.xlsx")

# 4: файл РІВНО на порозі (3.0 днів при порозі 3) → stale (>=)
_edge_dir = _TMP_DIR / "edge"
_edge_dir.mkdir()
edge_file = _edge_dir / "edge.xlsx"
edge_file.write_text("x")
edge_ts = time.time() - 3 * 86400 - 60  # трохи більше 3 днів, щоб уникнути похибки округлення
os.utime(edge_file, (edge_ts, edge_ts))
r4 = sf.check_and_record("Джерело-межа", str(_edge_dir / "*.xlsx"), max_stale_days=3, path=_STATE)
_chk("рівно на межі порогу: stale=True", r4["stale"] is True)

# 5: write_report() — stale ЗГОРИ (видимість проблем), fresh теж показано (доказ живого моніторингу)
report_path = sf.write_report(path=_STATE, out_path=_REPORT)
content = report_path.read_text(encoding="utf-8")
_chk("звіт: stale-джерело є в тексті", "Джерело-старе" in content)
_chk("звіт: fresh-джерело теж є (не лише проблеми)", "Джерело-свіже" in content)
_chk("звіт: no_files-джерело позначено 🔴", "Джерело-порожнє" in content and "жодного файлу" in content)
_chk("звіт: свіже позначено ✅", "✅ свіжий" in content)

# 6: повторний виклик того самого джерела ОНОВЛЮЄ запис, не дублює
r2b = sf.check_and_record("Джерело-свіже", str(_fresh_dir / "*.xlsx"), max_stale_days=3, path=_STATE)
state = sf._load_state(_STATE)
_chk("повторний виклик: лише один запис на джерело", len([k for k in state if k == "Джерело-свіже"]) == 1)

# 7: порожній стан (жодного check_and_record) → звіт не падає
_empty_state = _TMP_DIR / "_empty_state.json"
_empty_report = _TMP_DIR / "_empty_report.md"
sf._save_state({}, path=_empty_state)
er = sf.write_report(path=_empty_state, out_path=_empty_report)
_chk("порожній стан: звіт сформовано без винятку", er.exists())


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Детектор тиші: no_files/fresh/stale/межа/звіт/оновлення-без-дублю — усе коректно")
sys.exit(0)
