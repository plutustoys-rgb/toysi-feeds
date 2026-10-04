# -*- coding: utf-8 -*-
"""zoobaza_competitor_report.py — ЗВІТ (лише читання): найдешевший довірений конкурент на Prom по кожному SKU
відфільтрованого каталогу ZooBaza, тією САМОЮ функцією, що й для іграшок (`prom_competitor_pricer.find_best_competitor`),
щоб число «поза підлогою» було порівнюване з еталоном іграшок (замовлення Консультанта 2026-10-04).

НІЧОГО не підключає до фідів/репрайсера/цін/замовлень. Виклик іде БЕЗ own_link: у нас цих товарів на Prom ще нема
(buyBox/recommended недоступні) — працює ЛИШЕ фолбек SearchListingQuery + fuzzy (MATCH_MIN_SCORE=0.4; довіра до
ціни — score ≥ MATCH_MIN_SCORE_FOR_PRICING=0.6 і відсутність конфлікту розмірів, як у decide_action).

КОМІСІЇ НЕ підставлені (ставок для цих категорій нема): підлога рахується ПАРАМЕТРИЧНО за сумарною комісією
(Prom+оплата) на сітці TC_GRID; повний набір полів по кожному SKU лишається в JSON — ставку можна підставити без
перепрогону (`floor_for()`). Формула підлоги — канонічна репо (competitor_pricing.canonical_competitor_floor):
    B = cost*(1+0.03) + candidate*TC,  candidate = ціна_конкурента − PRICE_STEP(3 грн)
і, для порівняння з оцінкою Продажника, «ділення»: cost*1.03/(1−TC).
Позиція «на підлозі» ⇔ candidate < B  (ми не можемо підрізати конкурента, не пішовши нижче 3% маржі).

Запуск: python zoobaza_competitor_report.py [--limit N] [--clothing]   → reports/zoobaza_competitors_<дата>.{json,md}
"""
import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import prom_competitor_pricer as pc
import functools

# Кеш phash-ів: наше фото й фото конкурентів завантажувались заново на КОЖЕН виклик (≈0.8 с; ×4 на SKU) —
# memo лише в цьому звіті (поведінку prom_competitor_pricer для репрайсера не міняємо).
_orig_phash = pc._fetch_image_phash
_phash_cache = {}


def _cached_phash(url):
    """Кеш лише УСПІШНИХ завантажень: None (мережевий збій) НЕ запам'ятовується назавжди (аудит PR #608)."""
    if url in _phash_cache:
        return _phash_cache[url]
    h = _orig_phash(url)
    if h is not None:
        _phash_cache[url] = h
    return h


pc._fetch_image_phash = _cached_phash
import competitor_pricing as cp
import zoobaza_parser as z

OUT_DIR = Path(__file__).parent / "reports"
_CLEARANCE_RE = re.compile(r"распродаж|розпродаж|уцін|уценк|брак|пошкодж|вітрин", re.IGNORECASE)
TC_GRID = (0.117, 0.137, 0.17, 0.20, 0.25)   # сумарна комісія Prom+оплата: припущення Продажника 13.7% і вищі сценарії


def floor_for(cost: float, competitor_price: float, tc: float) -> float:
    """Канонічна підлога репо при відомому конкуренті (candidate = ціна − крок)."""
    return cp.canonical_competitor_floor(cost, competitor_price - cp.PRICE_STEP, tc, cp.MIN_PROFIT_COMPETITOR_FLOOR)


def division_floor(cost: float, tc: float) -> float:
    return cost * (1 + cp.MIN_PROFIT_COMPETITOR_FLOOR) / (1 - tc)


import re

_DIM_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[xхXХ×*]\s*(\d+(?:[.,]\d+)?)(?:\s*[xхXХ×*]\s*(\d+(?:[.,]\d+)?))?")


