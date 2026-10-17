# -*- coding: utf-8 -*-
"""eva_category_check.py — щоденна звірка «категорії кабінету EVA ↔ наш фід» (лише читання; рішення власника 16.10.2026: «не мені писати, а тобі [Коду]»).

ЧОМУ: 16.10 з'ясувалось, що ВСІ 94 категорії нашого фіду (7 781 товар у наявності) стояли в кабінеті EVA як «Нова / Потребують співставлення» (з 03.08 фід віддає id довідника EVA, кабінет бачить їх як нові категорії мерчанта), а 4 363 товари мали помилку «Категорію мерчанта не співставлено». Це лежало тижнями, бо ніхто не порівнював таблицю категорій кабінету з фідом.
ЩО РОБИТЬ: Playwright + збережена сесія кабінету (як eva_cabinet_scraper) читає `Категорії → Потребують співставлення` і `Співставлені`, бере публічний eva_feed.xml і рахує, скільки available-товарів лежить у НЕСПІВСТАВЛЕНИХ категоріях. Звіт — `eva_categories_<дата>.md` у AUDIT_REPORT_DIR. Якщо таких товарів >0 — один раз на добу дописує у канал (EXECUTOR_CHANNEL.md) запис «[Автомат → Код]» з числами й топом категорій (БЕЗ Telegram власнику). Нуль — тиша.
Запускається кроком у run_marketplace_actions.py (таск PlutusToys_MarketplaceActions, кожні 6 год). Протухла сесія → друкує підказку й виходить кодом 0 (сигнал про сесію дає eva_cabinet_scraper)."""
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / ".local_secrets" / "eva_cabinet_state.json"
CATEGORIES_URL = "https://seller.eva.ua/integrations/imports/categories"
FEED_URL = os.environ.get("EVA_FEED_URL", "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/eva_feed.xml")
REPORT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or (BASE_DIR / "reports"))
CHANNEL_FILE = Path(os.environ.get("EVA_CHECK_CHANNEL_FILE") or (REPORT_DIR.parent / "EXECUTOR_CHANNEL.md"))
MARK = "[Автомат → Код] EVA: категорії кабінету"
RECENT_DAYS = 3      # не частіше одного запису на стільки днів, поки стан триває
_ROW = re.compile(r"(\d+)\s*\n\s*ID:\s*(\d+)\s*\n\s*([^\n\t]*)\t?\s*([^\n]*)")


def parse_rows(text: str) -> dict:
    """{id категорії мерчанта: (назва, статус)} із тексту сторінки таблиці категорій кабінету."""
    return {m.group(1): (m.group(3).strip(), m.group(4).strip()) for m in _ROW.finditer(text or "")}


def read_list(page, f: str | None) -> dict:
    """Усі рядки одного списку (f='mr' — потребують співставлення, 'm' — співставлені) з пагінацією за курсором (?after=…)."""
    rows, seen = {}, set()
    url = CATEGORIES_URL + (f"?f={f}" if f else "")
    for _ in range(20):
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)
        n0 = len(rows)
        rows.update(parse_rows(page.inner_text("body")))
        links = [x for x in page.eval_on_selector_all("a[href*='after=']", "els => els.map(e => e.getAttribute('href'))") if x not in seen]
        if len(rows) == n0 or not links:
            break
        seen.add(links[-1])
        url = "https://seller.eva.ua" + links[-1]
    return rows


def analyze(unmatched: dict, matched: dict, feed_xml: str) -> dict:
    """Рахує товари фіду в категоріях без співставлення. Чиста функція (тестується без кабінету)."""
    root = ET.fromstring(feed_xml)
    offers = root.findall(".//offer")
    avail = [o for o in offers if o.get("available") == "true"]
    cats = Counter(o.findtext("categoryId") for o in avail)
    bad = {c: n for c, n in cats.items() if c in unmatched}
    unknown = {c: n for c, n in cats.items() if c not in unmatched and c not in matched}
    return {"feed_available": len(avail), "feed_categories": len(cats), "unmatched_total": len(unmatched), "matched_total": len(matched),
            "bad_categories": len(bad), "bad_offers": sum(bad.values()),
            "unknown_categories": len(unknown), "unknown_offers": sum(unknown.values()),
            "top": sorted(((n, c, unmatched[c][0]) for c, n in bad.items()), reverse=True)[:10]}


