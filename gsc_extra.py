# -*- coding: utf-8 -*-
"""gsc_extra.py — розширення щоденного знімка Search Console (запит SEO 19.10.2026, пп.1–3): тижневий підсумок + watchlist запитів, ротація URL Inspection з історією індексації, guard верифікацій власності.

Чисті функції (тестуються без мережі) + три «частини» для gsc_report.PARTS (кожна незалежна, збій однієї не скасовує інших):
  • part_weekly_summary → gsc_weekly_summary_<дата>.csv (довгий формат: section,key1,key2,clicks,impressions,position,extra): розділи weekly (по тижнях від 2026-08-17: кліки, покази, середня позиція, різних запитів), buckets (покази за позицією 1–3/4–10/11–20/21–50/50+),
    pagetypes (покази/кліки за типом сторінки: старі /p…,/ua/p…,/g… | нові /product- | /category-,/group- | catalog | головна | інші), top_growth/top_drop (TOP-30 запитів за зміною показів: останні 7 діб проти попередніх 7), watchlist (позиція/покази/кліки за кожен запит із gsc_watchlist.csv).
  • part_inspect_rotation → gsc_inspect_<дата>.csv (порція ≈1/7 priority-URL) + накопичувальний gsc_inspect_history.csv (url, перший день «Submitted and indexed») + gsc_inspect_summary_<дата>.txt («проіндексовано X з Y»).
  • part_verification → gsc_verification_<дата>.txt: перевірка meta google-site-verification, файлу googleXXXX.html, meta facebook-domain-verification на нашому сайті та наявності властивості в sites.list; кожна невдала перевірка = рядок «🚩 …» (його підхоплює daily_files_to_shared → SEO_CHANNEL).
Позиція в рядку GSC — середня за (дата,запит,сторінка); середня по групі — зважена показами."""
import csv
import math
import re
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

FIRST_WEEK = date(2026, 8, 17)             # понеділок, з якого SEO просить тижневий ряд
WEEKLY_FIELDS = ["section", "key1", "key2", "clicks", "impressions", "position", "extra"]
BUCKETS = (("1-3", 0.0, 3.0), ("4-10", 3.0, 10.0), ("11-20", 10.0, 20.0), ("21-50", 20.0, 50.0), ("50+", 50.0, 1e9))   # lo < позиція <= hi
VERIFY_META_GOOGLE = "AzAID7jv3z26c8wFyrtnzjRTa_2xDyEnFx_R-neaoZY"
VERIFY_HTML_FILE = "googled732b3f7a0271df0.html"
VERIFY_HTML_BODY = "google-site-verification: googled732b3f7a0271df0.html"
VERIFY_META_FB = "zrxty9pynr6k0qh1y1tpgzkfyliv7s"
SITE = "https://plutustoys.com.ua"
WATCHLIST_FILE = Path(__file__).resolve().parent / "gsc_watchlist.csv"
INSPECT_HISTORY_FIELDS = ["url", "first_checked", "first_indexed", "last_checked", "last_state"]
INDEXED_STATE = "Submitted and indexed"


def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def page_type(url: str) -> str:
    """Тип сторінки за шляхом: old (/p…, /ua/p…, /g… старого Prom) | product | category | catalog | home | other."""
    m = re.match(r"https?://[^/]+(/[^?#]*)?", url or "")
    path = (m.group(1) if m and m.group(1) else "/")
    if re.match(r"^/(ua/)?[pg]\d", path) or re.match(r"^/(ua/)?[pg]-?\d", path):
        return "old"
    if path.startswith("/product-"):
        return "product"
    if path.startswith("/category-") or path.startswith("/group-"):
        return "category"
    if path.startswith("/catalog"):
        return "catalog"
    if path in ("/", "/index.html"):
        return "home"
    return "other"


def _agg(rows):
    imp = sum(r["impressions"] for r in rows)
    clk = sum(r["clicks"] for r in rows)
    pos = (sum(r["position"] * r["impressions"] for r in rows) / imp) if imp else 0.0
    return clk, imp, pos


def parse_rows(api_rows: list) -> list:
    """{keys:[date,query,page], clicks, impressions, position} → плоскі рядки; нечисловий/неповний рядок пропускається."""
    out = []
    for r in api_rows or []:
        k = r.get("keys") or []
        if len(k) != 3:
            continue
        try:
            d = date.fromisoformat(k[0])
        except ValueError:
            continue
        out.append({"date": d, "query": (k[1] or "").strip().lower(), "page": k[2] or "", "clicks": _num(r.get("clicks")), "impressions": _num(r.get("impressions")), "position": _num(r.get("position"))})
    return out