def dims_of(name: str) -> set:
    """Набори розмірів із назви БЕЗ одиниць («60х80х24», «65х75»): кортеж чисел, відсортований (65х75 = 75х65).
    Саме так ZooBaza і продавці Prom пишуть розмір; `_size_tokens_conflict` репо вимагає одиницю (см/мм) і тут
    НЕ спрацьовує (знайдено живо 04.10: пуф 60х60 по 2050 зіставлявся з пуфом 42х42 по 1028 як «довірений»)."""
    out = set()
    for m in _DIM_RE.finditer((name or "").lower().replace(",", ".")):
        nums = tuple(sorted(float(g) for g in m.groups() if g))
        out.add(nums)
    return out


def _pack(res, name, it, size_ok=None):
    if not res:
        return None
    score = res["score"]
    c = {"price": res["price"], "name": res["name"], "score": round(score, 3), "id": res.get("id"),
         "url": f"https://prom.ua/ua/p{res.get('id')}-{res.get('urlText')}.html" if res.get("id") else None,
         "trusted_score": score >= pc.MATCH_MIN_SCORE_FOR_PRICING,
         "size_conflict": pc._size_tokens_conflict(name, res["name"] or ""),
         "photo_confirmed": bool(it["pictures"]) and pc._photo_confirms_match(it["pictures"], res.get("image")),
         "clearance_marker": bool(_CLEARANCE_RE.search(res["name"] or ""))}
    c["trusted"] = c["trusted_score"] and not c["size_conflict"]
    return c


def analyse_item(it: dict) -> dict:
    """A = РІВНО те, що дає find_best_competitor без own_link (пошук + _rank_competitor_candidates на всій видачі).
    B = ті самі функції, але кандидати попередньо відфільтровані за збігом розміру без одиниць (dims_of)."""
    name, cost = it["name"], it["cost"]
    row = {"id": it["id"], "vendor_code": it["vendor_code"], "name": name, "category": it["category_name"],
           "feed_price": it["price"], "cost": cost, "dims": sorted(dims_of(name))}
    raw = pc.search_prom_products(name)
    pics = it["pictures"] or None
    row["search_results"] = len(raw)
    row["A_raw"] = _pack(pc._rank_competitor_candidates(raw, name, cost, pics, check_presence=True), name, it)
    ours = dims_of(name)
    if ours:
        same = [p for p in raw if ours & dims_of(p.get("name"))]
        row["same_size_results"] = len(same)
        row["B_sized"] = _pack(pc._rank_competitor_candidates(same, name, cost, pics, check_presence=True), name, it)
    else:
        row["same_size_results"] = None
        row["B_sized"] = None
    return row


