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

# --- аудит #1698 ---
import http.client  # noqa: E402
import urllib.request  # noqa: E402

# (1) гонка пінг ↔ збірка: sitemap уже записано, стан збірки ще старий; після flush нові сторінки НЕ губляться
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    site, lm = make_site(td, n=3, t0=1000.0)
    state_path = site / ".htlastmod_state.json"
    os.utime(state_path, (1500.0, 1500.0))                       # mtime ПОПЕРЕДНЬОЇ записаної версії стану
    sf = td / "st.json"
    ix.run(now=1600.0, site_dir=site, state_file=sf, http=Http())  # baseline: last_ts = min(1600, mtime 1500) = 1500
    chk("baseline: межа = min(зараз, mtime стану збірки), а не «зараз» (збірка пише стан ПІЗНІШЕ за sitemap)", json.loads(sf.read_text())["last_ts"] == 1500.0)
    # нові сторінки з t=1550 з'являються в sitemap, а стан збірки ще не перезаписано (stale: нема запису) → пінг нічого не бачить
    sm = (site / "sitemap-products.xml").read_text(encoding="utf-8").replace("</urlset>", f"<url><loc>{ix.SITE_URL}/product-new.html</loc></url></urlset>")
    (site / "sitemap-products.xml").write_text(sm, encoding="utf-8")
    h = Http()
    r = ix.run(now=1700.0, site_dir=site, state_file=sf, http=h)
    chk("між записом sitemap і записом стану: нової сторінки в стані ще нема → не шлемо, last_ts не просувається вперед за mtime стану", r["status"] == "nothing" and json.loads(sf.read_text())["last_ts"] == 1500.0)
    lm["product-new.html"] = {"h": "x", "t": 1550.0}              # збірка дописала стан (його mtime тепер новіший)
    state_path.write_text(json.dumps(lm), encoding="utf-8")
    os.utime(state_path, (1800.0, 1800.0))
    h = Http()
    r = ix.run(now=1900.0, site_dir=site, state_file=sf, http=h)
    posts = [c for c in h.calls if c[0] == "POST"]
    chk("після flush стану нова сторінка (t=1550 < «зараз» першого пінгу) ВСЕ ОДНО піде в IndexNow", r["sent"] == 1 and posts[0][2]["urlList"] == [f"{ix.SITE_URL}/product-new.html"])

# (2) 4xx: некоректну пачку пропускаємо, 429/403/5xx зупиняють без втрати черги
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    site, lm = make_site(td, n=4, t0=1000.0)
    sf = td / "st.json"
    ix.run(now=900.0, site_dir=site, state_file=sf, http=Http())           # baseline (last_ts = now: файлу стану mtime реальний → min(900, mtime) = 900)
    old_b = ix.BATCH
    ix.BATCH = 2
    h = Http(statuses=[422, 200])
    r = ix.run(now=5000.0, site_dir=site, state_file=sf, http=h)
    chk("422 на першій пачці (2 адреси): відхилено як некоректну (спожито), решта 3 прийнято; черга порожня (не блокується назавжди)", r["sent"] == 3 and r["backlog"] == 0 and r["status"] == "ok")
    for st_code in (403, 429, 503, 0):
        site2, lm2 = make_site(td / f"s{st_code}", n=2, t0=1000.0) if (td / f"s{st_code}").mkdir() is None else (None, None)
        sf2 = td / f"st{st_code}.json"
        ix.run(now=900.0, site_dir=site2, state_file=sf2, http=Http())
        h = Http(statuses=[st_code])
        r = ix.run(now=5000.0, site_dir=site2, state_file=sf2, http=h)
        chk(f"HTTP {st_code} → зупинка, жодної адреси не втрачено (backlog = 3: 2 картки + головна)", r["sent"] == 0 and r["backlog"] == 3 and r["status"] == "partial")
    ix.BATCH = old_b

# (3) швидкість: 30 000 змінених + 20 000 у черзі (квадратичний set(old) давав 81 с)
import time as _time  # noqa: E402
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    site = td / "site"
    site.mkdir()
    n = 30000
    urls = [f"{ix.SITE_URL}/product-{i}.html" for i in range(n)]
    (site / "sitemap.xml").write_text('<sitemapindex><sitemap><loc>%s/sitemap-products.xml</loc></sitemap></sitemapindex>' % ix.SITE_URL, encoding="utf-8")
    (site / "sitemap-products.xml").write_text("<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>", encoding="utf-8")
    (site / ".htlastmod_state.json").write_text(json.dumps({f"product-{i}.html": {"h": "x", "t": 9000.0 + i} for i in range(n)}), encoding="utf-8")
    sf = td / "st.json"
    sf.write_text(json.dumps({"last_ts": 1.0, "day": "2000-01-01", "day_count": 0, "backlog": urls[:20000]}), encoding="utf-8")
    t0 = _time.time()
    r = ix.run(now=20000.0, site_dir=site, state_file=sf, http=Http())
    chk("30 000 змінених + 20 000 у черзі обробляються < 5 с (лінійно, а не 81 с)", _time.time() - t0 < 5 and r["sent"] == ix.MAX_PER_RUN)

# (4) HTTPException (IncompleteRead/BadStatusLine) не валить скрипт
real_urlopen = urllib.request.urlopen


def boom(*a, **k):
    raise http.client.IncompleteRead(b"x")


urllib.request.urlopen = boom
try:
    st_, body_ = ix._http("GET", "https://example.invalid/x")
finally:
    urllib.request.urlopen = real_urlopen
chk("_http: IncompleteRead → (0, '') замість винятку (юніт не падає з кодом 1)", st_ == 0 and body_ == "")

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