def query_stats(rows, start: date, end: date, match=None) -> tuple:
    sel = [r for r in rows if start <= r["date"] <= end and (match is None or match(r["query"]))]
    return _agg(sel)


def top_changes(rows, end: date, n: int = 30) -> tuple:
    """TOP-n зростань і падінь запитів за показами: [end−6, end] проти [end−13, end−7]."""
    cur_s, prev_s, prev_e = end - timedelta(days=6), end - timedelta(days=13), end - timedelta(days=7)
    cur, prev = defaultdict(list), defaultdict(list)
    for r in rows:
        if cur_s <= r["date"] <= end:
            cur[r["query"]].append(r)
        elif prev_s <= r["date"] <= prev_e:
            prev[r["query"]].append(r)
    res = []
    for q in set(cur) | set(prev):
        c_clk, c_imp, c_pos = _agg(cur.get(q, []))
        p_clk, p_imp, p_pos = _agg(prev.get(q, []))
        res.append((q, c_imp, p_imp, c_imp - p_imp, c_pos, p_pos, c_clk))
    growth = sorted((x for x in res if x[3] > 0), key=lambda x: (-x[3], x[0]))[:n]
    drop = sorted((x for x in res if x[3] < 0), key=lambda x: (x[3], x[0]))[:n]
    return growth, drop


