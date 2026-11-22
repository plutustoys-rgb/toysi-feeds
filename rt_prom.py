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
    Ємність: RT займають лише ВІЛЬНІ слоти (TOTAL_CAP − Toysi-offer'и) і не більше PROM_RT_MAX (за замовчуванням 1500, не вище 3000) — RT ніколи не витісняють Toysi. Журнал rt_prom_listed_state.json (усі колись емітовані rt-id): ті, що випали з активних, лишаються в кабінеті Prom «Немає в наявності» і займають слоти (catalog_sync їх не прибере) — віддаються заглушками available="false" і ВІДНІМАЮТЬСЯ від ємності активних.
Брама: PROM_RT_IN_FEED=1 (за замовчуванням ВИМКНЕНО — фід бітово незмінний). Свіжість supplier_rt.json (≤SUPPLIER_MAX_H) і відбору (≤STALE_H) — fail-safe: застаріле → RT цього прогону НЕ додаємо.

ЧОГО ТУТ СВІДОМО НЕМАЄ (окремі етапи): репрайсер/delist/скан конкурентів для rt- (ціна без конкурента = cost×1.75, репрайсер Prom її не знижує — rt- не входять до select_top_items);
запис rt- у prom_pushed_ledger (НЕ додаємо: prom_catalog_sync інакше вважав би їх «зниклими з Toysi» і гасив); авто-виконання Prom-замовлень з rt- (rt_order_runner зараз лише EVA).
Двійник Toysi перевіряється ЗА ЖИВИМ stock>0 на кожній емісії: відбір записує ПОТЕНЦІЙНИХ двійників без урахування залишку (брендових і «сліпих» за назвою без бренду, wjac ≥0.75), dup_of/rt_dup_map/відбір — fail-closed (id двійника, якого нема в каталозі → RT не емітуємо).
Жодних API-видалень цей модуль не робить; осиротілі rt- віддаються заглушками available="false" (див. вище)."""
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
ISOLATED_SIM = 0.6              # «ізольований» товар Toysi: найближчий сусід у каталозі має wjac < цього (немає близького двійника-варіанта) — проксі для RT
REF_THR = (0.3, 0.6)            # пороги першої версії (PR #2004) — лише для звіту «що було б зі старими порогами на ізольованій вибірці»
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
BLIND_SIM = 0.75                # «сліпий» двійник: схожість назв (IDF-Жаккар без бренду) із БУДЬ-ЯКИМ товаром Toysi незалежно від бренду ≥ цього → RT вважаємо дублем (поріг rt_dup_finder.SIM_T)
RT_DEFAULT_MAX = 1500           # жорсткий верхній ліміт RT у фіді (активні + осиротілі)
RT_ABS_MAX = 3000               # env PROM_RT_MAX не може піднятись вище цього
LISTED_FILE = Path(os.environ.get("RT_PROM_LISTED_FILE", str(HERE / "rt_prom_listed_state.json")))
DELETED_FILE = Path(os.environ.get("RT_PROM_DELETED_FILE", str(HERE / "rt_prom_deleted_state.json")))   # надгробки rt-карток, видалених rt_prom_cleanup.py: {"v":1,"deleted":{rt-id: ISO-час}} — НЕ віддаються у фід (інакше Prom «відновив» би видалене)
_XML_BAD_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")      # символи, недопустимі в XML 1.0 (і сурогати, що не кодуються в utf-8)


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
        if not all(isinstance(v, dict) and "twins" in v for v in sel["items"].values()):
            return False                                   # відбір зібрано старою версією (без ключа twins) — захисту від двійників, що повернулись у наявність, нема → недійсний, перебудувати
        return (now or datetime.now()) - datetime.fromisoformat(sel["built_at"]) <= timedelta(hours=STALE_H)
    except (KeyError, TypeError, ValueError, AttributeError):
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


def calibrate(knn: PromKnn, rnd, n: int = 5000) -> dict:
    """Пороги (top1, share) на половині A вибірки Toysi (leave-one-out: сусід-сам-себе виключено); точність ПЕРЕВІРЯЄТЬСЯ на відкладеній половині B.
    ВИМІР — лише на «ізольованих» товарах Toysi: без близького двійника-варіанта в тому ж каталозі (найближчий сусід wjac < ISOLATED_SIM) — це проксі для RT, чиїх двійників у Toysi нема за визначенням
    (звичайний leave-one-out на Toysi-із-сусідами-варіантами завищує точність). Пороги беруться з ЦЬОГО виміру, не з Toysi-із-сусідами.
    → {"thr": (top1, share)|None, "prec_a", "n_a", "prec_b", "n_b" (ізольовані), "isolated_n", "prec_b_all" (усі, для порівняння), "ref_thr_prec_b"/"ref_thr_n_b" (старі пороги REF_THR на ізольованих B), "labelled", "reason"}. thr=None → авто-категорій немає."""
    pool = [t for t in knn.items if knn.labels[t["id"]][0] != "EXCL"]
    out = {"thr": None, "prec_a": None, "n_a": 0, "prec_b": None, "n_b": 0, "isolated_n": 0, "prec_b_all": None, "ref_thr_prec_b": None, "ref_thr_n_b": 0, "labelled": len(pool), "reason": ""}
    if len(pool) < 2 * CAL_MIN_N:
        out["reason"] = f"замало навчальних товарів Toysi з відомою Prom-категорією ({len(pool)} < {2 * CAL_MIN_N})"
        return out
    sample = rnd.sample(pool, min(n, len(pool)))
    half = len(sample) // 2
    sets = {"a": sample[:half], "b": sample[half:]}
    loo, loo_all = {}, {}
    for k, part in sets.items():
        res, res_all = [], []
        for t in part:
            p = knn.predict(t["_tok"], exclude_id=t["id"])
            if p:
                row = (p, p["prom_id"] == knn.labels[t["id"]][0])
                res_all.append(row)
                if p["top1"] < ISOLATED_SIM:
                    res.append(row)
        loo[k], loo_all[k] = res, res_all
    out["isolated_n"] = len(loo["a"]) + len(loo["b"])
    best = None
    for x in (0.3, 0.4, 0.5, 0.6, 0.7):
        for y in (0.6, 0.8, 1.0):
            sel = [ok for p, ok in loo["a"] if p["top1"] >= x and p["share"] >= y]
            if len(sel) < CAL_MIN_N:
                continue
            prec = sum(sel) / len(sel)
            if prec >= TARGET_PREC and (best is None or len(sel) > best[2]):
                best = (x, y, len(sel), prec)
    rx, ry = REF_THR
    selr = [ok for p, ok in loo["b"] if p["top1"] >= rx and p["share"] >= ry]
    out.update({"ref_thr_n_b": len(selr), "ref_thr_prec_b": round(sum(selr) / len(selr), 3) if selr else None})
    if best is None:
        out["reason"] = f"жоден вузол сітки порогів не дає точності ≥{TARGET_PREC} на ізольованих товарах половини A (n_ізольованих={len(loo['a'])})"
        return out
    x, y = best[0], best[1]
    selb = [ok for p, ok in loo["b"] if p["top1"] >= x and p["share"] >= y]
    alls = [ok for p, ok in loo_all["b"] if p["top1"] >= x and p["share"] >= y]
    out.update({"prec_a": round(best[3], 3), "n_a": best[2], "n_b": len(selb), "prec_b": round(sum(selb) / len(selb), 3) if selb else None, "prec_b_all": round(sum(alls) / len(alls), 3) if alls else None})
    if len(selb) < HOLDOUT_MIN_N or out["prec_b"] < HOLDOUT_MIN_PREC:
        out["reason"] = f"відкладена вибірка не підтвердила точність (n_b={len(selb)}, prec_b={out['prec_b']}; потрібно n≥{HOLDOUT_MIN_N}, prec≥{HOLDOUT_MIN_PREC})"
        return out
    out["thr"] = (x, y)
    return out


# ───────────────────────────── відбір ─────────────────────────────
def _forced_stock_catalog(ty: dict) -> dict:
    """Копія каталогу, де кожен товар «в наявності» (stock ≥ 1): rt_dup_finder бере кандидатів лише зі stock>0, тож ТАК шукаємо потенційних двійників незалежно від поточного залишку (товар Toysi, що повернеться в наявність пізніше)."""
    out = {}
    for k, v in ty.items():
        try:
            st = int(v.get("stock") or 0)
        except (TypeError, ValueError):
            st = 0
        out[k] = v if st > 0 else dict(v, stock=1)
    return out


def _potential_twins(rt: dict, ty: dict, sids: list, rd, cand_sids: list) -> dict:
    """{rt-sid: [id Toysi, …]} — ПОТЕНЦІЙНІ двійники кандидатів БЕЗ урахування залишку Toysi (запис у відбір; жива перевірка stock — на емісії).
    (1) брендові: рішення DUP за rt_dup_finder + кандидати з sim ≥ GRAY_SIM або збігом артикула; (2) «сліпі»: wjac назви без бренду ≥ BLIND_SIM із БУДЬ-ЯКИМ товаром Toysi незалежно від бренду
    (RT з порожнім/іншим брендом інакше дістав би UNIQUE за конструкцією: Matcher.evaluate без кандидатів того ж бренду → None). Пошук — за індексом токенів (як PromKnn), не перебором."""
    import rt_eva
    ty_all = _forced_stock_catalog(ty)
    dup, ev, m_all = rd.find_dups(rt, ty_all, sids)
    post = defaultdict(list)
    df = Counter()
    for t in m_all.tel:
        df.update(t["_tok"])
    for i, t in enumerate(m_all.tel):
        for w in t["_tok"]:
            if df[w] <= POST_MAX_DF:
                post[w].append(i)
    out = {}
    for sid in cand_sids:
        tw = set()
        if sid in dup:
            tw.add(str(dup[sid][0]))
        for c in (ev.get(sid) or {}).get("cands", []):
            if c["sim"] >= rt_eva.GRAY_SIM or c["art"]:
                tw.add(str(c["tid"]))
        r = rt[sid[3:]]
        tokens = rd.tokens_for_sim(r["name"], set(rd.normalize_name(r.get("vendor") or "")))
        acc = Counter()
        for w in tokens:
            for i in post.get(w, ()):
                acc[i] += m_all.idf(w)
        for i, _ in acc.most_common(40):
            t = m_all.tel[i]
            if m_all.wjac(tokens, t["_tok"]) >= BLIND_SIM:
                tw.add(str(t["id"]))
        out[sid] = sorted(tw)
    return out


def build_selection(ty: dict, sup_items: dict, cache: dict, overrides: dict | None = None, prev_sel: dict | None = None) -> dict:
    """Повний відбір (важкий: Matcher по Toysi+RT і kNN; хвилини). ty — повний каталог Toysi; sup_items — supplier_rt.json["items"] {rt-<id>: item}; cache — prom_category_cache.
    prev_sel — попередній відбір: категорія уже відібраного товару ЛИШАЄТЬСЯ тією самою, поки лишається валідною (Prom: категорія вже створеної картки не має «гуляти» між прогонами)."""
    import rt_dup_finder as rd
    import rt_eva
    rt = {sid[3:]: v for sid, v in sup_items.items()}
    sids = list(sup_items)
    cls, m = rt_eva.classify_dups(rt, ty, sids, rd)
    twins = _potential_twins(rt, ty, sids, rd, [sid for sid in sids if cls[sid] in INCLUDE_CLASSES])
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
                if p is None:
                    drop["rule_no_item_knn"] += 1            # правило категорії RT без жодного item-level підтвердження → не беремо (лише ручна таблиця рятує такий товар)
                    continue
                if p["prom_id"] != rules[cname]["prom_id"]:
                    drop["veto"] += 1                        # item-level kNN (будь-якої довіри) суперечить правилу категорії RT
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
            items[sid] = {"prom_id": got["prom_id"], "prom_name": got.get("prom_name") or "", "toysi_cat": got.get("toysi_cat") or "", "cls": cls[sid], "src": src,
                          "twins": twins.get(sid, [])}
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
             "dropped": dict(drop), "rules": len(rules), "with_potential_twin": sum(1 for v in items.values() if v["twins"]), "bad_overrides": sorted(bad_ov),
             "calibration": {"thresholds": ({"top1": thr[0], "share": thr[1]} if thr else None), "prec_cal_half": cal["prec_a"], "n_cal_half": cal["n_a"], "prec_holdout": cal["prec_b"], "n_holdout": cal["n_b"],
                             "labelled_toysi": cal["labelled"], "isolated_n": cal["isolated_n"], "prec_holdout_all_neighbours": cal["prec_b_all"],
                             "old_thr_prec_holdout": cal["ref_thr_prec_b"], "old_thr_n_holdout": cal["ref_thr_n_b"], "reason": cal["reason"]}}
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
    return [p for p in (pics or []) if isinstance(p, str) and p.startswith("https://") and len(p) <= 1999 and not _XML_BAD_RE.search(p)][:OFFERS_PICTURES_MAX]


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


def toysi_twin_block(sid: str, r: dict, sel_item: dict, catalog: dict, dup_map: dict) -> str:
    """Причина НЕ емітувати RT через двійника Toysi, або "" (можна). FAIL-CLOSED. Двійники = dup_of з supplier_rt.json ∪ rt_dup_map.json ∪ sel_item["twins"] (потенційні двійники БЕЗ урахування залишку, записані
    відбором: rt_dup_finder бере кандидатів лише зі stock>0, тож двійник, що був OOS на момент відбору, інакше лишився б невидимим). Критерій «в наявності» = stock>0 за ЖИВИМ каталогом
    (як generate_prom_feed._build_xml: available = stock>0, і _margin: stock<=0 → -1). Нестрокове/списковe dup_of → "bad_dup_of"; id двійника, якого нема в живому каталозі → "toysi_twin_unknown";
    нерозбірливий залишок → "toysi_twin_in_stock". Ключі нормалізуються str().strip()."""
    if not isinstance(sel_item.get("twins"), list):
        return "no_twins_key"                                # fail-closed: відсутній/нескладений twins = захист від повернення двійника не діє
    tids = set()
    raw = r.get("dup_of")
    if raw not in (None, ""):
        if not isinstance(raw, str):
            return "bad_dup_of"
        tids.add(raw.strip())
    mp = dup_map.get(sid)
    if mp not in (None, ""):
        if not isinstance(mp, str):
            return "bad_dup_of"
        tids.add(mp.strip())
    tw = sel_item.get("twins") or []
    if not isinstance(tw, list):
        return "bad_dup_of"
    for x in tw:
        if not isinstance(x, str):
            return "bad_dup_of"
        tids.add(x.strip())
    tids.discard("")
    for tid in sorted(tids):
        it = catalog.get(tid)
        if it is None:
            return "toysi_twin_unknown"
        try:
            if int(it.get("stock") or 0) > 0:
                return "toysi_twin_in_stock"
        except (TypeError, ValueError):
            return "toysi_twin_in_stock"                     # нерозбірливий залишок двійника → не ризикуємо
    return ""


def _clean_xml_text(x):
    """Прибирає символи, недопустимі в XML 1.0 (керівні, сурогати, U+FFFE/U+FFFF)."""
    return _XML_BAD_RE.sub("", x) if isinstance(x, str) else x


def build_offer_fields(sid: str, r: dict, sel_item: dict, catalog: dict, dup_map: dict):
    """→ (dict полів offer, "") або (None, причина). Усі перевірки; зіпсований offer не емітуємо. Чиста функція (без мережі)."""
    import competitor_pricing as cp
    import generate_prom_feed as gp
    import google_rt_feed as grf
    import rt_supplier_export as rse
    from trademark_filter import is_uno_trademark_blocked
    if r.get("kv_dup_of"):
        return None, "kv_shadow"                              # RT-«тінь» товару KIDDISVIT (рішення власника 17.11: показуємо KIDDISVIT)
    twin = toysi_twin_block(sid, r, sel_item, catalog, dup_map)
    if twin:
        return None, twin
    raw_name = " ".join(_clean_xml_text(str(r.get("name") or "")).split())
    vendor = " ".join(_clean_xml_text(str(r.get("vendor") or "")).split())
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
    desc = _clean_xml_text(grf.scrub_description(rse.plain_text(_clean_xml_text(str(r.get("description") or "")))))
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
    texts = [f["name"], f["description"], f["vendor"], f.get("keywords_ua", ""), f.get("keywords", ""), *f["pictures"]] + [x for pn, pv in f["params"] for x in (pn, pv)]
    if any(_XML_BAD_RE.search(str(x)) for x in texts):
        return "xml_chars"                                    # недопустимі в XML символи (A6): до цього місця вони вже вичищені — якщо лишились, відхиляємо
    return ""


def load_prev_rt_ids(feed_path) -> set:
    """rt-id, які ВЖЕ у поточному prom_feed_top.xml (читати ДО перезапису файлу): при нестачі слотів лишаємо їх першими — менше churn."""
    try:
        return set(re.findall(r'<offer id="(rt-[^"]+)"', Path(feed_path).read_text(encoding="utf-8")))
    except OSError:
        return set()


def rt_hard_max() -> int:
    """Жорсткий верхній ліміт RT у фіді (активні + осиротілі): за замовчуванням RT_DEFAULT_MAX; env PROM_RT_MAX (ціле ≥0) може його змінити, але ніколи не вище RT_ABS_MAX."""
    v = os.environ.get("PROM_RT_MAX", "").strip()
    return min(int(v), RT_ABS_MAX) if v.isdigit() else RT_DEFAULT_MAX


def load_listed_status() -> tuple:
    """→ ({sid: знімок}, "ok" | "missing" | "corrupt"). Журнал усіх rt-id, КОЛИСЬ емітованих у фід, зі знімком для заглушки: {name, price, cat, vendor, pics, desc}. Записи НЕ видаляються: картка, що вже створена в Prom,
    лишається в кабінеті («Опубліковано / Немає в наявності») і займає слот; catalog_sync її ніколи не прибере (ext_id ∉ toysi_ids), тож облік слотів має це знати.
    "missing" — файлу нема (перший запуск АБО втрата), "corrupt" — є, але не читається/не того формату: обидва випадки append_rt_offers лікує з prev_ids (див. recovery)."""
    try:
        text = LISTED_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, "missing"
    except OSError:
        return {}, "corrupt"
    try:
        v = json.loads(text).get("listed")
        if not isinstance(v, dict):
            return {}, "corrupt"
        return {str(k): (x if isinstance(x, dict) else {}) for k, x in v.items()}, "ok"
    except (ValueError, AttributeError):
        return {}, "corrupt"


def load_listed() -> dict:
    return load_listed_status()[0]


DELETED_TTL_DAYS = 14           # надгробок діє 14 діб за міткою часу; після цього id може знову емітуватись (формат файлу не змінено: {id: ISO-час})


def _tomb_alive(ts, now: datetime) -> bool:
    """Надгробок діє, якщо мітка часу валідна й молодша за TTL. Невалідна мітка → діє (безпечний напрямок: id не емітуємо)."""
    try:
        return (now - datetime.fromisoformat(str(ts))).total_seconds() < DELETED_TTL_DAYS * 86400
    except ValueError:
        return True


def load_deleted_status(now: datetime | None = None) -> tuple:
    """→ ({rt-id: ISO-час видалення}, "ok" | "missing" | "corrupt"). Надгробки rt-карток, які rt_prom_cleanup.py ВИДАЛИВ у кабінеті Prom: такі id не емітуються (ні активними, ні заглушками) протягом DELETED_TTL_DAYS —
    поки вони в опублікованому фіді, Prom може «відновити» видалену картку при імпорті (так було з видаленими Toysi-SKU, див. competitor_pricing.load_delisted_pids). Прострочені надгробки відкидаються.
    Нема файлу = нічого не видалено."""
    now = now or datetime.now()
    try:
        text = DELETED_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, "missing"
    except OSError:
        return {}, "corrupt"
    try:
        v = json.loads(text).get("deleted")
        if not isinstance(v, dict):
            return {}, "corrupt"
        return {str(k): str(x) for k, x in v.items() if _tomb_alive(x, now)}, "ok"
    except (ValueError, AttributeError):
        return {}, "corrupt"


def load_deleted(now: datetime | None = None) -> dict:
    return load_deleted_status(now)[0]


def _write_deleted(cur: dict) -> bool:
    try:
        _atomic_write(DELETED_FILE, {"v": 1, "deleted": cur, "saved_at": datetime.now().isoformat(timespec="seconds")})
        return True
    except OSError:
        return False


def mark_deleted(ids, when: str | None = None, overwrite=()) -> bool:
    """Дописати надгробки (атомарно; прострочені заодно викидаються). Наявні НЕ перезаписуються, окрім id з `overwrite` (повторне видалення оживлої картки → TTL і добовий облік рахуються заново).
    False — файл пошкоджений/запис не вдався (викликач НЕ має вважати прибирання завершеним)."""
    cur, st = load_deleted_status()
    if st == "corrupt":
        return False
    when = when or datetime.now().isoformat(timespec="seconds")
    ow = {str(i) for i in overwrite}
    for i in ids:
        i = str(i)
        if i in ow:
            cur[i] = when
        else:
            cur.setdefault(i, when)
    return _write_deleted(cur)


def unmark_deleted(ids) -> int:
    """Зняти надгробки (ручна команда `rt_prom_cleanup.py --untomb <id>`). Повертає, скільки знято; -1 — файл пошкоджений/запис не вдався."""
    cur, st = load_deleted_status()
    if st == "corrupt":
        return -1
    drop = {str(i) for i in ids}
    hit = drop & set(cur)
    if not hit:
        return 0
    return len(hit) if _write_deleted({k: v for k, v in cur.items() if k not in hit}) else -1


def remove_listed(ids) -> int:
    """Прибрати id з журналу rt_prom_listed_state.json (атомарно; перечитує файл безпосередньо перед записом). Журнал відсутній/пошкоджений → нічого не пишемо (повертає -1). Інакше — скільки реально прибрано."""
    cur, st = load_listed_status()
    if st != "ok":
        return -1
    drop = {str(i) for i in ids}
    hit = drop & set(cur)
    if not hit:
        return 0
    try:
        _atomic_write(LISTED_FILE, {"v": 1, "listed": {k: v for k, v in cur.items() if k not in hit}, "saved_at": datetime.now().isoformat(timespec="seconds")})
    except OSError:
        return -1
    return len(hit)


def load_prev_rt_snapshots(feed_path, ids) -> dict:
    """{rt-id: знімок} з поточного prom_feed_top.xml (читати ДО перезапису) — лише для заданих id; джерело заглушок для осиротілих, якщо журнал втрачено. Помилка розбору → те, що встигли зібрати."""
    want = set(ids)
    out = {}
    if not want or not feed_path:
        return out
    try:
        for _ev, el in ET.iterparse(str(feed_path), events=("end",)):
            if el.tag != "offer":
                continue
            oid = el.get("id")
            if oid in want:
                out[oid] = {"name": el.findtext("name_ua") or el.findtext("name") or "", "price": el.findtext("price"), "cat": el.findtext("categoryId") or "", "vendor": el.findtext("vendor") or "",
                            "pics": [x.text for x in el.findall("picture") if x.text][:3], "desc": (el.findtext("description_ua") or "")[:1000]}
            el.clear()
    except (ET.ParseError, OSError):
        pass
    return out


def _snapshot(f: dict) -> dict:
    return {"name": f["name"], "price": f["price"], "cat": f["category"], "vendor": f["vendor"], "pics": f["pictures"][:3], "desc": f["description"][:1000]}


def _roundtrip_ok(o: ET.Element) -> bool:
    """offer має серіалізуватись у валідний UTF-8 XML і прочитатись назад (A6: невалідний символ ламав би весь файл поза try-хуком)."""
    try:
        txt = ET.tostring(o, encoding="unicode")
        txt.encode("utf-8")
        ET.fromstring(txt)
        return True
    except (ValueError, UnicodeError, ET.ParseError):
        return False


def _stub_offer(offers_el: ET.Element, sid: str, snap: dict) -> bool:
    """Осиротілий rt- (колись у фіді, зараз не активний) → мінімальний offer available="false" (як деактивація в rt_eva): Prom тримає його «Немає в наявності» у фіді, облік слотів чесний."""
    price = _num(snap.get("price"))
    name = snap.get("name") or ""
    if not price or price <= 0 or not name:
        return False
    o = ET.SubElement(offers_el, "offer", id=sid, available="false")
    ET.SubElement(o, "vendorCode").text = sid
    ET.SubElement(o, "name").text = name
    ET.SubElement(o, "name_ua").text = name
    ET.SubElement(o, "price").text = f"{price:.2f}"
    ET.SubElement(o, "currencyId").text = "UAH"
    ET.SubElement(o, "quantity_in_stock").text = "0"
    if snap.get("cat"):
        ET.SubElement(o, "categoryId").text = str(snap["cat"])
    for p in (snap.get("pics") or []):
        ET.SubElement(o, "picture").text = p
    if snap.get("vendor"):
        ET.SubElement(o, "vendor").text = snap["vendor"]
    d = snap.get("desc") or ""
    if len(d) >= 30:
        ET.SubElement(o, "description").text = d
        ET.SubElement(o, "description_ua").text = d
    if not _roundtrip_ok(o):
        offers_el.remove(o)
        return False
    return True


def append_rt_offers(root: ET.Element, catalog: dict, selection: dict, sup_items: dict, total_cap: int, prev_ids: set | None = None, save_state: bool = True, prev_snaps: dict | None = None) -> dict:
    """Дописує offer'и RT у <shop>/<offers>. root — результат generate_prom_feed._build_xml. Повертає статистику.
    ЄМНІСТЬ (A2): cap_total = min(total_cap − Toysi-offer'и, rt_hard_max()); осиротілі (колись емітовані, зараз не активні — у кабінеті Prom вони «Немає в наявності» і зайняті слоти) віддаються
    заглушками available="false" і ВІДНІМАЮТЬСЯ від ємності активних. Порядок активних при нестачі: колись у фіді → прибуток → id."""
    shop = root.find("shop")
    offers_el = shop.find("offers")
    valid = set(selection.get("valid_ids") or [])
    dup_map = load_dup_map()
    listed, lstatus = load_listed_status()
    dead = set(load_deleted())                                                  # видалені rt_prom_cleanup.py: ні активними, ні заглушками, ні в облік
    listed = {k: v for k, v in listed.items() if k not in dead}
    prev_ids = set(prev_ids or ()) - dead
    prev_snaps = prev_snaps or {}
    # ЛІКУВАННЯ втраченого/пошкодженого журналу: rt-id із ПОТОЧНОГО фіду (prev_ids) лишаються в кабінеті Prom, навіть якщо журнал їх не знає — обліковуємо їх як осиротілих і НЕ створюємо нових карток, доки журнал не відновлено
    recovery = lstatus == "corrupt" or (lstatus == "missing" and bool(prev_ids))
    toysi_n = len(offers_el)
    cap_total = max(0, min(int(total_cap) - toysi_n, rt_hard_max()))
    stats = Counter()
    ok = []
    for sid, si in sorted(selection.get("items", {}).items()):
        if si.get("cls") not in INCLUDE_CLASSES:
            continue
        if sid in dead:
            stats["skip_deleted_by_cleanup"] += 1
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
    known = set(listed) | prev_ids
    ok.sort(key=lambda f: (f["id"] not in known, -f["profit"], f["id"]))
    ok_ids = {f["id"] for f in ok}
    forced_orphans = len(known - ok_ids)
    room = max(0, cap_total - forced_orphans)
    stats["eligible"] = len(ok)
    if recovery:
        stats["recovery"] = 1
        new_cards = [f for f in ok if f["id"] not in prev_ids]
        ok = [f for f in ok if f["id"] in prev_ids]
        stats["skip_recovery_no_new_cards"] = len(new_cards)
        print(f"[Prom RT] журнал {LISTED_FILE.name} {'відсутній' if lstatus == 'missing' else 'пошкоджений'}, а в поточному фіді є {len(prev_ids)} rt- — відновлюю облік із фіду; нових карток цього прогону НЕ створюю ({len(new_cards)} відкладено)", file=sys.stderr)
    if len(ok) > room:
        stats["skip_no_free_slot"] = len(ok) - room
        ok = ok[:room]
    active = []
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
        if not _roundtrip_ok(o):
            offers_el.remove(o)
            stats["invalid_xml_roundtrip"] += 1
            continue
        active.append(f)
        stats["emitted"] += 1
    active_ids = {f["id"] for f in active}
    new_listed = dict(listed)
    for sid in prev_ids:
        if sid not in new_listed or not new_listed[sid]:
            new_listed[sid] = dict(prev_snaps.get(sid) or {})                   # відновлення журналу з поточного фіду
    for f in active:
        new_listed[f["id"]] = _snapshot(f)
    stubs = 0
    now_iso = datetime.now().isoformat(timespec="seconds")
    for sid in sorted(set(new_listed) - active_ids):
        if not (new_listed[sid] or {}).get("orphan_since"):
            new_listed[sid] = {**(new_listed[sid] or {}), "orphan_since": now_iso}   # мітка «осиротілий з» (для rt_prom_cleanup.py); новий dict — не мутуємо `listed`. Активні отримують свіжий _snapshot БЕЗ мітки → вона скидається
        snap = dict(new_listed[sid])
        r = sup_items.get(sid) or {}
        if not snap.get("name") and r.get("name"):
            snap["name"] = _clean_xml_text(str(r["name"]))                      # знімка нема — мінімум із живих даних постачальника
        if not _num(snap.get("price")) and _num(r.get("price")):
            snap["price"] = _num(r["price"])
        if _stub_offer(offers_el, sid, snap):
            stubs += 1
        else:
            stats["orphan_no_snapshot"] += 1                    # слот зайнятий, але заглушку зібрати нема з чого (запис без ціни/назви) — лише рахуємо
    n_orph = len(set(new_listed) - active_ids)
    stats["orphans"] = n_orph
    stats["orphan_stubs"] = stubs
    stats["slots_total"] = cap_total
    stats["free_slots"] = max(0, cap_total - len(active) - n_orph)
    print(f"[Prom RT] у фіді {len(active)} активних + {n_orph} осиротілих, вільних слотів {stats['free_slots']} (ємність RT {cap_total} = мін(слоти {int(total_cap)} − Toysi {toysi_n}, ліміт {rt_hard_max()}))", file=sys.stderr)
    if save_state and (new_listed != listed or lstatus == "corrupt"):
        try:
            _atomic_write(LISTED_FILE, {"v": 1, "listed": new_listed, "saved_at": datetime.now().isoformat(timespec="seconds")})
        except OSError as e:
            print(f"[Prom RT] не вдалось зберегти {LISTED_FILE.name}: {e} — облік осиротілих неповний", file=sys.stderr)
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
        listed_now = load_listed()
        need = {i for i in prev_ids if not (listed_now.get(i) or {}).get("name")}
        prev_snaps = load_prev_rt_snapshots(prev_feed_path, need) if need else {}
        st = append_rt_offers(work, catalog, sel, load_supplier(), total_cap, prev_ids, prev_snaps=prev_snaps)
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
    c = sel["stats"]["calibration"]
    print(f"[rt_prom] КАЛІБРУВАННЯ (ізольовані товари Toysi, без варіантів-сусідів wjac≥{ISOLATED_SIM}; проксі для RT): пороги {c['thresholds']}, точність на половині A {c['prec_cal_half']} (n={c['n_cal_half']}), "
          f"на ВІДКЛАДЕНІЙ половині B {c['prec_holdout']} (n={c['n_holdout']}); усього ізольованих {c['isolated_n']}; старі пороги {REF_THR}: точність B {c['old_thr_prec_holdout']} (n={c['old_thr_n_holdout']}); "
          f"для порівняння точність із сусідами-варіантами {c['prec_holdout_all_neighbours']}; причина відмови: {c['reason'] or '—'}")
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
