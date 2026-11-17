#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_indexnow_ping.py — IndexNow (17.11.2026). Інваріанти: (1) ключ-файл site/<KEY>.txt існує й містить РІВНО ключ; (2) перший запуск лише запам'ятовує «зараз», нічого не шле; (3) шлемо лише адреси з sitemap-ів (noindex поза ними), де t зі стану збірки > last_ts;
(4) ключ не віддається сайтом → не пінгуємо, черга зберігається; (5) 429/5xx і ліміти не гублять адреси: невідправлене лишається в backlog і йде наступним запуском, без дублів; (6) денний ліміт; (7) INDEXNOW=0 вимикає;
(8) payload: host, key, keyLocation, urlList; (9) адреси чужих хостів/без запису у стані — не шлемо."""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import indexnow_ping as ix  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


kf = HERE / "site" / f"{ix.KEY}.txt"
chk("ключ-файл site/<KEY>.txt існує й містить рівно ключ (публічний за протоколом)", kf.exists() and kf.read_text(encoding="utf-8").strip() == ix.KEY and len(ix.KEY) == 32)
chk("юніт indexnow-ping.service: абсолютний venv-python; таймер після site-rebuild (:40)", "ExecStart=/opt/plutustoys/venv/bin/python3 " in (HERE / "indexnow-ping.service").read_text(encoding="utf-8") and ":40:00" in (HERE / "indexnow-ping.timer").read_text(encoding="utf-8"))


class Http:
    def __init__(self, key_ok=True, statuses=None):
        self.calls, self.key_ok, self.statuses = [], key_ok, list(statuses or [])

    def __call__(self, method, url, body=None, headers=None):
        self.calls.append((method, url, json.loads(body) if body else None))
        if method == "GET":
            return (200, ix.KEY) if self.key_ok else (404, "")
        return (self.statuses.pop(0) if self.statuses else 200), ""


def make_site(td, n=5, t0=1000.0):
    site = Path(td) / "site"
    site.mkdir()
    urls = [f"{ix.SITE_URL}/product-{i}.html" for i in range(n)] + [f"{ix.SITE_URL}/"]
    (site / "sitemap.xml").write_text('<?xml version="1.0"?><sitemapindex><sitemap><loc>%s/sitemap-products.xml</loc></sitemap></sitemapindex>' % ix.SITE_URL, encoding="utf-8")
    (site / "sitemap-products.xml").write_text("<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "<url><loc>https://evil.example/x.html</loc></url></urlset>", encoding="utf-8")
    lm = {f"product-{i}.html": {"h": "x", "t": t0 + i} for i in range(n)}
    lm["index.html"] = {"h": "x", "t": t0}
    lm["product-noindex.html"] = {"h": "x", "t": t0 + 99}          # нема в sitemap
    (site / ".htlastmod_state.json").write_text(json.dumps(lm), encoding="utf-8")
    return site, lm


with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    site, lm = make_site(td)
    sf = td / "state.json"
    h = Http()
    r = ix.run(now=2000.0, site_dir=site, state_file=sf, http=h)
    chk("перший запуск: baseline, жодного HTTP-запиту, стан записано", r["status"] == "baseline" and h.calls == [] and json.loads(sf.read_text())["last_ts"] == 2000.0)
    h = Http()
    r = ix.run(now=2100.0, site_dir=site, state_file=sf, http=h)
    chk("нічого не змінилось після baseline → нічого не шлемо", r["status"] == "nothing" and h.calls == [])
    # зміни: сторінка 1 і нова 7
    lm["product-1.html"]["t"] = 2150.0
    lm["product-3.html"]["t"] = 2160.0
    lm["product-noindex.html"]["t"] = 2170.0
    (site / ".htlastmod_state.json").write_text(json.dumps(lm), encoding="utf-8")
    h = Http()
    r = ix.run(now=2200.0, site_dir=site, state_file=sf, http=h)
    posts = [c for c in h.calls if c[0] == "POST"]
    chk("змінені адреси з sitemap піти в IndexNow: product-3 і product-1 (нові першими), noindex-сторінки й чужий хост — ні", r["status"] == "ok" and r["sent"] == 2 and len(posts) == 1 and posts[0][2]["urlList"] == [f"{ix.SITE_URL}/product-3.html", f"{ix.SITE_URL}/product-1.html"])
    p = posts[0][2]
    chk("payload: host, key, keyLocation = https://host/<key>.txt, endpoint api.indexnow.org", p["host"] == ix.HOST and p["key"] == ix.KEY and p["keyLocation"] == f"{ix.SITE_URL}/{ix.KEY}.txt" and posts[0][1] == ix.ENDPOINT)
    h = Http()
    r = ix.run(now=2300.0, site_dir=site, state_file=sf, http=h)
    chk("повторний запуск без змін → нічого (адреси не рахуються двічі)", r["status"] == "nothing" and not [c for c in h.calls if c[0] == "POST"])

    # ключ не віддається
    lm["product-2.html"]["t"] = 2400.0
    (site / ".htlastmod_state.json").write_text(json.dumps(lm), encoding="utf-8")
    h = Http(key_ok=False)
    r = ix.run(now=2500.0, site_dir=site, state_file=sf, http=h)
    chk("ключ не віддається сайтом → не пінгуємо; адреса лишається в черзі", r["status"] == "key_not_served" and r["sent"] == 0 and r["backlog"] == 1 and not [c for c in h.calls if c[0] == "POST"])
    h = Http()
    r = ix.run(now=2600.0, site_dir=site, state_file=sf, http=h)
    chk("наступного разу (ключ віддається) черга піде: product-2", r["sent"] == 1 and [c for c in h.calls if c[0] == "POST"][0][2]["urlList"] == [f"{ix.SITE_URL}/product-2.html"] and r["backlog"] == 0)

    # 429: черга зберігається, без дублів
    for i in range(5):
        lm[f"product-{i}.html"]["t"] = 3000.0 + i
    (site / ".htlastmod_state.json").write_text(json.dumps(lm), encoding="utf-8")
    h = Http(statuses=[429])
    r = ix.run(now=3100.0, site_dir=site, state_file=sf, http=h)
    chk("429 від IndexNow → нічого не прийнято, усі 5 адрес у черзі", r["status"] == "partial" and r["sent"] == 0 and r["backlog"] == 5)
    h = Http()
    r = ix.run(now=3200.0, site_dir=site, state_file=sf, http=h)
    sent_urls = [u for c in h.calls if c[0] == "POST" for u in c[2]["urlList"]]
    chk("наступний запуск шле чергу рівно один раз (5 адрес, без дублів)", r["sent"] == 5 and len(sent_urls) == 5 and len(set(sent_urls)) == 5 and r["backlog"] == 0)

    # ліміт за запуск
    old_run, old_batch = ix.MAX_PER_RUN, ix.BATCH
    ix.MAX_PER_RUN, ix.BATCH = 2, 2
    for i in range(5):
        lm[f"product-{i}.html"]["t"] = 4000.0 + i
    (site / ".htlastmod_state.json").write_text(json.dumps(lm), encoding="utf-8")
    h = Http()
    r1 = ix.run(now=4100.0, site_dir=site, state_file=sf, http=h)
    r2 = ix.run(now=4200.0, site_dir=site, state_file=sf, http=h)
    r3 = ix.run(now=4300.0, site_dir=site, state_file=sf, http=h)
    allsent = [u for c in h.calls if c[0] == "POST" for u in c[2]["urlList"]]
    chk("MAX_PER_RUN=2: 5 адрес розкладено на 3 запуски (2+2+1), усі різні, черга порожніє", (r1["sent"], r2["sent"], r3["sent"]) == (2, 2, 1) and len(set(allsent)) == 5 and r3["backlog"] == 0)
    ix.MAX_PER_RUN, ix.BATCH = old_run, old_batch

    # денний ліміт
    old_day = ix.MAX_PER_DAY
    ix.MAX_PER_DAY = 3
    for i in range(5):
        lm[f"product-{i}.html"]["t"] = 5000.0 + i
    (site / ".htlastmod_state.json").write_text(json.dumps(lm), encoding="utf-8")
    sf2 = td / "state2.json"
    ix.run(now=4900.0, site_dir=site, state_file=sf2, http=Http())          # baseline
    ra = ix.run(now=5100.0, site_dir=site, state_file=sf2, http=Http())
    rb = ix.run(now=5200.0, site_dir=site, state_file=sf2, http=Http())
    chk("MAX_PER_DAY=3: у межах доби йде 3 адреси, решта (2) чекає в черзі, далі «daily_cap»", ra["sent"] == 3 and ra["backlog"] == 2 and rb["status"] == "daily_cap" and rb["sent"] == 0)
    ix.MAX_PER_DAY = old_day

    # вимкнення
    os.environ["INDEXNOW"] = "0"
    h = Http()
    chk("INDEXNOW=0 → вимкнено, нічого не робимо", ix.run(now=9000.0, site_dir=site, state_file=td / "s3.json", http=h)["status"] == "disabled" and h.calls == [])
    os.environ.pop("INDEXNOW", None)

    # нема sitemap / нема стану збірки
    (site / "sitemap.xml").unlink()
    chk("нема sitemap → no_sitemap, нічого не шлемо", ix.run(now=9100.0, site_dir=site, state_file=sf, http=Http())["status"] == "no_sitemap")

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
