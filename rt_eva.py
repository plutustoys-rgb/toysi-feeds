#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rt_eva.py — товари RoyalToys у EVA-фіді (рішення власника 21.10.2026: «додаємо унікальні і сумнівні, тільки так щоб фід не відхилився»).

ДВА ШАРИ
 1. ВІДБІР (build_selection / ensure_selection → rt_eva_selection.json): для кожного rt-товару — клас (DUP / GRAY / UNIQUE_BRAND / UNIQUE за rt_dup_finder) і категорія EVA.
    У фід ідуть UNIQUE, UNIQUE_BRAND і GRAY (власник: «унікальні і сумнівні»); DUP (точні дублі товару Toysi) — НІ (Toysi уже у фіді).
    Категорія — ЛИШЕ валідний листок EVA (eva_category_reference.csv ∪ id з eva_category_map.json), спосіб (за пріоритетом): (1) ручна таблиця rt_eva_category_overrides.json {категорія RT: {eva_id, eva_name} | null=виключити};
    (2) правило категорії RT (≥MIN_AUTO товарів із високою довірою і ≥AGREE_T згоди; kNN за назвою серед Toysi із відомою категорією EVA; ПОРОГИ ДОВІРИ — калібровані leave-one-out на Toysi, точність ≥TARGET_PREC) з вето по товару
    (високодовірений kNN-висновок про ІНШУ категорію → товар НЕ беремо); (3) високодовірений kNN по самому товару. Нема впевненості → товару у фіді НЕМАЄ (не вгадуємо). Категорії, що EVA не виставляємо (виключені Toysi-категорії) — виключено.
 2. ЕМІСІЯ (append_rt_offers): окремий від Toysi-циклу шлях (ціна RT = supplier_rt.price, а не формули Toysi; Toysi-функції собівартості/знижки НЕ застосовуються), ті самі санітайзери й фільтри EVA (_clean_text/_denoise_caps/…,
    стоп-бренди, заборонені слова, forbidden_products, фото https, опис ≥30 симв., ≥2 param, розміри), КОЖЕН offer проходить перевірку обов'язкових полів — зіпсований НЕ емітиться (щоб не відхилили фід/картку). Ідентифікатор offer = «rt-<id>»
    (так само його бачить конвеєр замовлень: toysi_code починається з «rt-»). Деактивація: колись листовані rt-id, що випали (OOS/виключені), — мінімальні offer'и available=false (стан rt_eva_listed_state.json).