def load_watchlist(path: Path | None = None) -> list:
    """[(запит у нижньому регістрі, режим 'exact'|'all_words')]: колонка query, необов'язкова match (exact за замовчуванням)."""
    out = []
    try:
        with open(path or WATCHLIST_FILE, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                q = (r.get("query") or "").strip().lower()
                if q:
                    out.append((q, (r.get("match") or "exact").strip() or "exact"))
    except OSError:
        return []
    return out


def make_matcher(q: str, mode: str):
    if mode == "all_words":
        words = q.split()
        return lambda s: all(w in s for w in words)
    return lambda s: s == q


def weekly_summary_rows(api_rows: list, end: date, watchlist: list) -> list:
    rows = parse_rows(api_rows)
    out = []
    if rows:
        weeks = defaultdict(list)
        for r in rows:
            if r["date"] >= FIRST_WEEK:
                weeks[week_start(r["date"])].append(r)
        for w in sorted(weeks):
            rs = weeks[w]
            clk, imp, pos = _agg(rs)
            out.append(["weekly", w.isoformat(), "", int(clk), int(imp), round(pos, 2), f"queries={len({r['query'] for r in rs if r['impressions'] > 0})}"])
            for name, lo, hi in BUCKETS:
                sel = [r for r in rs if lo < r["position"] <= hi]
                out.append(["buckets", w.isoformat(), name, int(sum(r["clicks"] for r in sel)), int(sum(r["impressions"] for r in sel)), "", ""])
            by_type = defaultdict(list)
            for r in rs:
                by_type[page_type(r["page"])].append(r)
            for t in ("old", "product", "category", "catalog", "home", "other"):
                c, i, p = _agg(by_type.get(t, []))
                out.append(["pagetypes", w.isoformat(), t, int(c), int(i), round(p, 2) if i else "", ""])
        growth, drop = top_changes(rows, end)
        for sec, lst in (("top_growth", growth), ("top_drop", drop)):
            for q, ci, pi, diff, cp, pp, cc in lst:
                out.append([sec, q, "", int(cc), int(ci), round(cp, 2) if ci else "", f"prev_impressions={int(pi)};diff={int(diff)};prev_position={round(pp, 2) if pi else ''}"])
        cur_s, prev_s, prev_e = end - timedelta(days=6), end - timedelta(days=13), end - timedelta(days=7)
        for q, mode in watchlist:
            m = make_matcher(q, mode)
            c_clk, c_imp, c_pos = query_stats(rows, cur_s, end, m)
            p_clk, p_imp, p_pos = query_stats(rows, prev_s, prev_e, m)
            a_clk, a_imp, a_pos = query_stats(rows, FIRST_WEEK, end, m)
            out.append(["watchlist", q, mode, int(c_clk), int(c_imp), round(c_pos, 2) if c_imp else "",
                        f"prev7_impressions={int(p_imp)};prev7_clicks={int(p_clk)};prev7_position={round(p_pos, 2) if p_imp else ''};since_aug17_impressions={int(a_imp)};since_aug17_clicks={int(a_clk)};since_aug17_position={round(a_pos, 2) if a_imp else ''}"])
    return out


def inspect_chunk(urls: list, today: date, days: int = 7) -> list:
    """Порція ≈1/days URL на сьогодні: індекс = today.toordinal() % days; за `days` діб покриває весь перелік."""
    if not urls:
        return []
    if len(urls) <= days:
        return list(urls)                      # короткий перелік беремо цілком (інакше частина днів була б порожньою)
    size = math.ceil(len(urls) / days)
    i = today.toordinal() % days
    return urls[i * size:(i + 1) * size]


def load_inspect_history(path: Path) -> dict:
    hist = {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r.get("url"):
                    hist[r["url"]] = r
    except (OSError, ValueError, csv.Error):
        pass                                  # відсутній/пошкоджений файл → порожня історія (аудит #852)
    return hist


def update_inspect_history(hist: dict, results: list, today: date) -> dict:
    """results — рядки INSPECT_FIELDS: [url, verdict, coverageState, …]. first_indexed = ПЕРШИЙ день зі станом «Submitted and indexed» (далі не перезаписується)."""
    d = today.isoformat()
    for r in results:
        url, state = r[0], (r[2] if len(r) > 2 else "")
        if str(r[1]).startswith("ERROR") or not state:
            continue                          # помилка або порожня відповідь не перезаписує останній відомий стан
        h = hist.get(url) or {"url": url, "first_checked": d, "first_indexed": "", "last_checked": d, "last_state": ""}
        h["last_checked"], h["last_state"] = d, state
        if state == INDEXED_STATE and not h.get("first_indexed"):
            h["first_indexed"] = d
        hist[url] = h
    return hist


def indexed_line(hist: dict, total_urls: int, today: date) -> str:
    known = len(hist)
    ok = sum(1 for h in hist.values() if h.get("last_state") == INDEXED_STATE)
    return f"{today:%Y-%m-%d}: проіндексовано {ok} з {known} перевірених URL (у priority-sitemap {total_urls}; стан кожного URL оновлюється раз на 7 діб)."


def check_verification(fetch, sites: list, site_url: str) -> list:
    """Рядки звіту: «✅ …» або «🚩 …» (причина). fetch(url) → (код, текст). Самодіагностика інциденту 18.10 (зникло підтвердження GSC)."""
    out = []
    code, html = fetch(SITE + "/")
    if code != 200:
        out.append(f"🚩 головна не відповіла 200 (код {code}): meta-верифікації перевірити неможливо")
    else:
        m = re.search(r'<meta[^>]*name=["\']google-site-verification["\'][^>]*content=["\']([^"\']+)["\']', html, re.I) or re.search(r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']google-site-verification["\']', html, re.I)
        if m and m.group(1) == VERIFY_META_GOOGLE:
            out.append("✅ meta google-site-verification на головній збігається")
        else:
            out.append(f"🚩 meta google-site-verification на головній {'відсутня' if not m else 'інша: ' + m.group(1)[:20] + '…'} (очікувана {VERIFY_META_GOOGLE[:8]}…)")
        f = re.search(r'<meta[^>]*name=["\']facebook-domain-verification["\'][^>]*content=["\']([^"\']+)["\']', html, re.I) or re.search(r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']facebook-domain-verification["\']', html, re.I)
        if f and f.group(1) == VERIFY_META_FB:
            out.append("✅ meta facebook-domain-verification на головній збігається")
        else:
            out.append(f"🚩 meta facebook-domain-verification на головній {'відсутня' if not f else 'інша'}")
    code, body = fetch(f"{SITE}/{VERIFY_HTML_FILE}")
    if code == 200 and VERIFY_HTML_BODY in (body or ""):
        out.append(f"✅ /{VERIFY_HTML_FILE} віддає очікуваний вміст")
    else:
        out.append(f"🚩 /{VERIFY_HTML_FILE}: код {code}, очікуваний вміст {'є' if VERIFY_HTML_BODY in (body or '') else 'відсутній'}")
    if sites is None:
        out.append("⚠️ sites.list не вдалося прочитати: перевірка властивості GSC пропущена")
    elif site_url in sites:
        out.append(f"✅ sites.list GSC містить {site_url}")
    else:
        out.append(f"🚩 sites.list GSC НЕ містить {site_url} (є: {', '.join(sites) or 'порожньо'}): доступ сервіс-акаунта втрачено")
    return out


def http_fetch(url: str, timeout: int = 30, retries: int = 1):
    """GET із одним повтором на мережевий збій (код 0): одиничний збій не має давати хибний 🚩."""
    for k in range(retries + 1):
        code, body = _http_fetch_once(url, timeout)
        if code != 0 or k == retries:
            return code, body
        import time as _t
        _t.sleep(3)
    return 0, ""


def _http_fetch_once(url: str, timeout: int = 30):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PlutusToys-gsc-guard/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:  # noqa: BLE001
        return 0, ""
