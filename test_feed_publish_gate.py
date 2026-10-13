#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_feed_publish_gate.py — гейт фіду перед публікацією на ФАКТАХ (без довільних порогів, рішення власника 13.10.2026) + мітка генерації (feed_publish_gate.sh, feed_meta.py).
Інваріанти: (1) відсутній/порожній файл, нечитабельний або ОБРІЗАНИЙ XML, XML без жодної позиції → блокує з причиною; (2) валідний XML з будь-якою кількістю позицій (навіть малою чи меншою, ніж було) → пускає
(жодних порогів 3000/30%); (3) FEED_GATE_OVERRIDE=1 пропускає; (4) feed_meta.json: generated_at = час ВИХІДНОГО файлу, не час публікації; не опублікований цього разу фід лишає ПОПЕРЕДНІЙ запис;
(5) ГОЛОВНЕ (запит Консультанта): публікація, що не містить файлів попередньої, НЕ затирає їх — на справжньому git: дві послідовні «публікації» в орфан-гілку по схемі publish_feed_pipeline_vps.sh
(`git checkout origin/feed-data -- .` + умовний перезапис), файли першої лишаються в дереві другої.
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


def feed(name, n, tag="item", truncate=False):
    p = os.path.join(tmp, name)
    open_t = f"<{tag}>" if tag == "item" else f"<{tag} id=\"1\">"
    root = "rss" if tag == "item" else "yml_catalog"
    body = f"<{root}>" + "".join(f"{open_t}x</{tag}>" for _ in range(n)) + f"</{root}>"
    if truncate:
        body = body[: len(body) - 20]            # обрізаний: немає закриваючих тегів
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    return p


def gate(new, env=None):
    e = dict(os.environ)
    e.pop("FEED_GATE_OVERRIDE", None)
    e["PLUTUS_PY"] = sys.executable.replace("\\", "/")
    e.update(env or {})
    r = subprocess.run(["bash", "-c", f'. "{GATE}"; feed_gate_ok "$1"', "x", new.replace("\\", "/")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=e)
    return r.returncode, r.stdout.strip()


rc, msg = gate(feed("n_big.xml", 300))
chk("валідний фід 300 позицій → пускає", rc == 0 and "300" in msg)
rc, msg = gate(feed("n_one.xml", 1))
chk("валідний фід з ОДНІЄЮ позицією → пускає (жодного мінімуму)", rc == 0)
rc, msg = gate(feed("n_small.xml", 7))
chk("валідний, але малий/менший за попередній фід → пускає (це правда про каталог, не поломка)", rc == 0)
rc, msg = gate(feed("n_zero.xml", 0))
chk("XML без жодної позиції → БЛОКУЄ з причиною", rc == 1 and "жодної позиції" in msg)
rc, msg = gate(feed("n_trunc.xml", 50, truncate=True))
chk("ОБРІЗАНИЙ XML → БЛОКУЄ з причиною", rc == 1 and "обрізаний" in msg)
trash = os.path.join(tmp, "trash.xml")
open(trash, "w", encoding="utf-8").write("це не xml <<<")
rc, msg = gate(trash)
chk("сміття замість XML → БЛОКУЄ", rc == 1)
empty = os.path.join(tmp, "empty.xml")
open(empty, "w").close()
rc, msg = gate(empty)
chk("порожній файл → БЛОКУЄ", rc == 1 and "порожній" in msg)
rc, msg = gate(os.path.join(tmp, "нема.xml"))
chk("файлу нема → БЛОКУЄ", rc == 1)
rc, msg = gate(feed("n_cut.xml", 0), {"FEED_GATE_OVERRIDE": "1"})
chk("FEED_GATE_OVERRIDE=1 пропускає свідомо", rc == 0 and "override" in msg)
rc, msg = gate(feed("offers_new.xml", 80, "offer"))
chk("YML-формат (<offer id=…>) рахується так само", rc == 0 and "80" in msg)
rc, msg = gate(feed("offers_trunc.xml", 80, "offer", truncate=True))
chk("YML обрізаний → БЛОКУЄ", rc == 1)
html = os.path.join(tmp, "page.html")
open(html, "w", encoding="utf-8").write("<html><body><item>x</item></body></html>")
rc, msg = gate(html)
chk("HTML-сторінка з випадковим <item> (не фід) → БЛОКУЄ: корінь не rss/yml_catalog", rc == 1 and "не фід" in msg)
rc, msg = gate(feed("n_ok.xml", 3), {"PLUTUS_PY": "/нема/такого/python"})
chk("збій самого гейта (нема інтерпретатора) → БЛОКУЄ з видимою причиною, не порожнім повідомленням", rc == 1 and "не зміг виконатись" in msg)
chk("жодних довільних порогів у скрипті гейта (немає 'min_abs', '70' і «30%»)", not any(x in open(os.path.join(HERE, "feed_publish_gate.sh"), encoding="utf-8").read().replace("0.70", "") for x in ("min_abs", "* 70", "понад 30%")))


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