Брама: EVA_RT_IN_FEED=1 у .env (власник); без неї — нічого не міняється. Застарілий відбір (старший за STALE_H) → RT у цьому прогоні пропускається (fail-safe), Toysi-частина фіду не залежить."""
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
SEL_FILE = Path(os.environ.get("RT_EVA_SELECTION_FILE", str(HERE / "rt_eva_selection.json")))
OVERRIDES_FILE = HERE / "rt_eva_category_overrides.json"
LISTED_FILE = Path(os.environ.get("RT_EVA_LISTED_FILE", str(HERE / "rt_eva_listed_state.json")))
SUPPLIER_FILE = HERE / "supplier_rt.json"
CATEGORY_REF = HERE / "eva_category_reference.csv"
STALE_H = 36
GRAY_SIM = 0.5
TARGET_PREC = 0.95
MIN_AUTO = 5
AGREE_T = 0.8
POST_MAX_DF = 400
TOPK = 5
INCLUDE_CLASSES = ("UNIQUE", "UNIQUE_BRAND", "GRAY")      # власник 21.10: унікальні і сумнівні; DUP — ні
RT_MIN_STOCK = 2                                            # RT у фіді лише з залишком ≥2 (та сама семантика «>1», що з 19.10 у Toysi; власна константа — аудит #939 B1)
MIN_MARKUP = 1.3                                            # санітарна перевірка ціни: price >= cost × 1.3 (supplier_rt: ×1.5)


def gate_on() -> bool:
    return os.environ.get("EVA_RT_IN_FEED") == "1"


def valid_eva_ids() -> set:
    """Листки EVA, дозволені у фіді: довідник (колонка БТК_id) ∪ id із eva_category_map.json (уже співставлені)."""
    ids = set()
    try:
        with open(CATEGORY_REF, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        for r in rows[1:]:
            if len(r) > 2 and r[2].strip().isdigit():
                ids.add(r[2].strip())
    except OSError:
        pass
    try:
        m = json.loads((HERE / "eva_category_map.json").read_text(encoding="utf-8"))
        ids |= {str(v.get("id")) for v in m.values() if isinstance(v, dict) and str(v.get("id") or "").isdigit()}
    except (OSError, ValueError):
        pass
    return ids


def load_overrides() -> dict:
    try:
        d = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


# ───────────────────────────── відбір ─────────────────────────────
class Knn:
    def __init__(self, m, ge):
        self.m, self.ge = m, ge
        self.items = m.tel
        df = Counter()
        for t in self.items:
            df.update(t["_tok"])
        self.post = defaultdict(list)
        for i, t in enumerate(self.items):
            for w in t["_tok"]:
                if df[w] <= POST_MAX_DF:
                    self.post[w].append(i)
        self.cache = {}

    def safe(self, t) -> bool:
        """Категорія Toysi, чий фінальний id EVA ВЖЕ співставила в кабінеті: legacy-повернення (EVA_LEGACY_OVERRIDE) або ціль remap-розкатки. EVA-id із довідника, яких там нема, можуть бути «не співставлені» (інцидент 03.08/17.10)."""
        cid, _ = self.ge._item_category(t)
        return bool(cid and ((cid in self.ge.EVA_LEGACY_OVERRIDE and self.ge.EVA_CATEGORY_MAP.get(cid)) or cid in self.ge.EVA_REMAP_OVERRIDE))

    def label(self, t):
        k = t["id"]
        if k not in self.cache:
            cid, cname = self.ge._item_category(t)
            if cid in self.ge.EVA_EXCLUDED_CATEGORIES:
                self.cache[k] = ("EXCL", "не виставляємо на EVA")
            else:
                self.cache[k] = self.ge._map_eva_category(cid, cname)
        return self.cache[k]

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
            votes[self.label(t)[0]] += s
        win, w = votes.most_common(1)[0]
        tot = sum(votes.values())
        name = next(self.label(t)[1] for s, t in top if self.label(t)[0] == win)
        return {"eva_id": win, "eva_name": name, "share": (w / tot) if tot else 0.0, "top1": top[0][0]}


def calibrate(knn, rnd, n=2000):
    """Найм'якший вузол сітки (top1, share) з точністю ≥ TARGET_PREC на Toysi (leave-one-out). None — жоден не досяг."""
    pool = [t for t in knn.items if knn.label(t)[0] != "EXCL"]
    sample = rnd.sample(pool, min(n, len(pool)))
    loo = []
    for t in sample:
        p = knn.predict(t["_tok"], exclude_id=t["id"])
        if p:
            loo.append((p, p["eva_id"] == knn.label(t)[0]))
    best = None
    for x in (0.3, 0.4, 0.5, 0.6, 0.7):
        for y in (0.6, 0.8, 1.0):
            sel = [ok for p, ok in loo if p["top1"] >= x and p["share"] >= y]
            if len(sel) < 50:
                continue
            prec = sum(sel) / len(sel)
            if prec >= TARGET_PREC and (best is None or len(sel) > best[2]):
                best = (x, y, len(sel), prec)
    return best


def classify_dups(rt, ty, sids, rd):
    dup, ev, m = rd.find_dups(rt, ty, sids)
    cls = {}
    for sid in sids:
        if sid in dup:
            cls[sid] = "DUP"
            continue
        e = ev.get(sid)
        if e is None:
            cls[sid] = "UNIQUE"
        elif e["cands"] and (e["cands"][0]["sim"] >= GRAY_SIM or any(c["art"] for c in e["cands"])):
            cls[sid] = "GRAY"
        else:
            cls[sid] = "UNIQUE_BRAND"
    return cls, m


