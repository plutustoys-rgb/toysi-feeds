# -*- coding: utf-8 -*-
"""trend_radar.py — «радар трендів» для SEO (рішення власника 21.10.2026; запит SEO): що ВПЕРШЕ з'явилось у каталозі сайту за 7 діб і які зростаючі пошукові запити збігаються з назвами цих товарів.

Лише читання джерел; пише два файли в reports/gmc (звідти daily_files_to_shared кладе їх у SEO_метрики/<дата>/):
  • trend_radar_<дата>.csv         — товари Toysi і RT, що вперше з'явились у каталозі сайту за останні 7 діб: id, назва, vendor, категорія сайту, ціна, залишок, дата першої появи, джерело дати, постачальник.
  • trend_radar_queries_<дата>.csv — top_growth із gsc_weekly_summary_<дата>.csv, де хоч одна основа слова запиту збігається з основою слова назви/vendor нового товару (join за основою слова, без порогів): запит, покази, збіг основ, кількість нових товарів із цією основою, приклади id.
Підробки відомих брендів тут НЕ відсіюємо: колонка vendor — для ручної перевірки SEO.

ДАТА ПЕРШОЇ ПОЯВИ. У каталозі її не було, тому ведемо ЖУРНАЛ `catalog_first_seen.json` (VPS-локальний стан, у .gitignore): щодня беремо id із опублікованого сайту (site/index.json) і невідомим ставимо сьогоднішню дату (джерело «observed»).
Перший запуск (журнал порожній): для Toysi береться дата з фіду постачальника (поле date, є не в усіх), джерело «toysi_feed_date»; решта — «baseline» (дата невідома, у радар НЕ потрапляє, поки не мине час). Товар, що зник і повернувся, НЕ вважається новим (журнал його пам'ятає).
Як запускається: VPS, останній крок gmc-status-report.service (після gsc_report.py, щоб бачити gsc_weekly_summary). Помилка читання одного джерела не валить решту; без index.json нічого не пишемо."""
import csv
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
SITE_DIR = Path(os.environ.get("SITE_DIR", str(HERE / "site")))
OUT_DIR = Path(os.environ.get("GMC_REPORT_DIR", str(HERE / "reports" / "gmc")))
LEDGER = Path(os.environ.get("CATALOG_FIRST_SEEN", str(HERE / "catalog_first_seen.json")))
TOYSI_CACHE = Path(os.environ.get("TOYSI_CATALOG_CACHE", str(HERE / "toysi_catalog_cache.json")))
SUPPLIER_RT = Path(os.environ.get("SUPPLIER_RT_FILE", str(HERE / "supplier_rt.json")))
WINDOW_DAYS = 7
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# службові слова запитів, що не описують товар (фіксований перелік, не поріг)
STOP = {"купити", "недорого", "ціна", "ціни", "відгуки", "україна", "україні", "київ", "доставка", "інтернет", "магазин", "опт", "дешево", "купить", "цена", "отзывы", "украина", "дешево", "для", "від", "або"}
WORD_RE = re.compile(r"[a-zа-яіїєґ0-9]+", re.I)


def stems(text: str) -> set:
    """Основи слів: нижній регістр, слова від 4 символів, не службові; основа = перші 5 символів (грубе «за основою слова»: сквіш/сквіші/сквішів → «сквіш»; лабубу → «лабуб»)."""
    out = set()
    for w in WORD_RE.findall((text or "").lower().replace("’", "").replace("'", "")):
        if len(w) >= 4 and w not in STOP:
            out.add(w[:5])
    return out


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        print(f"[trend_radar] ⚠ {path.name} не прочитано ({type(e).__name__}) — частина колонок буде порожня", file=sys.stderr)
        return default


def update_ledger(ledger: dict, ids, today: date, toysi_dates: dict) -> dict:
    """Додає невідомі id у журнал і повертає його. Перший запуск (seen порожній): дата з фіду Toysi (якщо є й не з майбутнього) або baseline; далі — сьогодні (observed). Існуючі записи НЕ змінюються."""
    seen = ledger.setdefault("seen", {})
    first_run = not seen
    if first_run:
        ledger["baseline_date"] = today.isoformat()
    for i in ids:
        i = str(i)
        if i in seen:
            continue
        if first_run:
            d = (toysi_dates or {}).get(i, "")
            if DATE_RE.fullmatch(d or "") and d <= today.isoformat():
                seen[i] = {"first": d, "src": "toysi_feed_date"}
            else:
                seen[i] = {"first": today.isoformat(), "src": "baseline"}
        else:
            seen[i] = {"first": today.isoformat(), "src": "observed"}
    return ledger


