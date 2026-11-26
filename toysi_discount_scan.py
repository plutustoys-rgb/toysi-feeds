# -*- coding: utf-8 -*-
"""toysi_discount_scan.py — ПЕРСОНАЛЬНІ ціни/знижки Toysi по кожному товару з каталогу (ЛИШЕ ЧИТАННЯ; нічого не купує/не міняє).

НАВІЩО (рішення власника 25.11: «для Toysi теж треба скрапер, який знімає вартість у каталозі, бо ми постійно у програші з їхніми знижками»): Toysi API віддає лише каталожну ціну, а знижка на товар (15 % / 5 % / 0 %) видна ТІЛЬКИ в залогіненому кабінеті
(сторінка категорії: «Код товару: 297194 … 291.92 грн. 248.13 грн.»). Правило-припущення (виняток за категорією/брендом, isImport) помилялось: «Велосипеди» мають 15 %, а код вважав 0 % (297194: собівартість 306.92 замість 263.13).
ЯК: Playwright із ЗБЕРЕЖЕНОЮ сесією Toysi (той самий .local_secrets/toysi_cabinet_state.json, що в toysi_cabinet_scraper.py; логін робить власник `--login` там), headless, ПОВІЛЬНО (пауза --delay між категоріями), по сторінках категорій `/ua/optom-*-sklad`:
у кожній картці `div.product#favbg<код>` береться «Код товару», каталожна і персональна ціна (якщо дві — знижка = 1 − нова/стара; якщо одна — знижки нема).
ВИХІД: toysi_discounts.json {"at": ISO, "items": {код: {"base": 291.92, "price": 248.13, "disc": 0.15}}, "stats": {...}} (атомарно tmp+replace). Сесія протухла (нема «Ваш депозит») → зупинка з поясненням, нічого не пишемо.
Запуск: python toysi_discount_scan.py [--delay 2.5] [--max-cats N] [--out шлях]"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = Path(os.environ.get("TOYSI_CABINET_STATE_FILE") or (BASE_DIR / ".local_secrets" / "toysi_cabinet_state.json"))
ROOT = "https://toysi.ua/ua/"
NAV_TIMEOUT_MS = 60000
CODE_RE = re.compile(r"Код товару:\s*(\d+)")
PRICE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*грн")

JS_PRODUCTS = """() => [...document.querySelectorAll('div.product')].map(c => ({id: c.id || '', text: c.innerText.replace(/\\s+/g, ' ')}))"""
JS_PAGES = """() => Math.max(1, ...[...document.querySelectorAll('.toysi-page-container a, .toysi-page-container span')].map(e => parseInt((e.textContent || '').trim(), 10)).filter(n => !isNaN(n)))"""
PAGE_STEP = 52          # крок offset (карток на сторінку); пагінація: /ua/<cat>-sklad/offset52/, offset104/, …
MAX_PAGES_PER_CAT = 80
JS_CATS = """() => [...new Set([...document.querySelectorAll('a[href^="/ua/optom-"]')].map(a => a.getAttribute('href')).filter(h => h.endsWith('-sklad')))]"""


def parse_card(text: str):
    """text картки → (код, base, price) або None. Дві ціни → (стара, нова); одна → base=price."""
    m = CODE_RE.search(text)
    if not m:
        return None
    prices = [float(x.replace(",", ".")) for x in PRICE_RE.findall(text)]
    if not prices:
        return None
    base, price = (prices[0], prices[1]) if len(prices) >= 2 else (prices[0], prices[0])
    if price > base:                      # захист від плутанини порядку
        base, price = price, base
    return m.group(1), round(base, 2), round(price, 2)


def scan(delay: float, max_cats: int, out: Path) -> dict:
    from playwright.sync_api import sync_playwright
    items, cat_stats = {}, {}
    t0 = time.time()
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(storage_state=str(STATE_FILE), locale="uk-UA")
        pg = ctx.new_page()
        pg.goto(ROOT, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        pg.wait_for_timeout(2500)
        if "Ваш депозит" not in pg.inner_text("body"):
            b.close()
            raise SystemExit("сесія Toysi протухла (нема «Ваш депозит») — потрібен --login у toysi_cabinet_scraper.py; нічого не записано")
        cats = pg.evaluate(JS_CATS)
        if max_cats:
            cats = cats[:max_cats]
        print(f"[toysi_scan] категорій: {len(cats)}", flush=True)
        for i, href in enumerate(cats, 1):
            pages, new_total, cards_total, zero_streak, status = 1, 0, 0, 0, None
            pgn = 0
            while pgn < pages and pgn < MAX_PAGES_PER_CAT:
                url = "https://toysi.ua" + href + ("" if pgn == 0 else f"/offset{pgn * PAGE_STEP}/")
                try:
                    r = pg.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                    pg.wait_for_timeout(1200)
                    prods = pg.evaluate(JS_PRODUCTS)
                    if pgn == 0:
                        pages = int(pg.evaluate(JS_PAGES) or 1)
                    status = r.status if r else None
                except Exception as e:  # noqa: BLE001
                    print(f"[toysi_scan] {url}: {type(e).__name__}", file=sys.stderr)
                    break
                new = 0
                for pr in prods:
                    parsed = parse_card(pr["text"])
                    if parsed and parsed[0] not in items:
                        code, base, price = parsed
                        items[code] = {"base": base, "price": price, "disc": round(1 - price / base, 4) if base else 0.0}
                        new += 1
                new_total += new
                cards_total += len(prods)
                zero_streak = zero_streak + 1 if new == 0 else 0
                pgn += 1
                time.sleep(delay)
                if zero_streak >= 2:           # дві сторінки поспіль без нових — товари вже зняті в інших категоріях
                    break
            cat_stats[href] = {"pages_seen": pgn, "pages_total": pages, "cards": cards_total, "new": new_total, "status": status}
            print(f"[toysi_scan] {i}/{len(cats)} {href}: стор. {pgn}/{pages}, карток {cards_total}, нових {new_total}, всього {len(items)}", flush=True)
        b.close()
    result = {"at": datetime.now().isoformat(timespec="seconds"),
              "items": items,
              "stats": {"categories": len(cat_stats), "items": len(items), "seconds": round(time.time() - t0)}}
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=2.5)
    ap.add_argument("--max-cats", type=int, default=0)
    ap.add_argument("--out", default=str(BASE_DIR / "toysi_discounts.json"))
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    r = scan(a.delay, a.max_cats, Path(a.out))
    from collections import Counter
    c = Counter(round(v["disc"], 2) for v in r["items"].values())
    print("[toysi_scan] готово:", r["stats"], "| розподіл знижок:", dict(c.most_common(8)))


if __name__ == "__main__":
    main()
