# -*- coding: utf-8 -*-
"""demand_featured_auto.py — «Популярне зараз» за ПОПИТОМ Google автоматично (запит власника 16.10: «список формується автоматично чи разово від SEO? треба зробити автоматично»).

Джерело — Search Console API (той самий сервіс-акаунт, що gsc_report; доступ «лише читання»): searchAnalytics.query, dimensions=page, 90 діб до «сьогодні−3». Сторінки зіставляються з нашими toysi_id:
  • /product-<id>.html — id напряму; • старі Prom-URL /ua/p<prom_id>-… і /p<prom_id>-… — через own_product_links_cache.json ({toysi_id: {prom_id, …}}, перевернутий).
Покази (і кліки як тай-брейк) складаються по id; беруться лише товари, що є в site/index.json (у наявності); правила відбору й чорний список — у demand_rules.json (редагує SEO через PR): {"blacklist_ids": [...], "max_ids": 40, "min_impressions": 1}.
ВИХІД: site/demand_featured_auto.json — той самий формат, що ручний site/demand_featured.json від SEO ({"date","source","ids","categories","note"}); поле categories береться з ручного файла (SEO веде), якщо він є. Окремий файл, бо demand_featured.json у git: запис у нього на VPS зламав би автосинхронізацію (merge --ff-only) при оновленні від SEO. Фронт читає свіжіший із двох (за полем date).
Збій API/мало даних (<MIN_IDS після відбору) → файл НЕ перезаписується. Запуск: `python demand_featured_auto.py` (VPS-таймер щодня)."""
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
SITE_DIR = Path(os.environ.get("SITE_DIR", str(HERE / "site")))
OUT_FILE = SITE_DIR / "demand_featured_auto.json"
MANUAL_FILE = SITE_DIR / "demand_featured.json"
INDEX_FILE = SITE_DIR / "index.json"
LINKS_CACHE = HERE / "own_product_links_cache.json"
RULES_FILE = HERE / "demand_rules.json"
DAYS = 90
LAG_DAYS = 3
MIN_IDS = 10
_PRODUCT = re.compile(r"/product-(\d+)\.html")
_PROM = re.compile(r"/(?:ua/)?p(\d+)-")


def load_rules(path: Path | None = None) -> dict:
    d = {"blacklist_ids": [], "max_ids": 40, "min_impressions": 1}
    try:
        raw = json.loads(Path(path or RULES_FILE).read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            d["blacklist_ids"] = [str(x) for x in (raw.get("blacklist_ids") or [])]
            d["max_ids"] = int(raw.get("max_ids") or d["max_ids"])
            d["min_impressions"] = int(raw.get("min_impressions") or d["min_impressions"])
    except (OSError, ValueError, TypeError):
        pass
    return d


def prom_to_toysi(cache: dict) -> dict:
    out = {}
    for tid, v in (cache or {}).items():
        pid = str((v or {}).get("prom_id") or "").strip() if isinstance(v, dict) else ""
        if pid:
            out[pid] = str(tid)
    return out


def aggregate(rows: list, prom_map: dict) -> dict:
    """{toysi_id: [покази, кліки]} зі сторінкових рядків GSC ({"keys":[url], impressions, clicks})."""
    agg = {}
    for r in rows:
        keys = r.get("keys") or []
        if not keys:
            continue
        url = str(keys[0])
        m = _PRODUCT.search(url)
        tid = m.group(1) if m else None
        if tid is None:
            m = _PROM.search(url)
            tid = prom_map.get(m.group(1)) if m else None
        if not tid:
            continue
        a = agg.setdefault(tid, [0, 0])
        a[0] += int(r.get("impressions") or 0)
        a[1] += int(r.get("clicks") or 0)
    return agg


def select(agg: dict, site_ids: set, rules: dict) -> list:
    black = set(rules["blacklist_ids"])
    ranked = sorted(((i, imp, clk) for i, (imp, clk) in agg.items() if i in site_ids and i not in black and imp >= rules["min_impressions"]), key=lambda x: (-x[1], -x[2], int(x[0])))
    return [i for i, _, _ in ranked[:rules["max_ids"]]]


def build(ids: list, now: datetime, manual: dict | None, n_pages: int) -> dict:
    out = {"date": now.strftime("%Y-%m-%d"), "source": f"Search Console API {DAYS}d (авто, demand_featured_auto.py): покази сторінок; старі Prom-URL зіставлено з toysi_id; відібрано {len(ids)} з {n_pages} зіставлених", "ids": ids}
    if isinstance(manual, dict) and manual.get("categories"):
        out["categories"] = manual["categories"]
    out["note"] = "ids — найбільше показів у Google за 90 днів, лише в наявності; чорний список і ліміт — demand_rules.json; зброю/мілітарі відсіює збірка сайту."
    return out


def main(session=None, now: datetime | None = None, out_file: Path | None = None) -> int:
    now = now or datetime.now()
    out_file = Path(out_file or OUT_FILE)
    try:
        index = json.loads(INDEX_FILE.read_text(encoding="utf-8-sig"))
        site_ids = {str(x["id"]) for x in index if isinstance(x, dict) and x.get("id")}
    except (OSError, ValueError, TypeError):
        site_ids = set()
    if not site_ids:
        print("[demand_auto] site/index.json порожній/нечитабельний: файл не оновлено", file=sys.stderr)
        return 1
    try:
        import gsc_report as gsc
        session = session or gsc.make_session()
        end = (now - timedelta(days=LAG_DAYS)).strftime("%Y-%m-%d")
        start = (now - timedelta(days=LAG_DAYS + DAYS - 1)).strftime("%Y-%m-%d")
        rows = gsc.query_rows(session, start, end, ["page"])
    except Exception as e:  # noqa: BLE001
        print(f"[demand_auto] Search Console не віддав дані ({type(e).__name__}: {str(e)[:120]}): файл не оновлено", file=sys.stderr)
        return 1
    try:
        cache = json.loads(LINKS_CACHE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        cache = {}
    agg = aggregate(rows, prom_to_toysi(cache))
    ids = select(agg, site_ids, load_rules())
    if len(ids) < MIN_IDS:
        print(f"[demand_auto] після відбору лише {len(ids)} id (<{MIN_IDS}; сторінок GSC {len(rows)}, зіставлено {len(agg)}): файл не оновлено", file=sys.stderr)
        return 1
    try:
        manual = json.loads(MANUAL_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        manual = None
    data = build(ids, now, manual, len(agg))
    tmp = out_file.with_name(out_file.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out_file)
    print(f"[demand_auto] {len(rows)} сторінок GSC → зіставлено {len(agg)} товарів → відібрано {len(ids)} → {out_file.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