def build_selection(ty: dict, sup_items: dict, ge) -> dict:
    """Повний відбір (важкий: Matcher по Toysi+RT і kNN; ~1–3 хв). sup_items — supplier_rt.json["items"] {rt-<id>: item}."""
    import rt_dup_finder as rd
    rt = {sid[3:]: v for sid, v in sup_items.items()}
    sids = list(sup_items)
    cls, m = classify_dups(rt, ty, sids, rd)
    knn = Knn(m, ge)
    thr = calibrate(knn, random.Random(20261021))
    known = {knn.label(t)[0] for t in knn.items if knn.safe(t)} - {"EXCL"}       # id категорій, які поточний Toysi-фід УЖЕ віддає EVA (відомо-прийняті: співставлені + legacy у кабінеті) — єдина безпечна множина для авто-вибору
    overrides = load_overrides()
    ref_ids = valid_eva_ids()
    valid = known
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
        hv = [preds[s][0]["eva_id"] for s in lst if preds[s][1]]
        if not hv:
            continue
        dom, c = Counter(hv).most_common(1)[0]
        if len(hv) >= MIN_AUTO and c / len(hv) >= AGREE_T and dom != "EXCL" and dom in valid:
            name = next(preds[s][0]["eva_name"] for s in lst if preds[s][1] and preds[s][0]["eva_id"] == dom)
            rules[cname] = {"eva_id": dom, "eva_name": name, "agree": round(c / len(hv), 2), "n_high": len(hv)}
    items = {}
    drop = Counter()
    for cname, lst in per_cat.items():
        ov = overrides.get(cname, "__none__")
        for sid in lst:
            p, high = preds[sid]
            eva = None
            if ov is None:
                drop["override_exclude"] += 1
                continue
            if isinstance(ov, dict) and str(ov.get("eva_id")) in (known | ref_ids):          # ручне рішення власника: довідник EVA теж дозволено
                eva, src = (str(ov["eva_id"]), ov.get("eva_name") or ""), "override"
            elif cname in rules:
                if high and p["eva_id"] != rules[cname]["eva_id"]:
                    drop["veto"] += 1                       # товар упевнено «іншої» категорії, ніж правило його категорії RT
                    continue
                eva, src = (rules[cname]["eva_id"], rules[cname]["eva_name"]), "rule"
            elif high and p["eva_id"] != "EXCL" and p["eva_id"] in valid:
                eva, src = (p["eva_id"], p["eva_name"]), "item"
            if not eva:
                drop["no_category"] += 1
                continue
            items[sid] = {"eva_id": eva[0], "eva_name": eva[1], "cls": cls[sid], "src": src}
    # огляд для власника: категорії RT без правила/ручного рішення — найкраща здогадка kNN (БЕЗ порогу довіри) + приклади назв; рішення власника кладемо в rt_eva_category_overrides.json
    review = []
    for cname, lst in sorted(per_cat.items(), key=lambda kv: -len(kv[1])):
        if cname in rules or cname in overrides:
            continue
        allv = [preds[s][0] for s in lst if preds[s][0]]
        votes = Counter(p["eva_id"] for p in allv)
        guess, gname, share = "", "", 0.0
        if votes:
            guess, gc = votes.most_common(1)[0]
            share = gc / len(allv)
            gname = next(p["eva_name"] for p in allv if p["eva_id"] == guess)
        review.append({"rt_category": cname, "items": len(lst), "guess_eva_id": guess, "guess_eva_name": gname, "guess_share": round(share, 2), "guess_valid": guess in valid,
                       "examples": [sup_items[s]["name"][:60] for s in lst[:3]]})
    stats = {"rt_total": len(sids), "classes": dict(Counter(cls.values())), "in_feed_candidates": len(items), "by_src": dict(Counter(v["src"] for v in items.values())),
             "dropped": dict(drop), "rules": len(rules), "thresholds": {"top1": thr[0], "share": thr[1], "precision": round(thr[3], 3)} if thr else None}
    ok_ids = sorted(known | {v["eva_id"] for v in items.values() if v["src"] == "override"})
    return {"built_at": datetime.now().isoformat(timespec="seconds"), "toysi_n": len(ty), "rt_n": len(sids), "items": items, "rules": rules, "review": review, "valid_ids": ok_ids, "dup_ids": sorted(s for s, c in cls.items() if c == "DUP"), "stats": stats}