def new_ids(ledger: dict, current_ids, today: date, days: int = WINDOW_DAYS) -> list:
    """id, що зараз є на сайті й вперше з'явились за останні `days` діб (включно з сьогодні), без baseline. Відсортовано: найновіші першими."""
    since = (today - timedelta(days=days - 1)).isoformat()
    rows = []
    cur = {str(i) for i in current_ids}
    for i, rec in (ledger.get("seen") or {}).items():
        if i in cur and rec.get("src") != "baseline" and rec.get("first", "") >= since:
            rows.append((rec["first"], i, rec.get("src", "")))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return rows


def product_rows(new: list, index_by_id: dict, toysi: dict, rt: dict) -> list:
    out = []
    for first, i, src in new:
        it = index_by_id.get(i) or {}
        if i.startswith("rt-"):
            meta = (rt or {}).get(i) or {}
            supplier, vendor, stock = "RoyalToys", meta.get("vendor", ""), meta.get("stock", "")
        else:
            meta = (toysi or {}).get(i) or {}
            supplier, vendor, stock = "Toysi", meta.get("vendor", ""), meta.get("stock", "")
        out.append({"id": i, "name": it.get("n", ""), "vendor": vendor or "", "site_category": it.get("c", ""), "price": it.get("pr", ""), "stock": stock,
                    "first_seen": first, "first_seen_source": src, "supplier": supplier})
    return out


def match_queries(growth_rows: list, products: list) -> list:
    """growth_rows: [{query, clicks, impressions, position, extra}], products: product_rows(). Для кожного запиту — які основи збігаються з основами назв/vendor нових товарів, скільки таких товарів, приклади id. Запит без збігу відкидається."""
    by_stem = {}
    for p in products:
        for s in stems(p["name"] + " " + p["vendor"]):
            by_stem.setdefault(s, []).append(p["id"])
    out = []
    for r in growth_rows:
        hit = sorted(s for s in stems(r["query"]) if s in by_stem)
        if not hit:
            continue
        ids = sorted({i for s in hit for i in by_stem[s]})
        out.append({**r, "matched_stems": " ".join(hit), "new_products_with_stem": "; ".join(f"{s}={len(by_stem[s])}" for s in hit), "example_ids": " ".join(ids[:5])})
    return out


def read_growth(path: Path) -> list:
    rows = []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r.get("section") == "top_growth":
                    rows.append({"query": r.get("key1", ""), "clicks": r.get("clicks", ""), "impressions": r.get("impressions", ""), "position": r.get("position", ""), "extra": r.get("extra", "")})
    except OSError as e:
        print(f"[trend_radar] ⚠ {path.name} не прочитано ({type(e).__name__})", file=sys.stderr)
    return rows


def latest_weekly(out_dir: Path):
    best = None
    for p in out_dir.glob("gsc_weekly_summary_*.csv"):
        m = re.fullmatch(r"gsc_weekly_summary_(\d{4}-\d{2}-\d{2})\.csv", p.name)
        if m and (best is None or m.group(1) > best[0]):
            best = (m.group(1), p)
    return best[1] if best else None


def write_csv(path: Path, cols: list, rows: list) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def main(today: date = None) -> int:
    today = today or date.today()
    idx = load_json(SITE_DIR / "index.json")
    if not isinstance(idx, list) or not idx:
        print("[trend_radar] index.json відсутній/порожній — нічого не пишу (журнал не чіпаю)", file=sys.stderr)
        return 0
    index_by_id = {str(p["id"]): p for p in idx if isinstance(p, dict) and "id" in p}
    toysi = load_json(TOYSI_CACHE, {}) or {}
    rt_raw = load_json(SUPPLIER_RT, {}) or {}
    rt = rt_raw.get("items", {}) if isinstance(rt_raw, dict) else {}
    ledger = load_json(LEDGER, {}) or {}
    if not isinstance(ledger, dict):
        ledger = {}
    update_ledger(ledger, index_by_id.keys(), today, {k: (v or {}).get("date", "") for k, v in toysi.items() if isinstance(v, dict)})
    tmp = LEDGER.with_name(LEDGER.name + ".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, LEDGER)
    products = product_rows(new_ids(ledger, index_by_id.keys(), today), index_by_id, toysi, rt)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_DIR / f"trend_radar_{today:%Y-%m-%d}.csv", ["id", "name", "vendor", "site_category", "price", "stock", "first_seen", "first_seen_source", "supplier"], products)
    weekly = latest_weekly(OUT_DIR)
    matched = match_queries(read_growth(weekly), products) if weekly else []
    write_csv(OUT_DIR / f"trend_radar_queries_{today:%Y-%m-%d}.csv", ["query", "clicks", "impressions", "position", "extra", "matched_stems", "new_products_with_stem", "example_ids"], matched)
    base = sum(1 for r in ledger["seen"].values() if r.get("src") == "baseline")
    print(f"[trend_radar] нових за {WINDOW_DAYS} діб: {len(products)}; запитів зі збігом: {len(matched)} (weekly: {weekly.name if weekly else 'нема'}); у журналі {len(ledger['seen'])}, з них baseline {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
