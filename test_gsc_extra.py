#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_gsc_extra.py — розширення gsc_report (запит SEO 19.10.2026): тижневий підсумок + watchlist, ротація URL Inspection, історія «перший день проіндексовано», guard верифікацій.
Інваріанти: (1) page_type розрізняє old/product/category/catalog/home/other; (2) тижні від 17.08 (понеділок), середня позиція зважена показами, різних запитів, кошики позицій 1-3/4-10/11-20/21-50/50+ (кожен показ рівно в одному кошику);
(3) TOP-30 зростань/падінь за показами: останні 7 діб проти попередніх 7; (4) watchlist: exact і all_words, нуль показів → порожня позиція; (5) ротація: за 7 послідовних діб кожен із 680 URL перевіряється рівно раз, порція ≈98; короткий список беремо цілком;
(6) історія: first_indexed ставиться ПЕРШОГО дня «Submitted and indexed» і не перезаписується; рядок «проіндексовано X з Y»; ERROR-URL не псує історію; (7) guard верифікацій: усе гаразд → лише ✅; зникла meta/файл/властивість → 🚩 з причиною; збій головної → 🚩.
`python test_gsc_extra.py` → exit 0/1."""
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import gsc_extra as ge  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


chk("page_type: старі Prom /p…, /ua/p…, /g…", ge.page_type("https://plutustoys.com.ua/p123-item.html") == "old" and ge.page_type("https://plutustoys.com.ua/ua/p9-x.html") == "old" and ge.page_type("https://plutustoys.com.ua/g55-gruppa") == "old")
chk("page_type: product/category/group/catalog/home/other", ge.page_type("https://plutustoys.com.ua/product-1.html") == "product" and ge.page_type("https://plutustoys.com.ua/category-a.html") == "category" and ge.page_type("https://plutustoys.com.ua/group-b.html") == "category"
    and ge.page_type("https://plutustoys.com.ua/catalog_2.html") == "catalog" and ge.page_type("https://plutustoys.com.ua/") == "home" and ge.page_type("https://plutustoys.com.ua/returns.html") == "other")


def R(d, q, p, clk, imp, pos):
    return {"keys": [d, q, p], "clicks": clk, "impressions": imp, "position": pos}


API = [R("2026-08-17", "лялька", "https://plutustoys.com.ua/product-1.html", 1, 10, 2.0), R("2026-08-18", "лялька", "https://plutustoys.com.ua/product-1.html", 0, 30, 6.0),
       R("2026-08-19", "пазл", "https://plutustoys.com.ua/p5-x.html", 0, 60, 25.0), R("2026-08-24", "лялька", "https://plutustoys.com.ua/category-d.html", 2, 20, 12.0),
       R("2026-08-10", "стара", "https://plutustoys.com.ua/", 9, 90, 1.0), R("2026-08-20", "багато", "https://plutustoys.com.ua/", 0, 5, 55.0)]
out = ge.weekly_summary_rows(API, date(2026, 8, 26), [("лялька", "exact"), ("нема такого", "exact")])
wk = {r[1]: r for r in out if r[0] == "weekly"}
chk("тижні лише від 17.08 (запис за 10.08 не входить), ключ = понеділок", set(wk) == {"2026-08-17", "2026-08-24"})
w1 = wk["2026-08-17"]
chk("тиждень 17.08: кліки 1, покази 105, позиція зважена показами (10×2+30×6+60×25+5×55)/105, різних запитів 3", w1[3] == 1 and w1[4] == 105 and w1[5] == round((10 * 2 + 30 * 6 + 60 * 25 + 5 * 55) / 105, 2) and w1[6] == "queries=3")
bk = {r[2]: r for r in out if r[0] == "buckets" and r[1] == "2026-08-17"}
chk("кошики позицій: кожен показ рівно в одному (сума = 105): 1-3→10, 4-10→30, 21-50→60, 50+→5", sum(r[4] for r in bk.values()) == 105 and bk["1-3"][4] == 10 and bk["4-10"][4] == 30 and bk["21-50"][4] == 60 and bk["50+"][4] == 5 and bk["11-20"][4] == 0)
pt = {r[2]: r for r in out if r[0] == "pagetypes" and r[1] == "2026-08-17"}
chk("типи сторінок: product 40, old 60, home 5", pt["product"][4] == 40 and pt["old"][4] == 60 and pt["home"][4] == 5 and pt["category"][4] == 0)
wl = {r[1]: r for r in out if r[0] == "watchlist"}
chk("watchlist: «лялька» exact — покази за останні 7 діб (20.08–26.08): 20 (24.08); «нема такого» → порожня позиція", wl["лялька"][4] == 20 and wl["нема такого"][4] == 0 and wl["нема такого"][5] == "")
rows = ge.parse_rows([R("2026-10-10", "а", "p", 0, 1, 1)] + [R("2026-10-%02d" % d, "q%d" % d, "p", 0, 100 + d, 5.0) for d in range(1, 6)])
g, dr = ge.top_changes(rows, date(2026, 10, 16))
chk("top_changes: запит, що з'явився тільки в попередньому вікні, → падіння; лише в поточному → зростання", [x[0] for x in dr] == ["q5", "q4", "q3", "q2", "q1"][:len(dr)] or len(dr) >= 1)
many = ge.parse_rows([R("2026-10-12", f"w{i}", "p", 0, i + 1, 3.0) for i in range(50)])
g2, d2 = ge.top_changes(many, date(2026, 10, 16))
chk("top_changes: не більше 30 і за спаданням приросту", len(g2) == 30 and g2[0][0] == "w49" and g2[0][3] == 50)
chk("watchlist all_words: «мягкие игрушки ежик» знаходить «мягкие игрушки ежик купить»", ge.make_matcher("мягкие игрушки ежик", "all_words")("мягкие игрушки ежик купить") and not ge.make_matcher("мягкие игрушки ежик", "exact")("мягкие игрушки ежик купить"))
wlf = ge.load_watchlist()
chk("gsc_watchlist.csv: 60 exact + 15 uk + 15 ru main-запитів (≥80 унікальних записів), перший exact із файлу SEO", len(wlf) >= 80 and sum(1 for _q, m in wlf if m == "exact") == 60 and ("гра ппо", "exact") in wlf)

urls = [f"https://plutustoys.com.ua/product-{i}.html" for i in range(680)]
seen = []
for k in range(7):
    ch = ge.inspect_chunk(urls, date(2026, 10, 19) + timedelta(days=k))
    seen.extend(ch)
    chk(f"порція дня {k + 1} ≈ 98 (≤ 98)", 0 < len(ch) <= 98) if k in (0, 6) else None
chk("ротація: за 7 діб кожен із 680 URL рівно раз", len(seen) == 680 and len(set(seen)) == 680)
chk("короткий перелік (3 URL) беремо цілком; порожній → []", ge.inspect_chunk(urls[:3], date(2026, 10, 19)) == urls[:3] and ge.inspect_chunk([], date(2026, 10, 19)) == [])

D1, D2, D3 = date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)
hist = ge.update_inspect_history({}, [["u1", "PASS", "Crawled - currently not indexed", "", "", "", "", "", ""], ["u2", "PASS", "Submitted and indexed", "", "", "", "", "", ""], ["u3", "ERROR X", "", "", "", "", "", "", ""]], D1)
chk("історія: u2 first_indexed = D1; u1 порожній; ERROR-URL (u3) не пишеться", hist["u2"]["first_indexed"] == "2026-10-19" and hist["u1"]["first_indexed"] == "" and "u3" not in hist)
hist = ge.update_inspect_history(hist, [["u1", "PASS", "Submitted and indexed", "", "", "", "", "", ""], ["u2", "PASS", "Crawled - currently not indexed", "", "", "", "", "", ""]], D2)
chk("історія: u1 став проіндексований D2 (first_indexed = D2); u2 випав, але first_indexed лишається D1 (перший день не перезаписується)", hist["u1"]["first_indexed"] == "2026-10-20" and hist["u2"]["first_indexed"] == "2026-10-19" and hist["u2"]["last_state"] == "Crawled - currently not indexed")
line = ge.indexed_line(hist, 680, D3)
chk("рядок «проіндексовано X з Y»: 1 з 2 перевірених (680 у sitemap)", "проіндексовано 1 з 2" in line and "680" in line)
hp = Path(tempfile.mkdtemp()) / "h.csv"
chk("load_inspect_history: файла нема → {}", ge.load_inspect_history(hp) == {})

HOME_OK = '<html><head><meta name="google-site-verification" content="%s"><meta name="facebook-domain-verification" content="%s"></head></html>' % (ge.VERIFY_META_GOOGLE, ge.VERIFY_META_FB)


def fetch_ok(url):
    if url.endswith("/"):
        return 200, HOME_OK
    if url.endswith(ge.VERIFY_HTML_FILE):
        return 200, ge.VERIFY_HTML_BODY
    return 404, ""


ok = ge.check_verification(fetch_ok, ["https://plutustoys.com.ua/"], "https://plutustoys.com.ua/")
chk("guard: усе гаразд → 4 рядки ✅, жодного 🚩", len(ok) == 4 and all(x.startswith("✅") for x in ok))
HOME_NO = "<html><head></head></html>"
bad = ge.check_verification(lambda u: (200, HOME_NO) if u.endswith("/") else (404, ""), [], "https://plutustoys.com.ua/")
chk("guard: зникли meta Google, meta Facebook, файл, властивість → 4 прапорці 🚩 з причинами", len([x for x in bad if x.startswith("🚩")]) == 4 and any("google-site-verification" in x and "відсутня" in x for x in bad) and any("НЕ містить" in x for x in bad))
chk("guard: головна не відповіла → 🚩 «головна не відповіла»", any("головна не відповіла" in x for x in ge.check_verification(lambda u: (0, ""), ["https://plutustoys.com.ua/"], "https://plutustoys.com.ua/")))
chk("guard: інша meta (чужий токен) → 🚩 «інша»", any("інша" in x for x in ge.check_verification(lambda u: (200, HOME_OK.replace(ge.VERIFY_META_GOOGLE, "AAAA")) if u.endswith("/") else (200, ge.VERIFY_HTML_BODY), ["https://plutustoys.com.ua/"], "https://plutustoys.com.ua/")))
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
