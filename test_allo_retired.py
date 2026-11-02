#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_allo_retired.py — ALLO прибрано з автоциклу й фідів (рішення власника 02.11.2026: «прибери, і фід теж»).
Інваріанти: (1) run_marketplace_actions.main не запускає жодного кроку ALLO, код виходу = код EVA; (2) run_feed_pipeline_vps.sh не генерує allo_feed.xml;
(3) publish_feed_pipeline_vps.sh не публікує його в циклі; (4) блок ALLO-RETIRE справді видаляє allo_feed.xml з дерева feed-data (реальний git, ідемпотентно),
не чіпаючи інших фідів; (5) feed_meta.update_meta прибирає запис allo_feed.xml, а решту лишає.
`python test_allo_retired.py` → exit 0/1."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import feed_meta  # noqa: E402
import run_marketplace_actions as r  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def code_lines(name):
    return [l for l in (HERE / name).read_text(encoding="utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]


# (1) автоцикл
os.environ["AUDIT_REPORT_DIR"] = tempfile.mkdtemp()
for rc_eva, want in ((0, 0), (1, 1)):
    calls = []
    r._run = lambda label, cmd, timeout=None, _c=calls, _rc=rc_eva: (_c.append((label, " ".join(map(str, cmd)))), _rc if "eva_cabinet_scraper" in " ".join(map(str, cmd)) else 0)[1]
    sys.argv = ["run_marketplace_actions.py", "--dry-run"]
    try:
        r.main()
        got = 0
    except SystemExit as e:
        got = e.code
    chk(f"автоцикл: жодного кроку ALLO (викликано {len(calls)} кроків)", calls and not any("allo" in (a + b).lower() for a, b in calls))
    chk(f"автоцикл: EVA rc={rc_eva} → код виходу {want} (отримали {got})", got == want)
chk("автоцикл: крок EVA лишився", any("eva_cabinet_scraper" in b and "--full-import" in b for _, b in calls))

# (2)(3) скрипти VPS
chk("run_feed_pipeline_vps.sh: generate_allo_feed.py не викликається", not any("allo" in l.lower() for l in code_lines("run_feed_pipeline_vps.sh")))
pub = code_lines("publish_feed_pipeline_vps.sh")
loop = [l for l in pub if l.startswith("for f in")]
chk("publish_feed_pipeline_vps.sh: цикл публікації без allo_feed.xml, але з eva_feed.xml", len(loop) == 1 and "allo" not in loop[0] and "eva_feed.xml" in loop[0])

# (4) блок ALLO-RETIRE на справжньому git
src = (HERE / "publish_feed_pipeline_vps.sh").read_text(encoding="utf-8")
m = re.search(r"# >>> ALLO-RETIRE.*?\n(.*?)# <<< ALLO-RETIRE", src, re.S)
chk("блок ALLO-RETIRE знайдено в publish_feed_pipeline_vps.sh", bool(m))
bash = shutil.which("bash")
if m and bash:
    block = "set -e\n" + m.group(1)
    td = Path(tempfile.mkdtemp())
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def git(cwd, *a):
        return subprocess.run(["git", *a], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8")

    git(td, "init", "-q", "--bare", "origin.git")
    seed = td / "seed"
    seed.mkdir()
    git(seed, "init", "-q")
    git(seed, "checkout", "-q", "-b", "feed-data")
    (seed / "feeds").mkdir()
    for f in ("allo_feed.xml", "eva_feed.xml", "meta_feed.xml"):
        (seed / "feeds" / f).write_text(f"<yml>{f}</yml>", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "push", "-q", str(td / "origin.git"), "feed-data")
    work = td / "work"
    work.mkdir()
    git(work, "init", "-q")
    git(work, "remote", "add", "origin", str(td / "origin.git"))
    git(work, "fetch", "-q", "--depth", "1", "origin", "feed-data")
    git(work, "checkout", "-q", "--orphan", "feed-data")
    git(work, "checkout", "origin/feed-data", "--", ".")
    chk("прекондиція: після restore allo_feed.xml є в дереві (це й був би вічний хвіст)", (work / "feeds" / "allo_feed.xml").exists())
    p = subprocess.run([bash, "-c", block], cwd=work, env=env, capture_output=True, text=True, encoding="utf-8")
    chk(f"блок ALLO-RETIRE відпрацював без помилки (rc={p.returncode} {p.stderr.strip()[:120]})", p.returncode == 0)
    git(work, "add", "-f", "feeds/eva_feed.xml")
    git(work, "commit", "-q", "-m", "pub")
    tree = git(work, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
    chk(f"у комміті на публікацію НЕМАЄ allo_feed.xml, інші фіди на місці ({tree})", "feeds/allo_feed.xml" not in tree and "feeds/eva_feed.xml" in tree and "feeds/meta_feed.xml" in tree)
    p2 = subprocess.run([bash, "-c", block], cwd=work, env=env, capture_output=True, text=True, encoding="utf-8")
    chk("повторний прогін (файла вже нема) теж ок — ідемпотентно, set -e не падає", p2.returncode == 0)
    shutil.rmtree(td, ignore_errors=True)
else:
    print("[SKIP] bash недоступний — git-перевірка блоку пропущена")

# (5) feed_meta
w = Path(tempfile.mkdtemp())
(w / "feeds").mkdir()
(w / "feeds" / "feed_meta.json").write_text(json.dumps({"allo_feed.xml": {"offers": 448}, "meta_feed.xml": {"offers": 5}}), encoding="utf-8")
src_dir = Path(tempfile.mkdtemp())
(src_dir / "eva_feed.xml").write_text("<offer id='1'></offer>", encoding="utf-8")
meta = feed_meta.update_meta(str(w), str(src_dir), ["eva_feed.xml"])
chk("feed_meta: запис allo_feed.xml прибрано, meta_feed.xml лишився, eva_feed.xml додано", "allo_feed.xml" not in meta and meta.get("meta_feed.xml") == {"offers": 5} and "eva_feed.xml" in meta)
chk("feed_meta: на диску теж без allo_feed.xml", "allo_feed.xml" not in json.loads((w / "feeds" / "feed_meta.json").read_text(encoding="utf-8")))

print("\nFAIL:" if F else "\nУСІ OK", F if F else "")
sys.exit(1 if F else 0)
