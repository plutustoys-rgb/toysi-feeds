#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_feed_publish_gate.py — гейт розміру фіду перед публікацією + мітка генерації (feed_publish_gate.sh, feed_meta.py).
Інваріанти: (1) порожній/обрізаний фід НЕ пускається (абсолютний мінімум і просідання >30% проти опублікованого); (2) нормальне зростання/невелике просідання пускається;
(3) FEED_GATE_OVERRIDE=1 пропускає свідоме скорочення; (4) feed_meta.json: generated_at = час ВИХІДНОГО файлу, не час публікації; не опублікований цього разу фід лишає ПОПЕРЕДНІЙ запис;
(5) ГОЛОВНЕ (запит Консультанта): публікація, що не містить файлів попередньої, НЕ затирає їх — на справжньому git: дві послідовні «публікації» в орфан-гілку по схемі
publish_feed_pipeline_vps.sh (`git checkout origin/feed-data -- .` + умовний перезапис), файли першої лишаються в дереві другої.
Потрібні bash і git у PATH. `python test_feed_publish_gate.py` → exit 0/1."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "feed_publish_gate.sh").replace("\\", "/")
sys.path.insert(0, HERE)
import feed_meta  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


tmp = tempfile.mkdtemp()


def feed(name, n, tag="item"):
    p = os.path.join(tmp, name)
    open_t = f"<{tag}>" if tag == "item" else f"<{tag} id=\"1\">"
    with open(p, "w", encoding="utf-8") as f:
        f.write("<root>" + "".join(f"{open_t}x</{tag}>" for _ in range(n)) + "</root>")
    return p


def gate(new, old, mn, env=None):
    e = dict(os.environ)
    e.pop("FEED_GATE_OVERRIDE", None)
    e.update(env or {})
    r = subprocess.run(["bash", "-c", f'. "{GATE}"; feed_gate_ok "$1" "$2" "$3"', "x", new.replace("\\", "/"), old.replace("\\", "/"), str(mn)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=e)
    return r.returncode, r.stdout.strip()


old_ok = feed("old.xml", 100)
rc, _ = gate(feed("n_same.xml", 100), old_ok, 10)
chk("той самий розмір → пускає", rc == 0)
rc, _ = gate(feed("n_grow.xml", 300), old_ok, 10)
chk("зростання (11837 проти 5980 на практиці) → пускає", rc == 0)
rc, _ = gate(feed("n_minus20.xml", 80), old_ok, 10)
chk("просідання на 20% → пускає", rc == 0)
rc, msg = gate(feed("n_minus40.xml", 60), old_ok, 10)
chk("просідання на 40% → БЛОКУЄ з причиною", rc == 1 and "30%" in msg)
rc, msg = gate(feed("n_zero.xml", 0), old_ok, 10)
chk("порожній фід → БЛОКУЄ (нижче абсолютного мінімуму)", rc == 1 and "мінімум" in msg)
rc, msg = gate(os.path.join(tmp, "нема.xml"), old_ok, 10)
chk("файлу нема → БЛОКУЄ", rc == 1)
rc, _ = gate(feed("first.xml", 50), os.path.join(tmp, "нема_раніше.xml"), 10)
chk("перша публікація (опублікованого ще нема) → пускає, якщо вище мінімуму", rc == 0)
rc, _ = gate(feed("n_tiny_first.xml", 5), os.path.join(tmp, "нема_раніше.xml"), 10)
chk("перша публікація нижче абсолютного мінімуму → блокує", rc == 1)
rc, msg = gate(feed("n_cut.xml", 20), old_ok, 10, {"FEED_GATE_OVERRIDE": "1"})
chk("FEED_GATE_OVERRIDE=1 пропускає свідоме скорочення", rc == 0 and "override" in msg)
rc, _ = gate(feed("offers_new.xml", 80, "offer"), feed("offers_old.xml", 100, "offer"), 10)
chk("YML-формат (<offer id=…>) рахується так само", rc == 0)
rc, _ = gate(feed("offers_cut.xml", 50, "offer"), os.path.join(tmp, "offers_old.xml"), 10)
chk("YML: просідання 50% → блокує", rc == 1)

# ── feed_meta
work = os.path.join(tmp, "work")
src = os.path.join(tmp, "src")
os.makedirs(os.path.join(work, "feeds"))
os.makedirs(src)
shutil.copy(feed("g.xml", 7), os.path.join(src, "google_merchant_feed.xml"))
shutil.copy(feed("e.xml", 3, "offer"), os.path.join(src, "eva_feed.xml"))
past = time.time() - 5 * 3600
os.utime(os.path.join(src, "google_merchant_feed.xml"), (past, past))
m = feed_meta.update_meta(work, src, ["google_merchant_feed.xml", "eva_feed.xml"])
chk("feed_meta: кількість позицій і розмір", m["google_merchant_feed.xml"]["offers"] == 7 and m["eva_feed.xml"]["offers"] == 3 and m["eva_feed.xml"]["bytes"] > 0)
from datetime import datetime, timezone  # noqa: E402
age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(m["google_merchant_feed.xml"]["generated_at"])).total_seconds() / 3600
chk("feed_meta: generated_at = час ВИХІДНОГО файлу (≈5 год тому), а не час публікації", 4.9 < age_h < 5.1)
prev = m["google_merchant_feed.xml"]["generated_at"]
m2 = feed_meta.update_meta(work, src, ["eva_feed.xml"])      # google цього разу НЕ опубліковано (заблоковано гейтом)
chk("feed_meta: неопублікований цього разу фід лишає ПОПЕРЕДНІЙ запис (вік видно чесно)", m2["google_merchant_feed.xml"]["generated_at"] == prev)
chk("feed_meta: файл на диску — валідний JSON без .tmp", json.load(open(os.path.join(work, "feeds", "feed_meta.json"), encoding="utf-8")) == m2 and not [x for x in os.listdir(os.path.join(work, "feeds")) if x.endswith(".tmp")])