def render_report(res: dict, now: datetime) -> str:
    lines = [f"# EVA: категорії кабінету ↔ фід, {now:%Y-%m-%d %H:%M}", "",
             f"- Кабінет: «Потребують співставлення» {res['unmatched_total']}, «Співставлені» {res['matched_total']}.",
             f"- Фід: available {res['feed_available']} товарів у {res['feed_categories']} категоріях.",
             f"- **У НЕСПІВСТАВЛЕНИХ категоріях: {res['bad_offers']} товарів ({res['bad_categories']} категорій).**",
             f"- Категорій фіду, яких кабінет не показує ні там, ні там: {res['unknown_categories']} ({res['unknown_offers']} товарів)."]
    if res["top"]:
        lines += ["", "Топ незіставлених за кількістю товарів:"]
        lines += [f"  {n:5d}  {cid}  {name}" for n, cid, name in res["top"]]
    return "\n".join(lines) + "\n"


HISTORY_FILE = REPORT_DIR / "eva_category_history.jsonl"
REGRESS_MARK = "[Автомат → Код] EVA: регрес кабінету"
MERCHANT_URL = "https://seller.eva.ua/merchant/products"
_COUNT_LABELS = (("all", "Всі товари"), ("active", "Активні"), ("sold", "Продані"), ("moderation", "На модерації"), ("hidden", "Приховані"))


def parse_merchant_counts(text: str) -> dict:
    """Лічильники «Мої товари» (seller.eva.ua/merchant/products): «Всі товари 6454 Активні 6454 Продані 0 На модерації 0 Приховані 0». Не знайдено → None (не 0)."""
    out = {}
    flat = re.sub(r"\s+", " ", text or "")
    for key, label in _COUNT_LABELS:
        m = re.search(re.escape(label) + r"\s+(\d[\d ]*)", flat)
        out[key] = int(m.group(1).replace(" ", "")) if m else None
    return out


