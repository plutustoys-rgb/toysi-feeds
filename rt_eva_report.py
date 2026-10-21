#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rt_eva_report.py — ЗВІТ (лише читання, фід EVA НЕ чіпається) для рішення «чи додавати товари RoyalToys у фід EVA»: власник 21.10.2026 — «потрібно точно відрізнити дублі і обрати правильну категорію».

ЩО РАХУЄ
 1. Дублі RT↔Toysi: клас кожного rt-товару — DUP (є в rt_dup_map: консервативний алгоритм rt_dup_finder), GRAY (бренд збігається, найближчий Toysi з схожістю ≥ GRAY_SIM, але не пройшов жорсткі перевірки/неоднозначний),
    UNIQUE_BRAND (бренд є в Toysi, схожого нема), UNIQUE (бренду RT у Toysi нема). Друга незалежна ознака — габарити упаковки (RT dims_cm ↔ Toysi «… в упаковці (см)»): її розподіл МІРЯЄТЬСЯ на відомих DUP-парах,
    потім дивимось, скільки GRAY мають габарити в тому ж коридорі (кандидати на «підвищити до DUP» вручну/після перевірки). Фото-звірка — окремий етап (потрібне завантаження зображень).
 2. Категорія EVA для rt-товару: kNN за назвою серед Toysi-товарів із ВІДОМОЮ категорією EVA (generate_eva_feed._item_category + _map_eva_category, з урахуванням remap/виключених категорій). Пороги довіри — НЕ вигадані:
    калібрую leave-one-out на вибірці самих Toysi-товарів (істина відома) і беру найм'якший вузол сітки (sim, share) з точністю ≥ TARGET_PREC. Далі агрегую за 323 категоріями RT: правило категорії, якщо ≥ MIN_AUTO товарів
    із високою довірою і ≥ AGREE_T з них голосують однаково; інше — на ручний розбір (як 51 категорія EVA 20.10).
