# -*- coding: utf-8 -*-
"""temp_janitor.py — прибирає СМІТТЯ ТЕСТІВ/агентів у %TEMP% (20.11.2026).

ЧОМУ: 20.11 диск ноутбука заповнився (вільно 8,7 ГБ). Виміряно: у %TEMP% лежало 21 471 тека `tmp…` на 22,4 ГБ, усі з 15.11, приріст ≈3 ГБ/добу
(1 346 725 файлів). Це тимчасові теки тестів (`tempfile.mkdtemp()` у ~70 test_*.py — 360 викликів, більшість без видалення) і копії коду, які лишають
аудитори (`mktemp -d` → `tmp.XXXXXXXXXX`). Windows сама %TEMP% не чистить.

ЩО РОБИТЬ: за замовчуванням ТІЛЬКИ ПОКАЗУЄ (dry-run). З `--apply` видаляє теки %TEMP%, чия назва СУВОРО збігається з одним із двох шаблонів
  • `tmp.` + 10 символів [A-Za-z0-9]   (bash `mktemp -d`),
  • `tmp` + 8 символів [a-z0-9_]        (Python `tempfile.mkdtemp()`),
і які не змінювались довше за `--min-age-hours` (типово 24). Не чіпає: файли, інші назви (`claude`, `playwright_*`, GUID, `TCD*.tmp` …), символічні посилання/junction,
теки нових за віком (щоб не зачепити тест, що зараз біжить). Помилка на одній теці (зайнята процесом) не зупиняє решту.
Нічого за межами %TEMP% не чіпає (шлях береться з tempfile.gettempdir(); `--dir` лише для тесту).
"""
import argparse
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PATTERNS = (re.compile(r"^tmp\.[A-Za-z0-9]{10}$"), re.compile(r"^tmp[a-z0-9_]{8}$"))


def is_scratch_name(name: str) -> bool:
    return any(p.match(name) for p in PATTERNS)


def _is_link(path: str) -> bool:
    """Символічне посилання або junction (reparse point) — не заходимо й не видаляємо."""
    if os.path.islink(path):
        return True
    try:
        return bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)   # лише Windows
    except (AttributeError, OSError):
        return False


def find_old_scratch(base, min_age_hours: float = 24.0, now: float | None = None) -> list:
    """Список шляхів тек-сміття (за шаблоном і віком) у base. Лише читає."""
    now = time.time() if now is None else now
    cutoff = now - min_age_hours * 3600
    out = []
    with os.scandir(base) as it:
        for e in it:
            try:
                if not e.is_dir(follow_symlinks=False) or not is_scratch_name(e.name) or _is_link(e.path):
                    continue
                if e.stat(follow_symlinks=False).st_mtime < cutoff:
                    out.append(e.path)
            except OSError:
                continue
    return out


def _onerror(func, path, exc_info):
    """Read-only файл (git-об'єкти в копіях репо) → знімаємо ознаку й повторюємо; інакше пропускаємо."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def sweep(base=None, min_age_hours: float = 24.0, apply: bool = False, budget_s: float = 600.0, now: float | None = None) -> dict:
    """{found, removed, failed}. Без apply — лише found."""
    base = str(base or tempfile.gettempdir())
    found = find_old_scratch(base, min_age_hours, now)
    res = {"base": base, "found": len(found), "removed": 0, "failed": 0}
    if not apply:
        return res
    t0 = time.time()
    for p in found:
        if time.time() - t0 > budget_s:
            break
        shutil.rmtree(p, onerror=_onerror)
        if os.path.exists(p):
            res["failed"] += 1
        else:
            res["removed"] += 1
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="справді видалити (без цього — лише показати)")
    ap.add_argument("--min-age-hours", type=float, default=24.0)
    ap.add_argument("--dir", default=None, help="лише для тесту; типово %%TEMP%%")
    a = ap.parse_args(argv)
    r = sweep(a.dir, a.min_age_hours, a.apply)
    print(f"[temp_janitor] {'APPLY' if a.apply else 'DRY-RUN'} {r['base']}: знайдено {r['found']}, видалено {r['removed']}, не вдалося {r['failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
