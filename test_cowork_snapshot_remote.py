#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_cowork_snapshot_remote.py — регрес deploy/cowork_snapshot_remote.sh (VPS-частина тижневого знімка Cowork).

ІНВАРІАНТИ: перед розпаковкою перевіряється вільне місце (мало -> нічого не розпаковано, старі знімки цілі);
розпаковка у <dir>.part і перейменування лише після успіху; підозріло мало файлів (<100) -> не публікується;
битий tar-потік -> .part прибрано, старі знімки цілі; ротація залишає KEEP найновіших лише ПІСЛЯ успіху;
права 700. Усе в тимчасовій теці (BACKUP_ROOT), без VPS. Потрібні bash і tar у PATH.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT = Path(__file__).resolve().parent / "deploy" / "cowork_snapshot_remote.sh"
BASH, TAR = shutil.which("bash"), shutil.which("tar")
F = []


def chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        F.append(name)


if not (BASH and TAR):
    print("[SKIP] bash/tar не знайдено")
    sys.exit(0)

tmp = Path(tempfile.mkdtemp())
src = tmp / "src" / "Cowork"
src.mkdir(parents=True)
for i in range(150):
    (src / f"f{i}.md").write_text(f"файл {i}\n", encoding="utf-8")
small = tmp / "small" / "Cowork"
small.mkdir(parents=True)
(small / "only.md").write_text("x", encoding="utf-8")


def tar_bytes(base):
    return subprocess.run([TAR, "-cf", "-", "-C", str(base.parent), base.name], capture_output=True).stdout


def posix(p):
    return str(p).replace("\\", "/")


def run(stream, root, expected=None, **env):
    e = {**os.environ, "BACKUP_ROOT": posix(root), **env}
    args = [BASH, posix(SCRIPT)] + ([str(expected)] if expected is not None else [])
    r = subprocess.run(args, input=stream, capture_output=True, env=e, timeout=120)
    return r.returncode, r.stdout.decode("utf-8", "replace")


good = tar_bytes(src)
root = tmp / "bk"

rc, out = run(good, root)
dirs = sorted(p.name for p in root.glob("cowork_*"))
chk("успішний знімок: код 0, SNAPSHOT_DONE, публікується без .part", rc == 0 and "SNAPSHOT_DONE" in out and len(dirs) == 1 and not dirs[0].endswith(".part"))

# --- ротація: два старі знімки + новий -> лишається KEEP=2 найновіших
for k, old in enumerate(("cowork_2026-01-01", "cowork_2026-01-02")):
    (root / old).mkdir()
    os.utime(root / old, (1_700_000_000 + k * 100_000, 1_700_000_000 + k * 100_000))   # різні й старші за сьогоднішній
rc, out = run(good, root)
dirs = sorted(p.name for p in root.glob("cowork_*"))
chk("ротація: лишилось 2 найновіших, найстаріший прибрано", rc == 0 and len(dirs) == 2 and "cowork_2026-01-01" not in dirs)

# --- мало місця: нічого не розпаковано, старі знімки цілі
before = sorted(p.name for p in root.glob("cowork_*"))
rc, out = run(good, root, MIN_FREE_GB="999999")
chk("мало місця: код 3, SNAPSHOT_FAILED, старі знімки цілі, .part немає",
    rc == 3 and "SNAPSHOT_FAILED" in out and sorted(p.name for p in root.glob("cowork_*")) == before)

# --- підозріло малий знімок (<100 файлів) не публікується і не ротує
rc, out = run(tar_bytes(small), root)
chk("<100 файлів: код 5, не опубліковано, старі цілі",
    rc == 5 and sorted(p.name for p in root.glob("cowork_*")) == before)

# --- битий потік
rc, out = run(good[: len(good) // 3], root)
after = sorted(p.name for p in root.glob("cowork_*"))
chk("обірваний/битий tar-потік: код != 0, .part прибрано, старі знімки цілі", rc != 0 and after == before)

# --- права
mode = oct((root).stat().st_mode & 0o777)
chk("корінь знімків має права 700 (там, де ФС це підтримує)", mode in ("0o700",) or os.name == "nt")

# --- аудит PR #965: кількість файлів від відправника, залишки .part, небезпечний корінь, predecessor того ж дня
before = sorted(p.name for p in root.glob("cowork_*"))
rc, out = run(good, root, expected=150)
chk("очікувана кількість збігається (150) -> опубліковано", rc == 0 and "SNAPSHOT_DONE" in out)
rc, out = run(good, root, expected=300)
chk("відправник запакував 300, дійшло 150 (обрив на межі запису) -> код 6, не опубліковано",
    rc == 6 and "SNAPSHOT_FAILED" in out and not list(root.glob("*.part")))
(root / "cowork_2020-01-01.part").mkdir()
(root / "cowork_2020-01-01.part" / "junk").write_text("x", encoding="utf-8")
rc, out = run(good, root, expected=150)
chk("залишок .part з ІНШОЇ дати прибирається при старті", rc == 0 and not list(root.glob("*.part")))
snap_today = sorted(p.name for p in root.glob("cowork_*"))
rc, out = run(good, root, expected=150, MIN_FREE_GB="999999")
chk("невдалий повторний запуск того ж дня НЕ чіпає опублікований знімок", rc == 3 and sorted(p.name for p in root.glob("cowork_*")) == snap_today)
rc, out = run(good, tmp / "x", BACKUP_ROOT="/")
chk("небезпечний BACKUP_ROOT='/' відхиляється", rc == 2 and "unsafe" in out)

shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if F else 0)