def read_merchant_counts(page) -> dict:
    page.goto(MERCHANT_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    return parse_merchant_counts(page.inner_text("body"))


def history_row(res: dict, counts: dict, now: datetime) -> dict:
    return {"ts": now.isoformat(timespec="seconds"), "feed_available": res["feed_available"], "bad_offers": res["bad_offers"],
            "unknown_offers": res["unknown_offers"], "unmatched_cats": res["unmatched_total"], "matched_cats": res["matched_total"],
            **{f"my_{k}": v for k, v in counts.items()}}


def last_history(path: Path | None = None) -> dict | None:
    path = Path(path or HISTORY_FILE)
    try:
        lines = [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None


def append_history(row: dict, path: Path | None = None) -> None:
    path = Path(path or HISTORY_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def regression_flags(prev: dict | None, cur: dict) -> list:
    """Лише ФАКТ погіршення проти попереднього знімка, без порогів (03.08 фід віддав усі 94 категорії як «Нова», і цього ніхто не побачив два місяці).
    Погіршення: більше товарів фіду в незіставлених/невідомих категоріях; менше співставлених категорій; менше «Активних» у «Мої товари»."""
    if not prev:
        return []
    out = []
    pb, cb = (prev.get("bad_offers") or 0) + (prev.get("unknown_offers") or 0), (cur.get("bad_offers") or 0) + (cur.get("unknown_offers") or 0)
    if cb > pb:
        out.append(f"товарів фіду в незіставлених категоріях стало більше: {pb} → {cb}")
    if (cur.get("matched_cats") or 0) < (prev.get("matched_cats") or 0):
        out.append(f"співставлених категорій у кабінеті стало менше: {prev.get('matched_cats')} → {cur.get('matched_cats')}")
    pa, ca = prev.get("my_active"), cur.get("my_active")
    if pa is not None and ca is not None and ca < pa:
        out.append(f"«Мої товари → Активні» впали: {pa} → {ca}")
    return out


def post_regression(flags: list, cur: dict, prev: dict | None, now: datetime, channel: Path | None = None) -> bool:
    """Запис `## [Автомат → Код] EVA: регрес кабінету <дата>` угорі каналу (перед першим рядком, що ПОЧИНАЄТЬСЯ з «## »); один на добу; без Telegram."""
    channel = Path(channel or CHANNEL_FILE)
    try:
        with open(channel, encoding="utf-8", newline="") as _f:
            s = _f.read()
    except OSError:
        return False
    head = f"## {REGRESS_MARK} {now:%Y-%m-%d}"
    if head in s:
        return False
    nl = "\r\n" if "\r\n" in s else "\n"
    body = "\n".join(f"🚩 {x}" for x in flags)
    entry = (f"{head}\n{body}\nБуло ({(prev or {}).get('ts')}): bad {(prev or {}).get('bad_offers')}, unknown {(prev or {}).get('unknown_offers')}, співставлені {(prev or {}).get('matched_cats')}, Активні {(prev or {}).get('my_active')}; "
             f"стало ({cur.get('ts')}): bad {cur.get('bad_offers')}, unknown {cur.get('unknown_offers')}, співставлені {cur.get('matched_cats')}, Активні {cur.get('my_active')}. Історія — eva_category_history.jsonl. Дія — Коду.\n\n").replace("\n", nl)
    m = re.search(r"(?m)^## ", s)
    new = (s[:m.start()] + entry + s[m.start():]) if m else (s + nl + entry)
    try:
        tmp = channel.with_name(channel.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as _f:
            _f.write(new)
        os.replace(tmp, channel)
    except OSError:
        return False
    return True


def post_to_channel(res: dict, now: datetime, channel: Path | None = None) -> bool:
    """Запис для Коду; не частіше ніж раз на RECENT_DAYS днів (поки стан триває); вставка перед першим рядком, що ПОЧИНАЄТЬСЯ з «## » (не всередині блок-цитати «> ## …»); атомарний запис; True — дописано."""
    channel = Path(channel or CHANNEL_FILE)
    stamp = f"{now:%Y-%m-%d}"
    try:
        with open(channel, encoding="utf-8", newline="") as _f:
            s = _f.read()
    except OSError:
        return False
    for m in re.finditer(r"(?m)^## " + re.escape(MARK) + r" (\d{4}-\d{2}-\d{2})", s):
        try:
            if (now.date() - date.fromisoformat(m.group(1))).days < RECENT_DAYS:
                return False
        except ValueError:
            continue
    head = f"## {MARK} {stamp}"
    nl = "\r\n" if "\r\n" in s else "\n"
    top = "; ".join(f"{c} {name} — {n}" for n, c, name in res["top"][:5])
    entry = (f"{head}: {res['bad_offers']} товарів фіду в НЕСПІВСТАВЛЕНИХ категоріях ({res['bad_categories']} категорій)\n"
             f"Кабінет `Категорії`: потребують співставлення {res['unmatched_total']}, співставлені {res['matched_total']}; фід: available {res['feed_available']}. Топ: {top}. Повний звіт — eva_categories_{stamp}.md. "
             f"Дія — Коду (повернення старих співставлених id: eva_legacy_rollout.json; решта — менеджер EVA за командою власника).\n\n").replace("\n", nl)
    m = re.search(r"(?m)^## ", s)
    new = (s[:m.start()] + entry + s[m.start():]) if m else (s + nl + entry)
    try:
        tmp = channel.with_name(channel.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as _f:
            _f.write(new)
        os.replace(tmp, channel)
    except OSError:
        return False
    return True


def main() -> int:
    now = datetime.now()
    if not STATE_FILE.exists():
        print(f"[eva_category_check] нема сесії {STATE_FILE.name}: крок пропущено (разовий `eva_cabinet_scraper.py --login` робить власник)")
        return 0
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True)
            ctx = b.new_context(storage_state=str(STATE_FILE), viewport={"width": 1400, "height": 1200})
            page = ctx.new_page()
            unmatched = read_list(page, "mr")
            matched = read_list(page, "m")
            try:
                counts = read_merchant_counts(page)
            except Exception as e:  # noqa: BLE001 — лічильники не критичні для звіту про категорії
                print(f"[eva_category_check] лічильники «Мої товари» не зчитано: {type(e).__name__}", file=sys.stderr)
                counts = {k: None for k, _ in _COUNT_LABELS}
            b.close()
        if not unmatched and not matched:
            print("[eva_category_check] кабінет не віддав жодної категорії (сесія протухла?): крок пропущено", file=sys.stderr)
            return 0
        with urllib.request.urlopen(FEED_URL, timeout=60) as r:
            feed = r.read().decode("utf-8", errors="replace")
        res = analyze(unmatched, matched, feed)
    except Exception as e:  # noqa: BLE001
        print(f"[eva_category_check] збій: {type(e).__name__}: {str(e)[:150]}", file=sys.stderr)
        return 1
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"eva_categories_{now:%Y-%m-%d}.md").write_text(render_report(res, now), encoding="utf-8")
    print(render_report(res, now))
    cur = history_row(res, counts, now)
    prev = last_history()
    flags = regression_flags(prev, cur)
    append_history(cur)
    if flags:
        print("[eva_category_check] 🚩 регрес:", "; ".join(flags))
        print("[eva_category_check] запис регресу в канал:", "додано" if post_regression(flags, cur, prev, now) else "уже є за сьогодні / канал недоступний")
    if res["bad_offers"] > 0:
        print("[eva_category_check] запис у канал:", "додано" if post_to_channel(res, now) else "уже є за сьогодні / канал недоступний")
    return 0


if __name__ == "__main__":
    sys.exit(main())