ВИХІД: reports/rt_eva_report_<дата>.md (підсумок) + rt_eva_categories_<дата>.csv (категорія RT → пропозиція EVA, к-сть, згода) + rt_eva_gray_<дата>.csv (GRAY-пари для вибіркового перегляду). Без мережевих записів.
Запуск (VPS): venv/bin/python3 rt_eva_report.py [--sample N]"""
import argparse
import csv
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
REPORT_DIR = Path(os.environ.get("RT_EVA_REPORT_DIR", str(HERE / "reports")))
GRAY_SIM = 0.5
TARGET_PREC = 0.95
MIN_AUTO = 5
AGREE_T = 0.8
POST_MAX_DF = 400            # токени частіші за це не індексуємо для kNN (шум)
TOPK = 5


def pkg_dims_toysi(t: dict):
    p = dict(t.get("params") or [])
    try:
        v = sorted(float(str(p[k]).replace(",", ".")) for k in ("Довжина в упаковці (см)", "Ширина в упаковці (см)", "Висота в упаковці (см)"))
    except (KeyError, ValueError):
        return None
    return v if all(x > 0 for x in v) else None


def dims_rt(r: dict):
    d = r.get("dims_cm")
    try:
        v = sorted(float(x) for x in d) if d and len(d) == 3 else None
    except (TypeError, ValueError):
        return None
    return v if v and all(x > 0 for x in v) else None


def dims_gap(a, b):
    """Найбільша відносна різниця за відсортованими вимірами (0 = ідентичні)."""
    if not a or not b:
        return None
    return max(abs(x - y) / max(x, y) for x, y in zip(a, b))


def pct(vals, q):
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))] if s else None


class Knn:
    def __init__(self, m, ge):
        self.m, self.ge = m, ge
        self.items = m.tel
        self.post = defaultdict(list)
        df = Counter()
        for i, t in enumerate(self.items):
            df.update(t["_tok"])
        self.df = df
        for i, t in enumerate(self.items):
            for w in t["_tok"]:
                if df[w] <= POST_MAX_DF:
                    self.post[w].append(i)
        self.label_cache = {}

    def label(self, t):
        key = t["id"]
        if key not in self.label_cache:
            cid, cname = self.ge._item_category(t)
            if cid in self.ge.EVA_EXCLUDED_CATEGORIES:
                self.label_cache[key] = ("EXCL", "не виставляємо на EVA")
            else:
                self.label_cache[key] = self.ge._map_eva_category(cid, cname)
        return self.label_cache[key]

    def query(self, tokens, exclude_id=None):
        acc = Counter()
        for w in tokens:
            for i in self.post.get(w, ()):
                acc[i] += self.m.idf(w)
        top = []
        for i, _ in acc.most_common(40):
            t = self.items[i]
            if exclude_id is not None and t["id"] == exclude_id:
                continue
            s = self.m.wjac(tokens, t["_tok"])
            top.append((s, t))
        top.sort(key=lambda x: -x[0])
        return top[:TOPK]

    def predict(self, tokens, exclude_id=None):
        top = self.query(tokens, exclude_id)
        if not top:
            return None
        votes = Counter()
        for s, t in top:
            votes[self.label(t)[0]] += s
        win, w = votes.most_common(1)[0]
        tot = sum(votes.values())
        name = next(self.label(t)[1] for s, t in top if self.label(t)[0] == win)
        return {"eva_id": win, "eva_name": name, "share": (w / tot) if tot else 0.0, "top1": top[0][0], "n": len(top)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=3000, help="розмір вибірки Toysi для калібрування довіри (leave-one-out)")
    a = ap.parse_args(argv)
    import royaltoys_parser as rp
    from parser import fetch_toysi_catalog
    import rt_dup_finder as rd
    import generate_eva_feed as ge
    rt, ty = rp.fetch_royaltoys_catalog(), fetch_toysi_catalog()
    if not rt or len(ty) < 25_000:
        print("[rt_eva_report] каталоги порожні/усічені — зупинка", file=sys.stderr)
        return 1
    sids = rd.published_rt_ids(rt)
    dup, ev, m = rd.find_dups(rt, ty, sids)
    day = datetime.now().strftime("%Y-%m-%d")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    L = [f"# RoyalToys → EVA: звіт перетину й категорій ({day}, лише читання, фід EVA не змінено)", "",
         f"RT-товарів у вітрині (published): **{len(sids)}**; Toysi (eligible): **{len(m.tel)}**.", ""]

    # ── 1. дублі ──
    cls = {}
    gray_rows = []
    for sid in sids:
        if sid in dup:
            cls[sid] = "DUP"
            continue
        e = ev.get(sid)
        if e is None:
            cls[sid] = "UNIQUE"
            continue
        best = e["cands"][0] if e["cands"] else None
        if best and (best["sim"] >= GRAY_SIM or any(c["art"] for c in e["cands"])):
            cls[sid] = "GRAY"
            r = rt[sid[3:]]
            why = []
            if not best["num_ok"]:
                why.append("числа")
            if not best["var_ok"]:
                why.append("варіант")
            if best["col"] not in ("equal", "both_none"):
                why.append("колір:" + best["col"])
            if sum(1 for c in e["cands"] if c["sim"] >= GRAY_SIM) > 1:
                why.append(">1 кандидата")
            if best["tn"] < rd.MIN_TOKENS_T:
                why.append("коротка назва Toysi")
            if not why:
                why.append("нижче порогу/неоднозначно")
            t = ty[best["tid"]]
            gap = dims_gap(dims_rt(r), pkg_dims_toysi(t))
            gray_rows.append((sid, (r.get("name_ua") or r["name"])[:70], best["tid"], t["name"][:70], round(best["sim"], 2), "+".join(why), "" if gap is None else round(gap, 2)))
        else:
            cls[sid] = "UNIQUE_BRAND"
    cnt = Counter(cls.values())
    L += ["## 1. Дублі (RT↔Toysi)", "", "| клас | к-сть | частка |", "|---|---|---|"]
    for k in ("DUP", "GRAY", "UNIQUE_BRAND", "UNIQUE"):
        L.append(f"| {k} | {cnt.get(k, 0)} | {cnt.get(k, 0) / len(sids) * 100:.1f}% |")
    L += ["", "DUP — алгоритм rt_dup_finder (бренд+назва+артикул+числа+колір, консервативно). GRAY — близький кандидат, що не пройшов перевірку/неоднозначний: НЕ додавати в EVA без рішення. UNIQUE_BRAND — бренд є в Toysi, схожого товару нема. UNIQUE — бренду RT у Toysi нема.", ""]

    # друга ознака: габарити упаковки
    dgaps = []
    nodims = 0
    for sid, v in dup.items():
        g = dims_gap(dims_rt(rt[sid[3:]]), pkg_dims_toysi(ty[v[0]]))
        if g is None:
            nodims += 1
        else:
            dgaps.append(g)
    L += ["### Друга ознака — габарити упаковки (виміряно на відомих DUP-парах)", ""]
    if dgaps:
        L.append(f"DUP-пар із габаритами з обох боків: **{len(dgaps)}** з {len(dup)} (без габаритів {nodims}). Різниця за відсортованими вимірами: p50 {pct(dgaps, .5):.2f}, p75 {pct(dgaps, .75):.2f}, p90 {pct(dgaps, .9):.2f}, p95 {pct(dgaps, .95):.2f}, max {max(dgaps):.2f}.")
        corr = pct(dgaps, 0.9)
        in_c = [r for r in gray_rows if r[6] != "" and r[6] <= corr]
        no_d = [r for r in gray_rows if r[6] == ""]
        L.append(f"GRAY із габаритами в коридорі ≤ p90 DUP ({corr:.2f}): **{len(in_c)}** з {len(gray_rows)} (без габаритів {len(no_d)}) — це кандидати «підвищити до DUP» після вибіркового перегляду очима.")
    else:
        L.append("Габаритів на DUP-парах не вистачає для калібрування.")
    with open(REPORT_DIR / f"rt_eva_gray_{day}.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rt_id", "rt_name", "toysi_id", "toysi_name", "sim", "чому_не_DUP", "розбіжність_габаритів"])
        w.writerows(gray_rows)
    L.append("")

    # ── 2. категорії ──
    knn = Knn(m, ge)
    rnd = random.Random(20261021)
    pool = [t for t in m.tel if knn.label(t)[0] != "EXCL"]
    sample = rnd.sample(pool, min(a.sample, len(pool)))
    loo = []
    for t in sample:
        p = knn.predict(t["_tok"], exclude_id=t["id"])
        if p:
            loo.append((p, p["eva_id"] == knn.label(t)[0]))
    L += ["## 2. Категорія EVA (kNN за назвою, калібрування leave-one-out на Toysi)", "",
          f"Вибірка Toysi (відома категорія): **{len(loo)}**. Точність за порогами (top1 — схожість найближчого, share — частка ваги переможної категорії серед {TOPK} сусідів):", "",
          "| top1 ≥ | share ≥ | охоплення | точність |", "|---|---|---|---|"]
    grid = [(x, y) for x in (0.3, 0.4, 0.5, 0.6, 0.7) for y in (0.6, 0.8, 1.0)]
    chosen = None
    for x, y in grid:
        sel = [ok for p, ok in loo if p["top1"] >= x and p["share"] >= y]
        if not sel:
            continue
        prec = sum(sel) / len(sel)
        L.append(f"| {x} | {y} | {len(sel) / len(loo) * 100:.0f}% | {prec * 100:.1f}% |")
        if prec >= TARGET_PREC and (chosen is None or len(sel) > chosen[2]):
            chosen = (x, y, len(sel), prec)
    if not chosen:
        L += ["", f"**Жоден вузол сітки не досяг точності {TARGET_PREC:.0%}** — автоматичний вибір категорії небезпечний; лише ручна таблиця категорій."]
        thr = None
    else:
        thr = (chosen[0], chosen[1])
        L += ["", f"Обрано найм'якший вузол із точністю ≥ {TARGET_PREC:.0%}: top1 ≥ {thr[0]}, share ≥ {thr[1]} (охоплення {chosen[2] / len(loo) * 100:.0f}%, точність {chosen[3] * 100:.1f}%)."]

    per_cat = defaultdict(list)
    n_auto = 0
    n_excl = 0
    for sid in sids:
        if cls[sid] in ("DUP",):
            continue
        r = rt[sid[3:]]
        tokens = rd.tokens_for_sim(r.get("name_ua") or r["name"], set(rd.normalize_name(r.get("vendor") or "")))
        p = knn.predict(tokens)
        high = bool(p and thr and p["top1"] >= thr[0] and p["share"] >= thr[1])
        n_auto += high
        if high and p["eva_id"] == "EXCL":
            n_excl += 1
        per_cat[(r.get("category_name") or "?").strip()].append((sid, p, high))
    total_nd = sum(len(v) for v in per_cat.values())
    L += ["", f"Товарів RT без DUP: **{total_nd}**. З високою довірою категорії (за обраним порогом): **{n_auto}** ({n_auto / max(total_nd, 1) * 100:.0f}%), з них «не виставляємо на EVA» (виключені категорії Toysi): {n_excl}.", ""]
    rows = []
    cat_auto = cat_manual = items_in_auto_cats = 0
    for cname, lst in sorted(per_cat.items(), key=lambda kv: -len(kv[1])):
        hv = [p["eva_id"] for _, p, high in lst if high]
        dom, share, name = "", 0.0, ""
        if hv:
            dom, c = Counter(hv).most_common(1)[0]
            share = c / len(hv)
            name = next(p["eva_name"] for _, p, high in lst if high and p["eva_id"] == dom)
        auto = len(hv) >= MIN_AUTO and share >= AGREE_T
        cat_auto += auto
        cat_manual += (not auto)
        items_in_auto_cats += len(lst) if auto else 0
        rows.append((cname, len(lst), len(hv), dom, name, round(share, 2), "авто" if auto else "ручна"))
    with open(REPORT_DIR / f"rt_eva_categories_{day}.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["категорія_RT", "товарів_без_DUP", "з_високою_довірою", "EVA_id", "EVA_назва", "згода", "рішення"])
        w.writerows(rows)
    L += [f"Категорій RT: **{len(rows)}**; правило «авто» (≥{MIN_AUTO} довірених товарів, згода ≥{AGREE_T:.0%}): **{cat_auto}**, на ручний розбір: **{cat_manual}**. Товарів у «авто»-категоріях: **{items_in_auto_cats}** ({items_in_auto_cats / max(total_nd, 1) * 100:.0f}%).", "",
          "Топ-15 категорій RT за розміром:", "", "| категорія RT | товарів | довірених | EVA | згода | рішення |", "|---|---|---|---|---|---|"]
    for r in rows[:15]:
        L.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[4] or '—'} ({r[3] or '—'}) | {r[5]} | {r[6]} |")
    # стартовий набір: UNIQUE (бренду RT у Toysi нема → дубль неможливий за побудовою) × категорія
    auto_cat = {r[0] for r in rows if r[6] == "авто"}
    uniq = [(sid, p, high) for lst in per_cat.values() for (sid, p, high) in lst if cls[sid] == "UNIQUE"]
    u_cats = Counter((rt[sid[3:]].get("category_name") or "?").strip() for sid, _, _ in uniq)
    u_auto_items = sum(1 for sid, p, h in uniq if (rt[sid[3:]].get("category_name") or "?").strip() in auto_cat)
    u_high = sum(1 for _, _, h in uniq if h)
    L += ["", "### Стартовий набір: UNIQUE (бренду RT у Toysi нема — дубль неможливий за побудовою)", "",
          f"Товарів UNIQUE: **{len(uniq)}** у **{len(u_cats)}** категоріях RT. З високою довірою категорії (поштучно): **{u_high}**; у категоріях із правилом «авто»: **{u_auto_items}**.", ""]
    cum, tot_all = 0, sum(u_cats.values())
    for n_top in (10, 20, 40, 80):
        cum = sum(c for _, c in u_cats.most_common(n_top))
        L.append(f"- перші {n_top} категорій RT покривають {cum} із {tot_all} UNIQUE-товарів ({cum / max(tot_all, 1) * 100:.0f}%)")
    cum_all = Counter()
    for cname, lst in per_cat.items():
        cum_all[cname] = len(lst)
    tot_nd2 = sum(cum_all.values())
    L += ["", "Покриття ручною таблицею (усі RT без DUP): " + "; ".join(f"топ-{n} категорій → {sum(c for _, c in cum_all.most_common(n)) / max(tot_nd2, 1) * 100:.0f}%" for n in (20, 50, 100, 150)), ""]
    # скільки в UNIQUE_BRAND «майже схожих» — оцінка ризику пропущених дублів
    near = [e["best_any"] for sid, e in ev.items() if cls.get(sid) == "UNIQUE_BRAND"]
    if near:
        L += [f"UNIQUE_BRAND: найвища схожість до Toysi того ж бренду — p50 {pct(near, .5):.2f}, p90 {pct(near, .9):.2f}; із схожістю 0.35–0.5: **{sum(1 for x in near if 0.35 <= x < 0.5)}** (кандидати на пропущені дублі, перегляд вибірково), ≥0.5 за визначенням у GRAY.", ""]
    L += ["", "Файли: `rt_eva_categories_*.csv` (категорія → пропозиція), `rt_eva_gray_*.csv` (GRAY-пари). Фід EVA цим звітом НЕ змінюється; пілот і заміри в кабінеті — окремий крок за рішенням власника."]
    out = REPORT_DIR / f"rt_eva_report_{day}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"[rt_eva_report] записано {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
