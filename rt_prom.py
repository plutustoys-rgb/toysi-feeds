#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rt_prom.py — товари RoyalToys (rt-) у Prom-фіді prom_feed_top.xml (рішення власника 22.11.2026, дослівно: «став РТ тільки без дублів з тойсі,
тільки якщо в тойсі немає цього товара в наявності»). Мета: довести каталог Prom до ліміту підписки (TARGET_COUNT=6000) товарами, яких Toysi не дає.

ДВА ШАРИ (за зразком rt_eva.py)
 1. ВІДБІР (build_selection / ensure_selection → rt_prom_selection.json): для кожного rt-товару — клас за rt_dup_finder (DUP / GRAY / UNIQUE_BRAND / UNIQUE; ту ж функцію classify_dups, що й EVA)
    і категорія Prom. У Prom беремо ЛИШЕ UNIQUE і UNIQUE_BRAND. DUP (є двійник у наявності) і GRAY (сумнівний двійник) — НІ: власник сказав «без дублів» (на відміну від EVA, де GRAY беремо).
    Категорія Prom (category_id; Prom відхиляє чи автозмінює невідомі) — лише ВІДОМА: (1) ручна таблиця rt_prom_category_overrides.json {категорія RT (рос.): {"prom_id": N, "prom_name": ".."} | null=виключити};
    (2) правило категорії RT (≥MIN_AUTO товарів із високою довірою і ≥AGREE_T згоди) — kNN за назвою серед товарів Toysi із ВІДОМИМ Prom category_id (реальний id з prom_category_cache.json або курована мапа
    TOYSI_TO_PROM_CATEGORY; заглушка 29 «Товари, загальне» = «невідомо»), з вето по товару; (3) високодовірений kNN по самому товару. ПОРОГИ ДОВІРИ калібруються на ПІВ-вибірці Toysi (leave-one-out),
    а точність ПЕРЕВІРЯЄТЬСЯ на ВІДКЛАДЕНІЙ другій половині (TARGET_PREC=0.95); відкладена точність нижча за HOLDOUT_MIN_PREC → авто-категорій немає зовсім (лише ручна таблиця). Нема впевненості → товару в Prom НЕМАЄ.
    Категорії Toysi, виключені з Prom (велосипеди тощо), для RT теж виключені (їх kNN-сусіди мають мітку EXCL).
 2. ЕМІСІЯ (append_rt_offers): окремий від Toysi-циклу шлях на КОПІЇ дерева. Ціна RT = competitor_pricing.decide_price_for_platform(cost, None, "prom", категорія Toysi-сусіда, prom_category_id) — КАНОНІЧНА формула
    «без конкурента» (cost×NO_COMPETITOR_MULT, але не нижче нижньої межі; +PROM_ORDER_FEE_UAH у межі); фіксований мінімум MIN_PROM_PRICE=60 ₴ (див. константу). ПЕРЕД емісією — живий каталог Toysi (параметр catalog):
    якщо двійник (dup_of з supplier_rt.json / rt_dup_map.json) зараз у наявності (stock>0) — товар НЕ беремо. Ідентифікатор offer і vendorCode = «rt-<id>» (Prom віддає його в замовленні як sku/external_id →
    orders_watcher: toysi_code «rt-…» → order_router НЕ передає в Toysi, «обробка вручну»).
    Ємність: RT займають лише ВІЛЬНІ слоти (TOTAL_CAP − кількість Toysi-offer'ів у фіді) — RT ніколи не витісняють Toysi.
Брама: PROM_RT_IN_FEED=1 (за замовчуванням ВИМКНЕНО — фід бітово незмінний). Свіжість supplier_rt.json (≤SUPPLIER_MAX_H) і відбору (≤STALE_H) — fail-safe: застаріле → RT цього прогону НЕ додаємо.

ЧОГО ТУТ СВІДОМО НЕМАЄ (окремі етапи): репрайсер/delist/скан конкурентів для rt- (ціна без конкурента = cost×1.75, репрайсер Prom її не знижує — rt- не входять до select_top_items);
запис rt- у prom_pushed_ledger (НЕ додаємо: prom_catalog_sync інакше вважав би їх «зниклими з Toysi» і гасив); авто-виконання Prom-замовлень з rt- (rt_order_runner зараз лише EVA).
Товар, що випав з фіду, Prom за налаштуванням імпорту «Статус товарів, яких немає в файлі» = «Немає в наявності» позначає недоступним сам — жодних stub-offer'ів і API-видалень цей модуль не робить."""
import copy
import csv
import json
import os
import random
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEL_FILE = Path(os.environ.get("RT_PROM_SELECTION_FILE", str(HERE / "rt_prom_selection.json")))
OVERRIDES_FILE = HERE / "rt_prom_category_overrides.json"
SUPPLIER_FILE = Path(os.environ.get("SUPPLIER_RT_FILE", str(HERE / "supplier_rt.json")))
DUP_FILE = HERE / "rt_dup_map.json"
STALE_H = 36                    # відбір старший за це → RT цього прогону не додаємо (як rt_eva)
SUPPLIER_MAX_H = 6.0            # supplier_rt.json старший за це → залишки/ціни RT не віддаємо (як rt_eva.supplier_fresh)
REBUILD_AFTER_H = 12.0
TARGET_PREC = 0.95              # точність kNN-категорії, яку мають давати пороги (калібрування на половині A)
HOLDOUT_MIN_PREC = 0.93         # точність на відкладеній половині B: нижче → авто-категорій немає (лише ручна таблиця). 0.93 = TARGET_PREC − 0.02 допуску на шум малої вибірки
HOLDOUT_MIN_N = 30              # менше відібраних у відкладеній половині → точність недостовірна → авто-категорій немає
CAL_MIN_N = 50                  # вузол сітки порогів потрібен хоча б на стільки прикладів
MIN_AUTO = 5
AGREE_T = 0.8
POST_MAX_DF = 400
TOPK = 5
INCLUDE_CLASSES = ("UNIQUE", "UNIQUE_BRAND")      # власник 22.11: без дублів; GRAY (сумнівний двійник) НЕ беремо — на відміну від EVA
MIN_RT_STOCK = 3                # як min_stock у rt_selection.json (експорт на сайт не віддає RT з залишком менше): менший залишок → ризик продажу того, чого в постачальника вже нема
MIN_CATALOG_N = 25_000          # каталог Toysi усічений (інакше «двійників у наявності немає» було б хибним) → RT не додаємо / відбір не перебудовуємо
MIN_KEEP_RATIO = 0.7            # новий відбір < 70% попереднього → відкидаємо (усічений каталог/збій)
STUB_PROM_CATEGORY = "29"       # «Товари, загальне» (25% комісії) = «Prom не розпізнав» (generate_prom_feed.PROM_STUB_CATEGORY_ID) → для RT категорією бути не може
# Мінімальна ціна RT на Prom — 60 ₴ (завдання власника-координатора). Обґрунтування цифрою: competitor_pricing.PROM_ORDER_FEE_UAH = 10 ₴ фіксовано за кожне замовлення («Дешева доставка»);
# при ціні 60 ₴ це 10/60 ≈ 16,7% виручки ДО категорійної комісії (≈10–25%) і комісії оплати — запас до нуля зникає; дешевше — збиток. Перевіряється ДОДАТКОВО до формули (формула сама цього не відсікає).
MIN_PROM_PRICE = 60.0
PLATFORM = "prom"
OFFERS_PICTURES_MAX = 10


def gate_on() -> bool:
    return os.environ.get("PROM_RT_IN_FEED") == "1"


def _atomic_write(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _num(x):
    try:
        v = float(str(x).replace(",", "."))
        return v if v == v and v not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


# ───────────────────────────── джерела ─────────────────────────────
def load_overrides() -> dict:
    try:
        d = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def load_supplier() -> dict:
    try:
        d = json.loads(SUPPLIER_FILE.read_text(encoding="utf-8"))
        items = d.get("items")
        return items if isinstance(items, dict) and items else {}
    except (OSError, ValueError, AttributeError):
        return {}


def supplier_fresh(max_h: float = SUPPLIER_MAX_H) -> bool:
    """supplier_rt.json оновлювався не раніше max_h годин тому (поле updated, ISO). Нема/битий/без дати → False (fail-safe)."""
    try:
        d = json.loads(SUPPLIER_FILE.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(str(d.get("updated")).replace("Z", "+00:00"))
        now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.now()
        return now - ts <= timedelta(hours=max_h)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def load_dup_map() -> dict:
    """rt_dup_map.json {"rt-<id>": "<id Toysi>"} (rt_dup_finder; те саме, що йде в поле dup_of у supplier_rt.json)."""
    try:
        d = json.loads(DUP_FILE.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def load_selection():
    try:
        d = json.loads(SEL_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("items"), dict) else None
    except (OSError, ValueError):
        return None


def selection_fresh(sel, now=None) -> bool:
    try:
        return (now or datetime.now()) - datetime.fromisoformat(sel["built_at"]) <= timedelta(hours=STALE_H)
    except (KeyError, TypeError, ValueError):
        return False


# ───────────────────────────── категорії Prom для Toysi-товарів ─────────────────────────────
def toysi_prom_label(t: dict, cache: dict):
    """Мітка Prom-категорії товару Toysi: (prom_id, caption, назва категорії Toysi) | ("EXCL", …) | None (невідомо).
    Пріоритет — як у generate_prom_feed._build_xml (без виведеного «derive»: це вторинна здогадка, у навчальні мітки її не беремо): 1) реальний id із prom_category_cache (не заглушка 29);
    2) курована мапа TOYSI_TO_PROM_CATEGORY за назвою категорії Toysi. Категорії, виключені з Prom (generate_prom_feed_top.is_excluded_category), → EXCL."""
    import generate_prom_feed as gp
    from generate_prom_feed_top import is_excluded_category
    tcat = (t.get("category_name") or "").strip()
    if is_excluded_category(t):
        return ("EXCL", "не виставляємо на Prom", tcat)
    ent = (cache or {}).get(str(t.get("id"))) or {}
    cid = str(ent.get("category_id") or "")
    if cid and cid != STUB_PROM_CATEGORY:
        return (cid, str(ent.get("category_caption") or ""), tcat)
    cur = gp._map_curated_category(tcat)
    if cur and str(cur) != STUB_PROM_CATEGORY:
        return (str(cur), "", tcat)
    return None


def known_prom_ids(labelled_ids) -> set:
    """Id Prom-категорій, дозволені для RT: що вже віддає Toysi-частина (навчальні мітки) ∪ курована мапа ∪ таблиця комісій кабінету (PROM_CATEGORY_ID_COMMISSION). Заглушка 29 — ні."""
    import generate_prom_feed as gp
    import competitor_pricing as cp
    ids = {str(i) for i in labelled_ids}
    ids |= {str(v) for v in gp.TOYSI_TO_PROM_CATEGORY.values()}
    ids |= {str(k) for k in cp.PROM_CATEGORY_ID_COMMISSION}
    ids.discard(STUB_PROM_CATEGORY)
    ids.discard("EXCL")
    return ids


class PromKnn:
    """kNN за назвою серед Toysi-товарів з відомою Prom-категорією. Метрика схожості — та сама, що в rt_dup_finder/rt_eva (IDF-зважений Жаккар основ слів, Matcher.wjac)."""

    def __init__(self, m, labels: dict):
        self.m = m
        self.labels = labels                                         # {toysi_id: (prom_id, caption, toysi_category)}
        self.items = [t for t in m.tel if t["id"] in labels]
        df = Counter()
        for t in self.items:
            df.update(t["_tok"])
        self.post = defaultdict(list)
        for i, t in enumerate(self.items):
            for w in t["_tok"]:
                if df[w] <= POST_MAX_DF:
                    self.post[w].append(i)

    def predict(self, tokens, exclude_id=None):
        acc = Counter()
        for w in tokens:
            for i in self.post.get(w, ()):
                acc[i] += self.m.idf(w)
        top = []
        for i, _ in acc.most_common(40):
            t = self.items[i]
            if exclude_id is not None and t["id"] == exclude_id:
                continue
            top.append((self.m.wjac(tokens, t["_tok"]), t))
        top.sort(key=lambda x: -x[0])
        top = top[:TOPK]
        if not top:
            return None
        votes = Counter()
        for s, t in top:
            votes[self.labels[t["id"]][0]] += s
        win, w = votes.most_common(1)[0]
        tot = sum(votes.values())
        best = next(t for s, t in top if self.labels[t["id"]][0] == win)
        _pid, cap, tcat = self.labels[best["id"]]
        return {"prom_id": win, "prom_name": cap, "toysi_cat": tcat, "share": (w / tot) if tot else 0.0, "top1": top[0][0]}


def calibrate(knn: PromKnn, rnd, n: int = 2000) -> dict:
    """Пороги (top1, share) на половині A вибірки Toysi (leave-one-out: сусід-сам-себе виключено); точність ПЕРЕВІРЯЄТЬСЯ на відкладеній половині B.
    → {"thr": (top1, share)|None, "prec_a", "n_a", "prec_b", "n_b", "labelled", "reason"}. thr=None → авто-категорій немає."""
    pool = [t for t in knn.items if knn.labels[t["id"]][0] != "EXCL"]
    out = {"thr": None, "prec_a": None, "n_a": 0, "prec_b": None, "n_b": 0, "labelled": len(pool), "reason": ""}
    if len(pool) < 2 * CAL_MIN_N:
        out["reason"] = f"замало навчальних товарів Toysi з відомою Prom-категорією ({len(pool)} < {2 * CAL_MIN_N})"
        return out
    sample = rnd.sample(pool, min(n, len(pool)))
    half = len(sample) // 2
    sets = {"a": sample[:half], "b": sample[half:]}
    loo = {}
    for k, part in sets.items():
        res = []
        for t in part:
            p = knn.predict(t["_tok"], exclude_id=t["id"])
            if p:
                res.append((p, p["prom_id"] == knn.labels[t["id"]][0]))
        loo[k] = res
    best = None
    for x in (0.3, 0.4, 0.5, 0.6, 0.7):
        for y in (0.6, 0.8, 1.0):
            sel = [ok for p, ok in loo["a"] if p["top1"] >= x and p["share"] >= y]
            if len(sel) < CAL_MIN_N:
                continue
            prec = sum(sel) / len(sel)
            if prec >= TARGET_PREC and (best is None or len(sel) > best[2]):
                best = (x, y, len(sel), prec)
    if best is None:
        out["reason"] = f"жоден вузол сітки порогів не дає точності ≥{TARGET_PREC} на половині A"
        return out
    x, y = best[0], best[1]
    selb = [ok for p, ok in loo["b"] if p["top1"] >= x and p["share"] >= y]
    out.update({"prec_a": round(best[3], 3), "n_a": best[2], "n_b": len(selb), "prec_b": round(sum(selb) / len(selb), 3) if selb else None})
    if len(selb) < HOLDOUT_MIN_N or out["prec_b"] < HOLDOUT_MIN_PREC:
        out["reason"] = f"відкладена вибірка не підтвердила точність (n_b={len(selb)}, prec_b={out['prec_b']}; потрібно n≥{HOLDOUT_MIN_N}, prec≥{HOLDOUT_MIN_PREC})"
        return out
    out["thr"] = (x, y)
    return out


# ───────────────────────────── відбір ─────────────────────────────
def build_selection(ty: dict, sup_items: dict, cache: dict, overrides: dict | None = None, prev_sel: dict | None = None) -> dict:
    """Повний відбір (важкий: Matcher по Toysi+RT і kNN; хвилини). ty — повний каталог Toysi; sup_items — supplier_rt.json["items"] {rt-<id>: item}; cache — prom_category_cache.
    prev_sel — попередній відбір: категорія уже відібраного товару ЛИШАЄТЬСЯ тією самою, поки лишається валідною (Prom: категорія вже створеної картки не має «гуляти» між прогонами)."""
    import rt_dup_finder as rd
    import rt_eva
    rt = {sid[3:]: v for sid, v in sup_items.items()}
    sids = list(sup_items)
    cls, m = rt_eva.classify_dups(rt, ty, sids, rd)
    labels = {}
    for t in m.tel:
        lab = toysi_prom_label(t, cache)
        if lab:
            labels[t["id"]] = lab
    knn = PromKnn(m, labels)
    cal = calibrate(knn, random.Random(20261122))
    thr = cal["thr"]
    valid = known_prom_ids({v[0] for v in labels.values()})
    overrides = load_overrides() if overrides is None else overrides
    prev_items = (prev_sel or {}).get("items") or {}
    per_cat = defaultdict(list)
    preds = {}
    for sid in sids:
        if cls[sid] not in INCLUDE_CLASSES:
            continue
        r = sup_items[sid]
        tokens = rd.tokens_for_sim(r["name"], set(rd.normalize_name(r.get("vendor") or "")))
        p = knn.predict(tokens)
        high = bool(p and thr and p["top1"] >= thr[0] and p["share"] >= thr[1])
        preds[sid] = (p, high)
        per_cat[(r.get("category") or "?").strip()].append(sid)
    rules = {}
    for cname, lst in per_cat.items():
        hv = [preds[s][0]["prom_id"] for s in lst if preds[s][1]]
        if not hv:
            continue
        dom, c = Counter(hv).most_common(1)[0]
        if len(hv) >= MIN_AUTO and c / len(hv) >= AGREE_T and dom != "EXCL" and dom in valid:
            p0 = next(preds[s][0] for s in lst if preds[s][1] and preds[s][0]["prom_id"] == dom)
            rules[cname] = {"prom_id": dom, "prom_name": p0["prom_name"], "toysi_cat": p0["toysi_cat"], "agree": round(c / len(hv), 2), "n_high": len(hv)}
    items, drop, bad_ov = {}, Counter(), set()
    for cname, lst in per_cat.items():
        ov = overrides.get(cname, "__none__")
        for sid in lst:
            p, high = preds[sid]
            got, src = None, ""
            if ov is None:
                drop["override_exclude"] += 1
                continue
            if isinstance(ov, dict):
                oid = str(ov.get("prom_id") or "")
                if oid.isdigit() and oid in valid:
                    got, src = {"prom_id": oid, "prom_name": ov.get("prom_name") or "", "toysi_cat": ov.get("toysi_cat") or ""}, "override"
                else:
                    bad_ov.add(cname)                        # ручне рішення з невідомим/заглушковим id — ігноруємо, як ніби його нема
            if got is None and cname in rules:
                if high and p["prom_id"] != rules[cname]["prom_id"]:
                    drop["veto"] += 1                        # товар упевнено «іншої» категорії, ніж правило його категорії RT
                    continue
                got, src = dict(rules[cname]), "rule"
            elif got is None and high and p["prom_id"] != "EXCL" and p["prom_id"] in valid:
                got, src = {"prom_id": p["prom_id"], "prom_name": p["prom_name"], "toysi_cat": p["toysi_cat"]}, "item"
            if got is None:
                drop["no_category"] += 1
                continue
            prev = prev_items.get(sid) or {}
            if prev.get("prom_id") in valid and prev.get("prom_id") != got["prom_id"] and src != "override":
                got = {"prom_id": prev["prom_id"], "prom_name": prev.get("prom_name") or "", "toysi_cat": prev.get("toysi_cat") or got.get("toysi_cat") or ""}
                src = "sticky"
            items[sid] = {"prom_id": got["prom_id"], "prom_name": got.get("prom_name") or "", "toysi_cat": got.get("toysi_cat") or "", "cls": cls[sid], "src": src}
    review = []
    for cname, lst in sorted(per_cat.items(), key=lambda kv: -len(kv[1])):
        if cname in rules or cname in overrides:
            continue
        allv = [preds[s][0] for s in lst if preds[s][0]]
        votes = Counter(p["prom_id"] for p in allv)
        guess, gname, share = "", "", 0.0
        if votes:
            guess, gc = votes.most_common(1)[0]
            share = gc / len(allv)
            gname = next(p["prom_name"] for p in allv if p["prom_id"] == guess)
        review.append({"rt_category": cname, "items": len(lst), "guess_prom_id": guess, "guess_prom_name": gname, "guess_share": round(share, 2), "guess_valid": guess in valid,
                       "examples": [sup_items[s]["name"][:60] for s in lst[:3]]})
    stats = {"rt_total": len(sids), "classes": dict(Counter(cls.values())), "in_feed_candidates": len(items), "by_src": dict(Counter(v["src"] for v in items.values())),
             "dropped": dict(drop), "rules": len(rules), "bad_overrides": sorted(bad_ov),
             "calibration": {"thresholds": ({"top1": thr[0], "share": thr[1]} if thr else None), "prec_cal_half": cal["prec_a"], "n_cal_half": cal["n_a"], "prec_holdout": cal["prec_b"], "n_holdout": cal["n_b"],
                             "labelled_toysi": cal["labelled"], "reason": cal["reason"]}}
    return {"built_at": datetime.now().isoformat(timespec="seconds"), "toysi_n": len(ty), "rt_n": len(sids), "items": items, "rules": rules, "review": review,
            "valid_ids": sorted(valid), "dup_ids": sorted(s for s, c in cls.items() if c == "DUP"), "stats": stats}


def ensure_selection(ty: dict, cache: dict, rebuild_after_h: float = REBUILD_AFTER_H):
    """Відбір для фіду: свіжий кеш (< rebuild_after_h) → його; інакше перебудова. Гарди: каталог Toysi усічений → перебудови немає (лишаємо свіжий старий або None);
    новий відбір суттєво менший за попередній → відкидаємо; збій → старий, якщо ще не застарів."""
    sup = load_supplier()
    if not sup:
        return None
    sel = load_selection()
    fresh_old = sel if sel and selection_fresh(sel) else None
    if fresh_old and fresh_old.get("rt_n", 0) > 0 and (datetime.now() - datetime.fromisoformat(fresh_old["built_at"])) <= timedelta(hours=rebuild_after_h):
        return fresh_old
    if len(ty) < MIN_CATALOG_N:
        print(f"[rt_prom] каталог Toysi усічений ({len(ty)}) — відбір не перебудовую", file=sys.stderr)
        return fresh_old
    try:
        new = build_selection(ty, sup, cache, prev_sel=sel)
    except Exception as e:  # noqa: BLE001
        print(f"[rt_prom] перебудова відбору не вдалась ({type(e).__name__}: {str(e)[:120]}) — {'лишаємо попередній' if fresh_old else 'RT у фід цього прогону НЕ йдуть'}", file=sys.stderr)
        return fresh_old
    prev_n = (sel or {}).get("stats", {}).get("in_feed_candidates") or 0
    if fresh_old and prev_n and new["stats"]["in_feed_candidates"] < prev_n * MIN_KEEP_RATIO:
        print(f"[rt_prom] новий відбір підозріло менший ({new['stats']['in_feed_candidates']} проти {prev_n}) — відкинуто, лишаємо попередній", file=sys.stderr)
        return fresh_old
    _atomic_write(SEL_FILE, new)
    return new


# ───────────────────────────── емісія ─────────────────────────────
def _pictures(r: dict) -> list:
    pics = r.get("images")
    if isinstance(pics, str):
        try:
            pics = json.loads(pics.replace("'", '"'))
        except ValueError:
            pics = []
    return [p for p in (pics or []) if isinstance(p, str) and p.startswith("https://") and len(p) <= 1999][:OFFERS_PICTURES_MAX]


def _dims_params(r: dict) -> list:
    out = []
    d = r.get("dims_cm")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            d = None
    try:
        if d and len(d) == 3 and all(float(x) > 0 for x in d):
            out.append(("Розміри упаковки (см)", " × ".join(f"{float(x):g}" for x in d)))
    except (TypeError, ValueError):
        pass
    w = _num(r.get("weight_kg"))
    if w and w > 0:
        out.append(("Вага (кг)", f"{w:g}"))
    return out


def toysi_twin_in_stock(sid: str, r: dict, catalog: dict, dup_map: dict) -> str:
    """Id двійника Toysi, що ЗАРАЗ у наявності (живий каталог, stock>0), або "". Джерела двійників: dup_of з supplier_rt.json і rt_dup_map.json (обидва — результат rt_dup_finder)."""
    for tid in {str(r.get("dup_of") or ""), str(dup_map.get(sid) or "")}:
        if not tid:
            continue
        it = catalog.get(tid)
        try:
            if it and int(it.get("stock") or 0) > 0:
                return tid
        except (TypeError, ValueError):
            return tid                                       # нерозбірливий залишок двійника → не ризикуємо
    return ""


def build_offer_fields(sid: str, r: dict, sel_item: dict, catalog: dict, dup_map: dict):
    """→ (dict полів offer, "") або (None, причина). Усі перевірки; зіпсований offer не емітуємо. Чиста функція (без мережі)."""
    import competitor_pricing as cp
    import generate_prom_feed as gp
    import google_rt_feed as grf
    import rt_supplier_export as rse
    from trademark_filter import is_uno_trademark_blocked
    if r.get("kv_dup_of"):
        return None, "kv_shadow"                              # RT-«тінь» товару KIDDISVIT (рішення власника 17.11: показуємо KIDDISVIT)
    twin = toysi_twin_in_stock(sid, r, catalog, dup_map)
    if twin:
        return None, "toysi_twin_in_stock"
    raw_name = " ".join(str(r.get("name") or "").split())
    vendor = " ".join(str(r.get("vendor") or "").split())
    cat_src = (r.get("category") or "").strip()
    reason = rse.policy_reason({"id": sid[3:], "name": raw_name, "name_ua": raw_name, "category_name": cat_src})
    if reason:
        return None, "policy" if reason == "policy" else "not_toy"
    if gp.is_clearance_item(raw_name, cat_src, "") or gp.is_clearance_item(raw_name, sel_item.get("toysi_cat") or "", ""):
        return None, "clearance"
    if is_uno_trademark_blocked(raw_name, vendor):
        return None, "trademark_uno"
    if gp._is_russian_language(raw_name):
        return None, "russian_language"
    if len(raw_name) < 3:
        return None, "name"
    stock = _num(r.get("stock"))
    cost = _num(r.get("cost"))
    if stock is None or stock < MIN_RT_STOCK:
        return None, "stock"
    if not cost or cost <= 0 or cost < gp.MIN_SUPPLIER_PRICE:
        return None, "cost"
    pics = _pictures(r)
    if not pics:
        return None, "no_pictures"
    desc = grf.scrub_description(rse.plain_text(r.get("description") or ""))
    if len(desc) < 30:
        return None, "short_desc"
    if grf.leaks(raw_name, vendor, desc):
        return None, "supplier_name_leak"
    params = _dims_params(r)
    if not params:
        return None, "no_dims"
    try:
        pid_int = int(sel_item["prom_id"])
    except (KeyError, TypeError, ValueError):
        return None, "category"
    tcat = sel_item.get("toysi_cat") or None
    dec = cp.decide_price_for_platform(cost, None, PLATFORM, tcat, prom_category_id=pid_int)
    price = dec["price"]
    if price < MIN_PROM_PRICE:
        return None, "cheap"
    if price < cost:
        return None, "price"                                  # недосяжно за формулою (min_safe ≥ cost), але гарантія «не нижче собівартості» — остання лінія оборони
    commission = cp.compute_total_commission(PLATFORM, tcat, price, prom_category_id=pid_int)
    profit = price * (1 - commission) - cp.prom_effective_cost(cost)
    name = gp._truncate_name(gp.sanitize_prom_text(raw_name))
    kw_ua, kw_ru = gp.generate_keywords({"name": raw_name, "category_name": tcat or "", "vendor": vendor})
    return {"id": sid, "vendor_code": sid, "name": name, "price": price, "stock": int(stock), "category": str(pid_int), "pictures": pics,
            "vendor": gp.normalize_vendor(vendor) if vendor and not grf.leaks(vendor) else "", "description": gp.sanitize_prom_text(desc), "params": params,
            "keywords_ua": gp.dedupe_keywords(gp.sanitize_prom_text(kw_ua)) if kw_ua else "", "keywords": gp.dedupe_keywords(gp.sanitize_prom_text(kw_ru)) if kw_ru else "",
            "profit": profit}, ""


def validate_offer(f: dict, valid_ids: set) -> str:
    """'' = ок, інакше причина. Остання лінія оборони перед записом у XML."""
    if not re.fullmatch(r"rt-[0-9A-Za-z_-]{1,40}", f["id"]) or f["vendor_code"] != f["id"]:
        return "id"
    if not f["name"] or len(f["name"]) > 130:
        return "name"
    if f["price"] < MIN_PROM_PRICE or f["stock"] <= 0:
        return "price/stock"
    if f["category"] not in valid_ids or f["category"] == STUB_PROM_CATEGORY:
        return "category"
    if not f["pictures"] or not all(p.startswith("https://") for p in f["pictures"]):
        return "pictures"
    if len(f["description"]) < 30:
        return "fields"
    return ""


def load_prev_rt_ids(feed_path) -> set:
    """rt-id, які ВЖЕ у поточному prom_feed_top.xml (читати ДО перезапису файлу): при нестачі слотів лишаємо їх першими — менше churn."""
    try:
        return set(re.findall(r'<offer id="(rt-[^"]+)"', Path(feed_path).read_text(encoding="utf-8")))
    except OSError:
        return set()


def append_rt_offers(root: ET.Element, catalog: dict, selection: dict, sup_items: dict, total_cap: int, prev_ids: set | None = None) -> dict:
    """Дописує offer'и RT у <shop>/<offers>. root — результат generate_prom_feed._build_xml. Повертає статистику.
    Слотів: total_cap − (кількість Toysi-offer'ів у фіді); при нестачі — спершу ті, що вже були у фіді, далі за прибутком (profit за каноном decide_price_for_platform), далі за id."""
    shop = root.find("shop")
    offers_el = shop.find("offers")
    valid = set(selection.get("valid_ids") or [])
    dup_map = load_dup_map()
    free = max(0, int(total_cap) - len(offers_el))
    env_cap = os.environ.get("PROM_RT_MAX", "").strip()
    if env_cap.isdigit():
        free = min(free, int(env_cap))
    stats = Counter()
    ok = []
    for sid, si in sorted(selection.get("items", {}).items()):
        if si.get("cls") not in INCLUDE_CLASSES:
            continue
        r = sup_items.get(sid)
        if not r:
            stats["skip_not_in_supplier"] += 1
            continue
        f, why = build_offer_fields(sid, r, si, catalog, dup_map)
        if f is None:
            stats["skip_" + why] += 1
            continue
        bad = validate_offer(f, valid)
        if bad:
            stats["invalid_" + bad] += 1
            continue
        ok.append(f)
    prev_ids = prev_ids or set()
    ok.sort(key=lambda f: (f["id"] not in prev_ids, -f["profit"], f["id"]))
    stats["eligible"] = len(ok)
    stats["free_slots"] = free
    if len(ok) > free:
        stats["skip_no_free_slot"] = len(ok) - free
        ok = ok[:free]
    for f in sorted(ok, key=lambda f: f["id"]):
        o = ET.SubElement(offers_el, "offer", id=f["id"], available="true")
        ET.SubElement(o, "vendorCode").text = f["vendor_code"]
        ET.SubElement(o, "name").text = f["name"]            # російського тексту нема (rt_ru_text.json має інший ключ перекладу) → як у Toysi без rus-варіанту: українська
        ET.SubElement(o, "name_ua").text = f["name"]
        ET.SubElement(o, "price").text = f"{f['price']:.2f}"
        ET.SubElement(o, "currencyId").text = "UAH"
        ET.SubElement(o, "quantity_in_stock").text = str(f["stock"])
        ET.SubElement(o, "categoryId").text = f["category"]
        for p in f["pictures"]:
            ET.SubElement(o, "picture").text = p
        if f["vendor"]:
            ET.SubElement(o, "vendor").text = f["vendor"]
        ET.SubElement(o, "description").text = f["description"]
        ET.SubElement(o, "description_ua").text = f["description"]
        if f["keywords_ua"]:
            ET.SubElement(o, "keywords_ua").text = f["keywords_ua"]
        if f["keywords"]:
            ET.SubElement(o, "keywords").text = f["keywords"]
        for pn, pv in f["params"]:
            ET.SubElement(o, "param", name=pn).text = pv
        stats["emitted"] += 1
    return dict(stats)


def maybe_add_rt_offers(root: ET.Element, catalog: dict, cache: dict, total_cap: int, prev_feed_path=None) -> ET.Element:
    """Точка входу для generate_prom_feed.generate_feed(post_build=…): лише за брамою PROM_RT_IN_FEED=1, лише зі свіжим постачальником і відбором, лише на повному каталозі Toysi.
    Працює на КОПІЇ дерева: будь-який збій → повертається ОРИГІНАЛ (Toysi-частина без змін, RT у цьому прогоні не йдуть)."""
    try:
        if not gate_on():
            return root
        if len(catalog) < MIN_CATALOG_N:
            print(f"[Prom] RT: каталог Toysi усічений ({len(catalog)}) — RoyalToys у цей фід НЕ додано.", file=sys.stderr)
            return root
        if not supplier_fresh():
            print(f"[Prom] RT: supplier_rt.json застарів (>{SUPPLIER_MAX_H:g} год) чи недоступний — RoyalToys у цей фід НЕ додано (Prom сам позначить їх «Немає в наявності»).", file=sys.stderr)
            return root
        sel = ensure_selection(catalog, cache)
        if not sel or not selection_fresh(sel):
            print("[Prom] RT: відбір відсутній/застарілий — RoyalToys у цей фід НЕ додано (Toysi-частина без змін).", file=sys.stderr)
            return root
        work = copy.deepcopy(root)
        prev_ids = load_prev_rt_ids(prev_feed_path) if prev_feed_path else set()
        st = append_rt_offers(work, catalog, sel, load_supplier(), total_cap, prev_ids)
        print(f"[Prom] RoyalToys у фіді: {st}")
        return work
    except Exception as e:  # noqa: BLE001
        print(f"[Prom] RT: збій додавання ({type(e).__name__}: {str(e)[:160]}) — RoyalToys у фід НЕ додано, Toysi-частина без змін.", file=sys.stderr)
        return root


def main(argv=None) -> int:
    """CLI: python rt_prom.py --build [--dry] → перебудувати rt_prom_selection.json і надрукувати підсумок (важко; лише VPS: читає живий каталог Toysi, supplier_rt.json, prom_category_cache.json)."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--dry", action="store_true", help="не записувати rt_prom_selection.json")
    ap.add_argument("--review-csv", help="після --build записати CSV «категорії RT без рішення» (для власника)")
    a = ap.parse_args(argv)
    if not a.build:
        ap.print_help()
        return 0
    from parser import fetch_toysi_catalog
    from prom_competitor_pricer import _load_prom_category_cache
    ty = fetch_toysi_catalog()
    sup = load_supplier()
    cache = _load_prom_category_cache()
    if len(ty) < MIN_CATALOG_N or not sup:
        print("[rt_prom] каталог Toysi/supplier_rt порожній або усічений — відбір не чіпаю", file=sys.stderr)
        return 1
    sel = build_selection(ty, sup, cache, prev_sel=load_selection())
    if not a.dry:
        _atomic_write(SEL_FILE, sel)
    print("[rt_prom]", "(dry) " if a.dry else "записано " + SEL_FILE.name + " ", json.dumps(sel["stats"], ensure_ascii=False))
    if a.review_csv:
        with open(a.review_csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["категорія_RT", "товарів", "здогадка_Prom_id", "здогадка_Prom_назва", "частка_здогадки", "id_валідний", "приклади_назв"])
            for r in sel["review"]:
                w.writerow([r["rt_category"], r["items"], r["guess_prom_id"], r["guess_prom_name"], r["guess_share"], r["guess_valid"], " | ".join(r["examples"])])
        print("[rt_prom] огляд категорій:", a.review_csv, len(sel["review"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
