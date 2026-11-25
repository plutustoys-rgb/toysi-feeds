# -*- coding: utf-8 -*-
"""eva_competitor_scan.py — ціни КОНКУРЕНТІВ на EVA для наших товарів (ЛИШЕ ЧИТАННЯ; нічого не міняє в цінах/фідах). Запуск — на ПК власника (справжній Chrome, його IP), НЕ на VPS (EVA/Cloudflare блокує дата-центр на 2-му запиті).

ЩО РОБИТЬ: обходить сторінки КАТЕГОРІЙ eva.ua (`/ua/<id>/<slug>/?p=N`; ці адреси дозволені robots.txt EVA: `Allow: /*?p=`; пошук `?q=` НЕ використовуємо — він заборонений `Disallow: /*?`), з кожної картки бере назву, ціну, стару ціну, наявність.
Потім зіставляє з нашим EVA-фідом (eva_feed.xml): картка = «наш товар», якщо код Toysi з нашого фіду стоїть у назві картки у дужках `(304218)` АБО нормалізована назва збігається з нашою `name_ua`. Картка з ціною, що збігається з нашою (±0,5 ₴), вважається НАШОЮ; решта — конкуренти. Мінімум конкурента — у виході.
ЯК ПОВОДИТЬСЯ З ЗАХИСТОМ: темп людини (--delay, дефолт 25 с + джиттер), ПЕРШИЙ 403/«Attention Required»/Cloudflare → скрипт ЗУПИНЯЄТЬСЯ й зберігає те, що зібрав. Жодного обходу захисту (без проксі, без підміни відбитків).
СТАН 25.11 (пілот): режим Playwright (scan()) EVA/Cloudflare БЛОКУЄ (403 на 1-му запиті без вікна, на 2-му з вікном) — НЕ обходимо. Працює збір з РЕАЛЬНОГО Chrome власника: JS fetch() сторінок категорій у відкритій вкладці eva.ua (cookies сесії, 20–28 с між сторінками, стоп на першому блоці) → файл JSON у Downloads → `parse_card` + `match` цього модуля. 12 сторінок = 12×200, 0 блоків.
ВИХІД: eva_competitors_<дата>.json — {"meta": {...}, "cards": [{"pr","url","name","price","old","in_stock","cat"}], "matched": {"<код Toysi>": {"ours": <ціна нашого фіду>, "competitors": [{"price","pr","name"}], "min_comp": <число|null>}}}.
Запуск: python eva_competitor_scan.py --seeds https://eva.ua/ua/13821/antistress/ --max-pages 3 --delay 25 --feed <шлях до eva_feed.xml> --out <каталог>"""
import argparse
import json
import random
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

FEED_URL = "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/eva_feed.xml"
BLOCK_MARKERS = ("Attention Required", "Sorry, you have been blocked", "Just a moment")

JS_CARDS = r"""
() => {
  const out = new Map();
  for (const a of document.querySelectorAll('a[href]')) {
    const m = (a.getAttribute('href') || '').match(/\/pr(\d+)\/?(#.*)?$/);
    if (!m) continue;
    const pr = m[1];
    if (out.has(pr)) continue;
    let c = a, best = null;
    for (let i = 0; i < 6 && c; i++, c = c.parentElement) {
      const t = (c.innerText || '').replace(/\s+/g, ' ').trim();
      const prs = new Set([...c.querySelectorAll('a[href]')].map(x => (x.getAttribute('href') || '').match(/\/pr(\d+)/)).filter(Boolean).map(x => x[1]));
      if (prs.size > 1) break;
      if (/₴|Немає в наявності/.test(t)) best = t;
    }
    if (best) out.set(pr, best.slice(0, 400));
  }
  return [...out.entries()].map(([pr, text]) => ({pr, text}));
}
"""


def parse_card(text: str) -> dict:
    """Текст картки → name/price/old/in_stock. Ціни — «1 009 ₴» / «1 025,81 ₴»; поточна = остання ціна в картці (стара — перша, якщо є знижка «-15%»)."""
    prices = [float(p.replace(" ", "").replace(" ", "").replace(",", ".")) for p in re.findall(r"(\d[\d  ]*(?:,\d{1,2})?)\s*₴", text)]
    in_stock = "Немає в наявності" not in text
    name = re.split(r"\d[\d  ]*(?:,\d{1,2})?\s*₴|Немає в наявності", text, maxsplit=1)[0].strip()
    name = re.sub(r"^(Промокод|Акція)\s+", "", name)
    price = prices[-1] if prices else None
    old = prices[0] if len(prices) > 1 and re.search(r"-\d+\s*%", text) else None
    return {"name": name, "price": price, "old": old, "in_stock": in_stock}


