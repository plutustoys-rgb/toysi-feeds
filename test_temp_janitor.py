#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_temp_janitor.py — temp_janitor: чіпає ЛИШЕ теки зі шаблонів сміття й лише старші за поріг; dry-run нічого не видаляє. `python test_temp_janitor.py` → exit 0/1."""
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import temp_janitor as tj  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


chk("шаблон: tmp.XXXXXXXXXX і tmpXXXXXXXX — сміття", tj.is_scratch_name("tmp.90M5LQJMTX") and tj.is_scratch_name("tmpz62iu_b7") and tj.is_scratch_name("tmp2s9lsl38"))
chk("шаблон: інше НЕ сміття (claude, playwright, GUID, TCD*.tmp, tmp, задовгі/закороткі)",
    not any(tj.is_scratch_name(n) for n in ("claude", "playwright_chromiumdev_profile-x1", "00ABD36A-7E4A-40D8-B869-20556B86AD88", "TCD8E64.tmp", "tmp", "tmp123", "tmp.ABC", "tmpABCDEFGH", "tmpabcdefghi", "mytmpabcdefgh")))

base = Path(tempfile.mkdtemp(prefix="janitor_test_"))
try:
    now = time.time()
    old = now - 48 * 3600

    def mk(name, age=old, files=2, readonly=False):
        d = base / name
        (d / "sub").mkdir(parents=True)
        for i in range(files):
            f = d / "sub" / f"f{i}.txt"
            f.write_text("x")
            if readonly:
                os.chmod(f, stat.S_IREAD)
        for q in [*d.rglob("*")]:               # усе всередині теж «старе» (інакше вкладені mtime = зараз і тека виглядає живою)
            os.utime(q, (age, age))
        os.utime(d, (age, age))
        return d

    mk("tmp.90M5LQJMTX")                    # старе bash-сміття
    mk("tmpz62iu_b7")                       # старе python-сміття
    mk("tmpfresh123", age=now - 3600)        # нове (1 год) — не чіпати
    mk("tmpro_dir12", readonly=True)         # read-only файли всередині (як git-об'єкти)
    mk("tmpABCDEFGH")                       # велика літера в 8-символьній частині — не шаблон Python
    mk("tmpshort")                          # закоротка назва
    mk("claude")                            # чужа тека зі старим mtime — не чіпати
    mk("playwright_chromiumdev_profile-ab")  # не за шаблоном — не чіпати
    (base / "tmpaabbccdd").write_text("файл, а не тека")
    os.utime(base / "tmpaabbccdd", (old, old))

    r = tj.sweep(base, 24, apply=False, now=now)
    names = set(os.listdir(base))
    chk("dry-run: знайшов 3 старі (bash, python, read-only), нічого не видалив", r["found"] == 3 and r["removed"] == 0 and len(names) == 9)

    r = tj.sweep(base, 24, apply=True, now=now)
    names = set(os.listdir(base))
    chk("apply: видалено рівно 3, збоїв 0", r["found"] == 3 and r["removed"] == 3 and r["failed"] == 0)
    chk("apply: старі сміттєві теки зникли (включно з read-only)", not ({"tmp.90M5LQJMTX", "tmpz62iu_b7", "tmpro_dir12"} & names))
    chk("apply: лишились нова тека, claude, playwright_*, файл tmpaabbccdd", {"tmpfresh123", "claude", "playwright_chromiumdev_profile-ab", "tmpaabbccdd"} <= names)

    r = tj.sweep(base, 0.5, apply=True, now=now)
    chk("поріг 0,5 год: нова (1 год) тека теж видаляється, чужі лишаються", "tmpfresh123" not in os.listdir(base) and "claude" in os.listdir(base))

    act = mk("tmpactive01")
    top = act / "fresh_child"; top.mkdir(); os.utime(top, (now, now)); os.utime(act, (old, old))
    chk("вік за найсвіжішою активністю: корінь старий, але дитина верхнього рівня свіжа → не сміття", str(act) not in tj.find_old_scratch(base, 24, now))
    chk("корінь диска/домашня тека → відмова, нічого не знайдено",
        tj.sweep(os.path.splitdrive(str(base))[0] + os.sep, 24, apply=False).get("refused") is True and tj.sweep(os.path.expanduser("~"), 24, apply=False).get("refused") is True and tj.main(["--dir", os.path.expanduser("~")]) == 2)

    chk("main(): без --apply це dry-run (exit 0)", tj.main(["--dir", str(base), "--min-age-hours", "0"]) == 0 and "claude" in os.listdir(base))
finally:
    shutil.rmtree(base, ignore_errors=True)

import health_check as hc  # noqa: E402

_orig = tj.find_old_scratch
_ages = []
tj.find_old_scratch = lambda *a, **k: (_ages.append(a[1]), ["x"] * (hc.TEMP_SCRATCH_WARN + 1))[1]
r = hc.check_temp(None)
chk("health_check.check_temp: більше порогу → одне попередження temp:scratch з командою прибирання", len(r) == 1 and r[0]["id"] == "temp:scratch" and r[0]["sev"] == "warn" and "temp_janitor.py" in r[0]["hint"])
tj.find_old_scratch = lambda *a, **k: ["x"] * hc.TEMP_SCRATCH_WARN
chk("health_check.check_temp: вік береться з TEMP_SCRATCH_MIN_AGE_H (48), а не 24", _ages == [hc.TEMP_SCRATCH_MIN_AGE_H] and hc.TEMP_SCRATCH_MIN_AGE_H == 48)
chk("health_check.check_temp: рівно поріг → чисто", hc.check_temp(None) == [])
tj.find_old_scratch = _orig

sys.exit(1 if F else 0)