def _atomic_write(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")      # унікальне ім'я: погодинний і 6-годинний цикли можуть писати одночасно
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


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


MIN_KEEP_RATIO = 0.7        # новий відбір не приймаємо, якщо кандидатів < 70% попереднього (усічений каталог/збій) — лишаємо попередній


def ensure_selection(ty: dict, ge, rebuild_after_h: float = 12.0):
    """Відбір для фіду: свіжий кеш (< rebuild_after_h) → його; інакше перебудова. Гарди (аудит #939 B4): каталог Toysi усічений (<25 000) → перебудови немає (лишаємо свіжий старий або None);
    новий відбір суттєво менший за попередній (<MIN_KEEP_RATIO) → відкидаємо; збій → старий, якщо ще не застарів."""
    sup = load_supplier()
    if not sup:
        return None
    sel = load_selection()
    fresh_old = sel if sel and selection_fresh(sel) else None
    if fresh_old and fresh_old.get("rt_n", 0) > 0 and (datetime.now() - datetime.fromisoformat(fresh_old["built_at"])) <= timedelta(hours=rebuild_after_h):
        return fresh_old
    if len(ty) < 25_000:
        print(f"[rt_eva] каталог Toysi усічений ({len(ty)}) — відбір не перебудовую", file=sys.stderr)
        return fresh_old
    try:
        new = build_selection(ty, sup, ge)
    except Exception as e:  # noqa: BLE001
        print(f"[rt_eva] перебудова відбору не вдалась ({type(e).__name__}: {str(e)[:120]}) — {'лишаємо попередній' if fresh_old else 'RT у фід цього прогону НЕ йдуть'}", file=sys.stderr)
        return fresh_old
    prev_n = (sel or {}).get("stats", {}).get("in_feed_candidates") or 0
    if fresh_old and prev_n and new["stats"]["in_feed_candidates"] < prev_n * MIN_KEEP_RATIO:
        print(f"[rt_eva] новий відбір підозріло менший ({new['stats']['in_feed_candidates']} проти {prev_n}) — відкинуто, лишаємо попередній", file=sys.stderr)
        return fresh_old
    _atomic_write(SEL_FILE, new)
    return new


def supplier_fresh(max_h: float = 6.0) -> bool:
    """supplier_rt.json оновлювався не раніше max_h годин тому (поле updated): застарілі залишки/ціни RT у фід не віддаємо."""
    try:
        d = json.loads(SUPPLIER_FILE.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(str(d.get("updated")).replace("Z", "+00:00"))
        now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.now()
        return now - ts <= timedelta(hours=max_h)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def load_supplier() -> dict:
    try:
        d = json.loads(SUPPLIER_FILE.read_text(encoding="utf-8"))
        items = d.get("items")
        return items if isinstance(items, dict) and items else {}
    except (OSError, ValueError, AttributeError):
        return {}


# ───────────────────────────── емісія ─────────────────────────────
def _num(x):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _pictures(r: dict, max_n: int) -> list:
    pics = r.get("images")
    if isinstance(pics, str):                                   # supplier_rt зберігає список як рядок у деяких вивантаженнях
        try:
            pics = json.loads(pics.replace("'", '"'))
        except ValueError:
            pics = []
    return [p for p in (pics or []) if isinstance(p, str) and p.startswith("https://")][:max_n]


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


def build_offer_fields(sid: str, r: dict, sel_item: dict, ge, forbidden_fn):
    """→ (dict полів offer) або (None, причина). Тут ВСІ перевірки обов'язкових полів EVA — зіпсований offer не емітимо."""
    vendor = (r.get("vendor") or "").strip()
    if not vendor:
        return None, "no_vendor"
    if ge._is_stop_brand(vendor):
        return None, "stop_brand"
    raw_name = r.get("name") or ""
    if ge._name_has_forbidden_word(raw_name):
        return None, "forbidden_word"
    if forbidden_fn({"name": raw_name, "vendor": vendor, "category_name": r.get("category") or ""}):
        return None, "forbidden_policy"
    stock = _num(r.get("stock"))
    cost, price = _num(r.get("cost")), _num(r.get("price"))
    if stock is None or stock < RT_MIN_STOCK:
        return None, "stock"
    if not cost or cost <= 0 or cost < ge.MIN_SUPPLIER_PRICE or not price or price < cost * MIN_MARKUP:
        return None, "price"
    pics = _pictures(r, ge.EVA_MAX_PICTURES)
    if not pics:
        return None, "no_pictures"
    name = ge._normalize_trailing_color_case(ge._limit_punctuation(ge._denoise_caps(ge._clean_text(raw_name))))
    if len(name) < 3:
        return None, "name"
    desc = ge._truncate(ge._clean_text(ge._sanitize_eva_description(ge._strip_urls(r.get("description") or ""))), ge.EVA_DESCRIPTION_MAX_LEN)
    if len(desc) < ge.EVA_DESCRIPTION_MIN_LEN:
        return None, "short_desc"
    params = _dims_params(r)
    if not any(pn.startswith("Розміри") for pn, _ in params):
        return None, "no_dims"                                  # EVA: розміри товару обов'язкові (в назві/описі/фото/параметрах); без габаритів — не емітуємо (аудит #939 N7)
    for fn, fv in (("Виробник", vendor), ("Категорія", sel_item.get("eva_name") or r.get("category") or "Дитячі товари")):
        if len(params) >= 2:
            break
        params.append((fn, fv))
    if len(params) < 2:
        return None, "params"
    return {"id": sid, "name": name, "price": round(price), "stock": int(stock), "category": sel_item["eva_id"], "pictures": pics, "vendor": ge._clean_text(ge.normalize_vendor(vendor)),
            "description": desc, "params": params}, ""


def validate_offer(f: dict, valid_ids: set, name_max: int) -> str:
    """'' = ок, інакше причина. Остання лінія оборони перед записом у XML."""
    if not re.fullmatch(r"rt-[0-9A-Za-z_-]{1,40}", f["id"]):
        return "id"
    if not f["name"] or len(f["name"]) > name_max:
        return "name"
    if f["price"] <= 0 or f["stock"] <= 0:
        return "price/stock"
    if f["category"] not in valid_ids:
        return "category"
    if not f["pictures"] or not all(p.startswith("https://") and len(p) <= 1999 for p in f["pictures"]):
        return "pictures"
    if not f["vendor"] or len(f["description"]) < 30 or len(f["params"]) < 2:
        return "fields"
    return ""


def _snapshot(f: dict, sel_item: dict) -> dict:
    return {"cat": f["category"], "catname": sel_item.get("eva_name") or "", "name": f["name"], "price": f["price"], "vendor": f["vendor"], "pics": f["pictures"][:3], "desc": f["description"][:500]}


def _load_listed() -> dict:
    """Стан «колись листовані» {sid: знімок}: категорія (липка), назва, ціна, бренд, фото, початок опису — щоб (а) заглушка деактивації вийшла повною навіть коли id зник із supplier_rt (select_ids прибирає товари з малим залишком),
    (б) категорія вже листованого товару НЕ мінялась між прогонами (EVA: categoryId після додавання не міняти — повторна модерація). Старий формат (список id) → знімки порожні."""
    try:
        d = json.loads(LISTED_FILE.read_text(encoding="utf-8"))
        v = d.get("listed")
        if isinstance(v, dict):
            return {str(k): (x if isinstance(x, dict) else {}) for k, x in v.items()}
        if isinstance(v, list):
            return {str(k): {} for k in v}
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def append_rt_offers(root: ET.Element, ge, selection: dict, sup_items: dict) -> dict:
    """Дописує у <shop>: категорії (якщо ще нема) і offer'и RT. Повертає статистику. root — результат ge._build_xml (до indent/CDATA).
    СТАБІЛЬНІСТЬ (аудит #939 B3): уже листований товар (є у rt_eva_listed_state.json) тримає ТУ САМУ категорію і лишається у фіді, поки проходить фільтри/залишок і не став DUP — дрібні коливання kNN/порогів відбору його не вибивають
    і категорію не міняють; нові товари додаються за відбором."""
    from forbidden_products import is_forbidden
    shop = root.find("shop")
    cats_el, offers_el = shop.find("categories"), shop.find("offers")
    have_cats = {c.get("id") for c in cats_el}
    valid = set(selection.get("valid_ids") or []) | have_cats           # лише відомо-прийняті EVA категорії (+ ручні рішення власника); id поза цим → offer не емітується
    toysi_names = {ge._dedup_key(o.findtext("name_ua") or "") for o in offers_el}
    stats = Counter()
    prev = _load_listed()
    dup_ids = set(selection.get("dup_ids") or [])
    overrides = load_overrides()
    excluded_cats = {k for k, v in overrides.items() if v is None}
    items = selection.get("items", {})
    cand = {}
    for sid, si in items.items():
        if si.get("cls") in INCLUDE_CLASSES:
            cand[sid] = dict(si)
    for sid, snap in prev.items():                                         # гістерезис: колись листований лишається кандидатом (крім DUP)
        if sid not in cand and sid not in dup_ids and snap.get("cat"):
            cand[sid] = {"eva_id": snap["cat"], "eva_name": snap.get("catname") or "", "cls": "HOLD", "src": "sticky"}
    for sid, si in cand.items():                                           # липка категорія
        sc = (prev.get(sid) or {}).get("cat")
        if sc and sc in valid:
            si["eva_id"], si["eva_name"] = sc, (prev[sid].get("catname") or si.get("eva_name") or "")
    active = {}
    for sid, sel_item in cand.items():
        r = sup_items.get(sid)
        if not r:
            stats["skip_not_in_supplier"] += 1
            continue
        if (r.get("category") or "").strip() in excluded_cats:
            stats["skip_override_exclude"] += 1
            continue
        f, why = build_offer_fields(sid, r, sel_item, ge, is_forbidden)
        if f is None:
            stats["skip_" + why] += 1
            continue
        bad = validate_offer(f, valid, ge.EVA_NAME_MAX_LEN)
        if bad:
            stats["invalid_" + bad] += 1
            continue
        active[sid] = (f, sel_item)
    # унікальні назви (EVA: повторювані назви — зауваження модерації): збіг з Toysi-офером чи між RT → дописуємо артикул
    seen = set(toysi_names)
    for sid, (f, sel_item) in sorted(active.items()):
        key = ge._dedup_key(f["name"])
        if key in seen:
            r_code = (sup_items[sid].get("vendor_code") or "").strip()
            suffix = f" ({r_code})" if r_code else f" ({sid})"
            f["name"] = ge._truncate(f["name"], ge.EVA_NAME_MAX_LEN - len(suffix)) + suffix
            key = ge._dedup_key(f["name"])
            stats["renamed_dup"] += 1
        seen.add(key)
    new_prev = dict(prev)
    for sid, (f, sel_item) in sorted(active.items()):
        if f["category"] not in have_cats:
            ET.SubElement(cats_el, "category", id=f["category"]).text = ge._clean_text(sel_item.get("eva_name") or f["category"])
            have_cats.add(f["category"])
        o = ET.SubElement(offers_el, "offer", id=sid, available="true")
        ET.SubElement(o, "name_ua").text = f["name"]
        ET.SubElement(o, "price").text = f"{f['price']:.2f}"
        ET.SubElement(o, "currencyId").text = "UAH"
        ET.SubElement(o, "stock_quantity").text = str(f["stock"])
        ET.SubElement(o, "categoryId").text = f["category"]
        for p in f["pictures"]:
            ET.SubElement(o, "picture").text = p
        ET.SubElement(o, "vendor").text = f["vendor"]
        ET.SubElement(o, "description_ua").text = f["description"]
        for pn, pv in f["params"]:
            ET.SubElement(o, "param", name=ge._clean_text(pn)).text = ge._clean_text(str(pv))
        new_prev[sid] = _snapshot(f, sel_item)
        stats["emitted"] += 1
    # деактивація: колись листовані, зараз не активні. Повна заглушка (як у Toysi-частини: бренд, фото, опис, категорія) із живих даних або зі знімка — навіть коли id зник із supplier_rt (товар із малим залишком туди не потрапляє)
    for sid in sorted(set(new_prev) - set(active)):
        snap = new_prev.get(sid) or {}
        r = sup_items.get(sid) or {}
        price = _num(r.get("price")) or _num(snap.get("price"))
        name = r.get("name") or snap.get("name") or ""
        if not price or price <= 0 or not name:
            continue
        o = ET.SubElement(offers_el, "offer", id=sid, available="false")
        ET.SubElement(o, "name_ua").text = ge._truncate(ge._normalize_trailing_color_case(ge._limit_punctuation(ge._denoise_caps(ge._clean_text(name)))), ge.EVA_NAME_MAX_LEN) or f"Товар {sid}"
        ET.SubElement(o, "price").text = f"{round(price):.2f}"
        ET.SubElement(o, "currencyId").text = "UAH"
        ET.SubElement(o, "stock_quantity").text = "0"
        cat = snap.get("cat") or (cand.get(sid) or {}).get("eva_id")
        if cat and cat in valid:
            ET.SubElement(o, "categoryId").text = cat
            if cat not in have_cats:
                ET.SubElement(cats_el, "category", id=cat).text = ge._clean_text(snap.get("catname") or (cand.get(sid) or {}).get("eva_name") or cat)
                have_cats.add(cat)
        pics = _pictures(r, ge.EVA_MAX_PICTURES) if r else []
        for p in (pics or snap.get("pics") or []):
            ET.SubElement(o, "picture").text = p
        vend = (r.get("vendor") or snap.get("vendor") or "").strip()
        if vend:
            ET.SubElement(o, "vendor").text = ge._clean_text(ge.normalize_vendor(vend)) if r else vend
        d = snap.get("desc") or ""
        if len(d) >= ge.EVA_DESCRIPTION_MIN_LEN:
            ET.SubElement(o, "description_ua").text = d
        stats["deactivated"] += 1
    try:
        _atomic_write(LISTED_FILE, {"v": 2, "listed": new_prev, "saved_at": datetime.now().isoformat(timespec="seconds")})
    except OSError as e:
        print(f"[rt_eva] не вдалось зберегти {LISTED_FILE.name}: {e}", file=sys.stderr)
    return dict(stats)


def main(argv=None) -> int:
    """CLI: python rt_eva.py --build → перебудувати rt_eva_selection.json (важко; лише VPS/читання каталогів)."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--review-csv", help="після --build записати CSV «категорії RT без рішення» (для власника)")
    a = ap.parse_args(argv)
    if not a.build:
        ap.print_help()
        return 0
    from parser import fetch_toysi_catalog
    import generate_eva_feed as ge
    ty = fetch_toysi_catalog()
    sup = load_supplier()
    if len(ty) < 25_000 or not sup:
        print("[rt_eva] каталог Toysi/supplier_rt порожній або усічений — відбір не чіпаю", file=sys.stderr)
        return 1
    sel = build_selection(ty, sup, ge)
    _atomic_write(SEL_FILE, sel)
    print("[rt_eva] записано", SEL_FILE.name, json.dumps(sel["stats"], ensure_ascii=False))
    if a.review_csv:
        with open(a.review_csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["категорія_RT", "товарів", "здогадка_EVA_id", "здогадка_EVA_назва", "частка_здогадки", "id_валідний", "приклади_назв"])
            for r in sel["review"]:
                w.writerow([r["rt_category"], r["items"], r["guess_eva_id"], r["guess_eva_name"], r["guess_share"], r["guess_valid"], " | ".join(r["examples"])])
        print("[rt_eva] огляд категорій:", a.review_csv, len(sel["review"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