def _stats(rows: list, key: str) -> dict:
    n = len(rows)
    with_c = [r for r in rows if r.get(key)]
    trusted = [r for r in with_c if r[key]["trusted"]]
    photo = [r for r in trusted if r[key]["photo_confirmed"]]
    out = {"sku": n, "any_candidate": len(with_c), "trusted": len(trusted), "trusted_photo_confirmed": len(photo),
           "no_candidate": n - len(with_c), "by_tc": {}}
    for tc in TC_GRID:
        on_c = [r for r in trusted if r[key]["price"] - cp.PRICE_STEP < floor_for(r["cost"], r[key]["price"], tc)]
        on_d = [r for r in trusted if r[key]["price"] - cp.PRICE_STEP < division_floor(r["cost"], tc)]
        out["by_tc"][str(tc)] = {"on_floor_canonical": len(on_c), "on_floor_division": len(on_d),
                                 "pct_canonical": round(100 * len(on_c) / len(trusted), 1) if trusted else None,
                                 "pct_division": round(100 * len(on_d) / len(trusted), 1) if trusted else None}
    ratios = sorted(r[key]["price"] / r["feed_price"] for r in trusted)
    if ratios:
        out["competitor_to_feed_price"] = {"min": round(ratios[0], 3), "median": round(ratios[len(ratios) // 2], 3),
                                           "max": round(ratios[-1], 3),
                                           "within_1pct_of_feed": sum(1 for x in ratios if abs(x - 1) <= 0.01),
                                           "below_feed_by_more_than_30pct": sum(1 for x in ratios if x < 0.7)}
    sc = sorted(r[key]["score"] for r in with_c)
    if sc:
        out["score_quantiles"] = {"p25": sc[len(sc) // 4], "median": sc[len(sc) // 2], "p75": sc[3 * len(sc) // 4]}
    return out


def summarize(rows: list) -> dict:
    sized = [r for r in rows if r["dims"]]
    return {"n": len(rows), "with_dims_in_name": len(sized),
            "B_trusted_with_clearance_marker": sum(1 for r in sized if r.get("B_sized") and r["B_sized"]["trusted"]
                                                   and r["B_sized"].get("clearance_marker")),
            "A_raw_all": _stats(rows, "A_raw"), "B_sized_only_skus_with_dims": _stats(sized, "B_sized"),
            "A_raw_only_skus_with_dims": _stats(sized, "A_raw")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--summarize", action="store_true", help="лише звести вже накопичений .jsonl, без запитів")
    ap.add_argument("--file", default="", help="чекпоінт .jsonl (за замовчуванням: сьогоднішній; для --summarize — найновіший)")
    ap.add_argument("--clothing", action="store_true", help="включити одяг (за замовчуванням НІ — рішення Консультанта)")
    a = ap.parse_args()
    if a.summarize:
        cands = sorted(OUT_DIR.glob("zoobaza_competitors_*.jsonl"), key=lambda p: p.stat().st_mtime)
        ckf = Path(a.file) if a.file else (cands[-1] if cands else None)
        if ckf is None or not ckf.exists():
            print("[ZB-report] чекпоінта нема")
            return
        rows = [json.loads(l) for l in ckf.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(json.dumps(summarize([r for r in rows if not r.get("error")]), ensure_ascii=False, indent=1))
        return
    catalog = z.filter_catalog(z.fetch_zoobaza_catalog(), include_clothing=a.clothing)
    items = list(catalog.values())
    if a.limit:
        items = items[: a.limit]
    print(f"[ZB-report] SKU до аналізу: {len(items)} (одяг={'так' if a.clothing else 'ні'})")
    # Чекпоінт: кожен SKU дописується в .jsonl одразу; повторний запуск продовжує з місця зупинки
    # (довгий прогін упирається в ліміт фонової задачі — без цього втрачався весь результат).
    ck = Path(a.file) if a.file else OUT_DIR / f"zoobaza_competitors_{date.today().isoformat()}.jsonl"
    OUT_DIR.mkdir(exist_ok=True)
    rows = [json.loads(l) for l in ck.read_text(encoding="utf-8").splitlines() if l.strip()] if ck.exists() else []
    done = {r["id"] for r in rows}
    if done:
        print(f"[ZB-report] продовжую: уже зроблено {len(done)}")
    for i, it in enumerate(items, 1):
        if it["id"] in done:
            continue
        try:
            rows.append(analyse_item(it))
            with ck.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rows[-1], ensure_ascii=False) + chr(10))
        except Exception as e:  # noqa: BLE001 — один збій не валить прогін, але фіксується
            rows.append({"id": it["id"], "name": it["name"], "error": str(e), "A_raw": None, "B_sized": None,
                         "dims": [], "cost": it["cost"], "feed_price": it["price"]})
        if i % 10 == 0:
            print(f"[ZB-report] {i}/{len(items)}", flush=True)
        time.sleep(pc.SEARCH_DELAY)
    errs = [r for r in rows if r.get("error")]
    summ = summarize([r for r in rows if not r.get("error")])
    summ["errors"] = len(errs)
    OUT_DIR.mkdir(exist_ok=True)
    stamp = date.today().isoformat()
    (OUT_DIR / f"zoobaza_competitors_{stamp}.json").write_text(
        json.dumps({"summary": summ, "rows": rows, "tc_grid": TC_GRID, "step": cp.PRICE_STEP,
                    "target_margin": cp.MIN_PROFIT_COMPETITOR_FLOOR, "feed_to_opt": z.ZOOBAZA_FEED_TO_OPT},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