# ── (5) справжній git: друга публікація не затирає файли першої
def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout


remote = os.path.join(tmp, "remote.git")
subprocess.run(["git", "init", "-q", "--bare", remote], check=True)


def publish(files):
    """схема publish_feed_pipeline_vps.sh: свіжий каталог, orphan, підтягнути ПОВНЕ дерево feed-data, умовно перезаписати файли цього прогону, force-push."""
    wd = tempfile.mkdtemp(dir=tmp)
    git(wd, "init", "-q")
    git(wd, "remote", "add", "origin", remote)
    git(wd, "config", "user.name", "t")
    git(wd, "config", "user.email", "t@t")
    subprocess.run(["git", "fetch", "--depth", "1", "origin", "feed-data"], cwd=wd, capture_output=True)
    git(wd, "checkout", "--orphan", "feed-data", "-q")
    subprocess.run(["git", "checkout", "origin/feed-data", "--", "."], cwd=wd, capture_output=True)
    os.makedirs(os.path.join(wd, "feeds"), exist_ok=True)
    for name, content in files.items():
        with open(os.path.join(wd, "feeds", name), "w", encoding="utf-8") as f:
            f.write(content)
        git(wd, "add", "-f", f"feeds/{name}")
    git(wd, "commit", "-q", "-m", "publish")
    git(wd, "push", "-q", "--force", remote, "feed-data:feed-data")


def published_tree():
    return set(git(remote, "ls-tree", "-r", "--name-only", "feed-data").split())


publish({"google_merchant_feed.xml": "G1", "meta_feed.xml": "M1", "bing_feed.xml": "B1"})      # публікація 1: google/meta/bing
publish({"prom_feed_top.xml": "P1", "eva_feed.xml": "E1"})                                      # публікація 2: інші файли, БЕЗ файлів першої
tree = published_tree()
chk("друга публікація НЕ затерла файли першої (google/meta/bing лишились у дереві)", {"feeds/google_merchant_feed.xml", "feeds/meta_feed.xml", "feeds/bing_feed.xml"} <= tree)
chk("і додала свої (prom, eva)", {"feeds/prom_feed_top.xml", "feeds/eva_feed.xml"} <= tree)
chk("вміст файлів першої не змінився", git(remote, "show", "feed-data:feeds/google_merchant_feed.xml") == "G1")

shutil.rmtree(tmp, ignore_errors=True)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