def norm_name(s: str) -> str:
    s = re.sub(r"\([^)]*\)", " ", s.casefold())          # прибираємо всі дужки: коди/кольори/артикули
    return " ".join(re.sub(r"[^\w]+", " ", s).split())


def load_feed(src: str) -> dict:
    """→ {код_Toysi: {"price": float, "name": str, "available": bool}} з eva_feed.xml (файл або URL)."""
    if src.startswith("http"):
        raw = urllib.request.urlopen(urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"}), timeout=120).read().decode("utf-8", "replace")
    else:
        raw = Path(src).read_text(encoding="utf-8", errors="replace")
    out = {}
    for m in re.finditer(r'<offer id="([^"]+)"[^>]*available="([^"]+)"[^>]*>(.*?)</offer>', raw, re.S):
        b = m.group(3)
        p = re.search(r"<price>([\d.]+)</price>", b)
        n = re.search(r"<name_ua>([^<]*)</name_ua>", b)
        if p and n and m.group(1).isdigit():                # лише числові id Toysi (rt-/kv- мають інші ключі — не мішаємо)
            out[m.group(1)] = {"price": float(p.group(1)), "name": n.group(1), "available": m.group(2) == "true"}
    return out


def match(cards: list, feed: dict, own_check: bool = True) -> dict:
    """Зіставлення: код у дужках у назві картки АБО збіг нормалізованої назви. Картка з ціною ±0,5 від нашої — наша; решта — конкуренти."""
    by_name = {}
    for code, f in feed.items():
        by_name.setdefault(norm_name(f["name"]), []).append(code)
    res = {}
    for c in cards:
        if c["price"] is None:
            continue
        codes = set(re.findall(r"\((\d{3,7})\)", c["name"])) & set(feed)
        if not codes:
            cand = by_name.get(norm_name(c["name"]), [])
            codes = set(cand) if len(cand) == 1 else set()   # неоднозначний збіг назв — не вгадуємо
        for code in codes:
            r = res.setdefault(code, {"ours": feed[code]["price"], "competitors": [], "own_cards": 0})
            if own_check and abs(c["price"] - feed[code]["price"]) <= 0.5:
                r["own_cards"] += 1
            elif c["in_stock"]:
                r["competitors"].append({"price": c["price"], "pr": c["pr"], "name": c["name"][:90]})
    for r in res.values():
        r["min_comp"] = min((x["price"] for x in r["competitors"]), default=None)
    return res


def scan(seeds: list, max_pages: int, delay: float, out_dir: Path, feed_src: str, headless: bool = True) -> Path:
    from playwright.sync_api import sync_playwright
    feed = load_feed(feed_src)
    cards, log, blocked = {}, [], False
    t0 = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=headless)
        ctx = browser.new_context(locale="uk-UA")
        page = ctx.new_page()
        for seed in seeds:
            for n in range(1, max_pages + 1):
                url = seed if n == 1 else f"{seed}?p={n}"
                t = time.time()
                try:
                    r = page.goto(url, timeout=60000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3500)
                    status = r.status if r else None
                    title = page.title()
                except Exception as e:  # noqa: BLE001
                    log.append({"url": url, "error": type(e).__name__})
                    break
                if status != 200 or any(m in title for m in BLOCK_MARKERS):
                    log.append({"url": url, "status": status, "title": title[:60], "stop": "BLOCK"})
                    blocked = True
                    break
                items = page.evaluate(JS_CARDS)
                new = 0
                for it in items:
                    if it["pr"] not in cards:
                        d = parse_card(it["text"])
                        d.update({"pr": it["pr"], "url": f"https://eva.ua/ua/pr{it['pr']}/", "cat": seed})
                        cards[it["pr"]] = d
                        new += 1
                log.append({"url": url, "status": status, "cards": len(items), "new": new, "sec": round(time.time() - t, 1)})
                print(f"[eva_scan] {url}: {len(items)} карток, нових {new}", flush=True)
                if not items:
                    break
                time.sleep(delay + random.uniform(0, delay * 0.4))
            if blocked:
                break
        browser.close()
    matched = match(list(cards.values()), feed)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"eva_competitors_{stamp}.json"
    meta = {"at": stamp, "feed_offers": len(feed), "pages": len([x for x in log if x.get("status") == 200]), "cards": len(cards), "blocked": blocked,
            "seconds": round(time.time() - t0), "delay": delay, "log": log}
    path.write_text(json.dumps({"meta": meta, "cards": list(cards.values()), "matched": matched}, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def summarize(matched: dict, ncards: int, out_md: Path) -> None:
    """Підсумок українською у UTF-8 файл (консоль Windows ламає кирилицю): скільки збігів, співвідношення цін по корзинах закупівлі (наближено: ціна фіду / 1,5)."""
    import statistics as st
    from collections import Counter, defaultdict
    withc = {k: v for k, v in matched.items() if v["min_comp"]}
    r = sorted(v["ours"] / v["min_comp"] for v in withc.values())
    lines = [f"# EVA: конкуренти за скануванням ({datetime.now():%Y-%m-%d %H:%M})", "",
             f"- Карток на EVA прочитано: {ncards}; зіставлено з нашими кодами Toysi: {len(matched)}; з конкурентом: {len(withc)}.", ""]
    if r:
        lines.append(f"- Наша ціна / мінімум конкурента: медіана {st.median(r):.3f}; p25 {r[len(r)//4]:.3f}; p75 {r[3*len(r)//4]:.3f}.")
        b = Counter("ми дорожчі >10%" if x > 1.10 else "ми дорожчі 3–10%" if x > 1.03 else "у межах ±3%" if x >= 0.97 else "ми дешевші" for x in r)
        for k in ("ми дорожчі >10%", "ми дорожчі 3–10%", "у межах ±3%", "ми дешевші"):
            lines.append(f"  - {k}: {b[k]} ({100*b[k]/len(r):.0f}%)")
        by = defaultdict(list)
        for v in withc.values():
            c = v["ours"] / 1.5
            by["до 200 ₴" if c < 200 else "200–500 ₴" if c < 500 else "понад 500 ₴"].append(v["ours"] / v["min_comp"])
        lines += ["", "| Закупівля (наближ.) | Товарів | Медіана нас/конкурент | Дорожчі >10% |", "|---|---|---|---|"]
        for k in ("до 200 ₴", "200–500 ₴", "понад 500 ₴"):
            if by[k]:
                lines.append(f"| {k} | {len(by[k])} | {st.median(by[k]):.2f} | {sum(1 for x in by[k] if x > 1.10)} ({100*sum(1 for x in by[k] if x > 1.10)/len(by[k]):.0f}%) |")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def from_json(path: str, feed_src: str, out_dir: Path, competitor_only: bool = False, export_prices: str = None) -> Path:
    """Файл із eva_scan_browser.js (збір у Chrome власника) → parse_card + match → JSON і підсумок .md у out_dir."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cards = []
    for pr, v in raw["cards"].items():
        d = parse_card(v["t"])
        d.update({"pr": pr, "url": f"https://eva.ua/ua/pr{pr}/", "cat": v.get("cat")})
        cards.append(d)
    matched = match(cards, load_feed(feed_src), own_check=not competitor_only)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir.mkdir(parents=True, exist_ok=True)
    jp, mp = out_dir / f"eva_competitors_{stamp}.json", out_dir / f"eva_competitors_{stamp}.md"
    jp.write_text(json.dumps({"meta": {"at": stamp, "cards": len(cards), "blocked": raw.get("blocked"), "pages": len(raw.get("log", []))}, "matched": matched}, ensure_ascii=False, indent=1), encoding="utf-8")
    summarize(matched, len(cards), mp)
    if export_prices:       # для generate_eva_feed (ворота EVA_COMPETITOR_PRICE): {"at": ISO, "prices": {код_Toysi: мін_ціна_конкурента}}
        pr = {k: v["min_comp"] for k, v in matched.items() if v.get("min_comp")}
        Path(export_prices).write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "prices": pr}, ensure_ascii=False), encoding="utf-8")
        print(f"[eva_scan] експортовано цін конкурентів: {len(pr)} → {export_prices}")
    return mp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-json", help="файл із eva_scan_browser.js (Завантаження) — лише підрахунок, без мережі до EVA")
    ap.add_argument("--export-prices", help="куди записати eva_competitor_prices.json для фіду EVA")
    ap.add_argument("--competitor-only", action="store_true", help="усі картки файлу — конкурента (вітрина продавця): не шукати «наших» за ціною")
    ap.add_argument("--seeds", nargs="+", default=[])
    ap.add_argument("--max-pages", type=int, default=3)
    ap.add_argument("--delay", type=float, default=25.0)
    ap.add_argument("--feed", default=FEED_URL)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "reports" / "eva_competitors"))
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.from_json:
        print("підсумок:", from_json(a.from_json, a.feed, Path(a.out), a.competitor_only, a.export_prices))
        return
    if not a.seeds:
        ap.error("потрібні --seeds або --from-json")
    print("збережено:", scan(a.seeds, a.max_pages, a.delay, Path(a.out), a.feed, headless=not a.headed))


if __name__ == "__main__":
    main()
