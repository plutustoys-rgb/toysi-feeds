#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kv_eva.py — товари KIDDISVIT у EVA-фіді (рішення власника 18.11.2026: «додавай товар кіддісвіт без дублів на єву. тільки той, якого немає у каталозі. дублі — коли у Тойсі немає в наявності»).

ПРАВИЛО ВІДБОРУ (ЄДИНЕ, без винятків)
 • «Немає у каталозі» = немає двійника ні серед Toysi-карток, ні серед RoyalToys-карток, які ЗАРАЗ активні (available="true") в EVA-дереві цього прогону. Двійник береться з даних, а не з «схожості в моменті»:
   — Toysi: поле `dup_of` картки kv- у supplier_kv.json (kv_dup_finder → kv_dup_map.json);
   — RoyalToys: зворотна мапа rt_kv_dup_map.json (rt-… → kv-…);
   — клас DUP із відбору (rt_eva.classify_dups) БЕЗ відомого двійника → вважаємо «двійник є» і НЕ емітуємо (не можемо перевірити наявність).
 • Двійник є → kv-картка йде у фід ЛИШЕ поки жоден з її двійників не активний у цьому фіді (у Toysi немає в наявності). Двійник повернувся → kv-картка стає available="false" (EVA: вимкнення, не видалення і не зміна категорії).
 • Клас GRAY («схожий, але не точний дубль») — НЕ емітуємо (власник: «тільки той, якого немає у каталозі»; для RT було інакше — там «сумнівні» дозволені).
 • «Уцінка» (kvu-) у EVA не йде зовсім (пошкоджене пакування; політика «Уцінка» — лише власний сайт).
ЯК ЦЕ НЕ ВИКЛИКАЄ НОВУ МОДЕРАЦІЮ ІСНУЮЧИХ КАРТОК
 • Нічого, що вже стоїть в EVA (Toysi, RT), не змінюється: ні id, ні категорія, ні назва — kv лише ДОДАЄТЬСЯ в кінець <offers> (на копії дерева; збій → фід без kv, як є).
 • Колись листована kv-картка тримає ТУ САМУ категорію (липка, kv_eva_listed_state.json) — EVA після додавання categoryId не міняти.
 • Нові id додаються лише через відбір з перевіреною категорією (rt_eva.build_selection: ручні рішення → правило категорії → високодовірений kNN; нема впевненості → товар не беремо).
Брама: EVA_KV_IN_FEED=1 у .env VPS (вмикає лише власник); без неї — нічого не міняється. Застарілі supplier_kv.json (>6 год) і відбір (>36 год) → kv у цьому прогоні пропускається (fail-safe).
Емісія — ті самі перевірки полів, що в rt_eva.build_offer_fields (бренд, стоп-бренди, заборонені слова/товари, залишок ≥2, ціна, фото https, опис ≥30, розміри, ≥2 параметри); ціна = supplier_kv.price (закрита формула сайту; рішення про ціну EVA окреме — власника)."""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import rt_eva
from google_rt_feed import leaks

HERE = Path(__file__).resolve().parent
SEL_FILE = Path(os.environ.get("KV_EVA_SELECTION_FILE", str(HERE / "kv_eva_selection.json")))
LISTED_FILE = Path(os.environ.get("KV_EVA_LISTED_FILE", str(HERE / "kv_eva_listed_state.json")))
OVERRIDES_FILE = HERE / "kv_eva_category_overrides.json"
SUPPLIER_FILE = Path(os.environ.get("SUPPLIER_KV_FILE", str(HERE / "supplier_kv.json")))
RT_KV_MAP_FILE = HERE / "rt_kv_dup_map.json"
UNIQUE_CLASSES = ("UNIQUE", "UNIQUE_BRAND")                 # GRAY — ні (див. докстрінг)
SEL_CLASSES = UNIQUE_CLASSES + ("DUP",)                     # DUP теж потрібен у відборі: категорію беремо так само, а емісію вирішує двійник
STALE_H = rt_eva.STALE_H
MIN_KEEP_RATIO = rt_eva.MIN_KEEP_RATIO
KV_ID_RX = re.compile(r"kv-[0-9]{1,12}")


def gate_on() -> bool:
    return os.environ.get("EVA_KV_IN_FEED") == "1"


def load_overrides() -> dict:
    try:
        d = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def load_supplier() -> dict:
    """Лише kv- (не kvu-): {kv-<id>: item}. Порожній dict — файл відсутній/зіпсований."""
    try:
        d = json.loads(SUPPLIER_FILE.read_text(encoding="utf-8"))
        items = d.get("items")
        if not isinstance(items, dict):
            return {}
        return {k: v for k, v in items.items() if KV_ID_RX.fullmatch(str(k)) and isinstance(v, dict)}
    except (OSError, ValueError, AttributeError):
        return {}


def supplier_fresh(max_h: float = 6.0) -> bool:
    try:
        d = json.loads(SUPPLIER_FILE.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(str(d.get("updated")).replace("Z", "+00:00"))
        now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.now()
        return now - ts <= timedelta(hours=max_h)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def load_rt_twins() -> dict:
    """{kv-<id>: [rt-<id>, …]} — зворотна мапа rt_kv_dup_map.json (rt → kv). Файл зіпсований → {} (тоді RT-двійників «нема»: НЕБЕЗПЕЧНО лише у бік зайвої картки, тому при зіпсованому файлі kv не емітуємо — див. twins_ok)."""
    try:
        d = json.loads(RT_KV_MAP_FILE.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return None
    except (OSError, ValueError):
        return None
    out = {}
    for rt_id, kv_id in d.items():
        out.setdefault(str(kv_id), []).append(str(rt_id))
    return out


def twins_of(sid: str, item: dict, sel_item: dict, rt_twins: dict) -> tuple:
    """→ (список id двійників, «двійник є, але невідомо який»). Невідомий двійник = клас DUP відбору без dup_of: наявність не перевірити → не емітуємо."""
    ids = []
    if item.get("dup_of"):
        ids.append(str(item["dup_of"]))
    ids.extend(rt_twins.get(sid, []))
    unknown = (sel_item.get("cls") == "DUP") and not ids
    return ids, unknown


# ───────────────────────────── відбір ─────────────────────────────
def build_selection(ty: dict, sup_items: dict, ge) -> dict:
    return rt_eva.build_selection(ty, sup_items, ge, overrides=load_overrides(), include_classes=SEL_CLASSES)


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


KV_REBUILD_AFTER_H = 4.0        # коротше, ніж у RT (12 год): двійник Toysi, що повернувся/з'явився, не має висіти поряд із kv-карткою до півдоби (аудит #1705, залишковий ризик 1)


def ensure_selection(ty: dict, ge, rebuild_after_h: float = KV_REBUILD_AFTER_H):
    """Ті самі гарди, що в rt_eva.ensure_selection (аудит #939 B4): усічений каталог Toysi → без перебудови; новий відбір <70% попереднього → відкидаємо; збій → старий, якщо свіжий."""
    sup = load_supplier()
    if not sup:
        return None
    sel = load_selection()
    fresh_old = sel if sel and selection_fresh(sel) else None
    if fresh_old and fresh_old.get("rt_n", 0) > 0 and (datetime.now() - datetime.fromisoformat(fresh_old["built_at"])) <= timedelta(hours=rebuild_after_h):
        return fresh_old
    if len(ty) < 25_000:
        print(f"[kv_eva] каталог Toysi усічений ({len(ty)}) — відбір не перебудовую", file=sys.stderr)
        return fresh_old
    try:
        new = build_selection(ty, sup, ge)
    except Exception as e:  # noqa: BLE001
        print(f"[kv_eva] перебудова відбору не вдалась ({type(e).__name__}: {str(e)[:120]}) — {'лишаємо попередній' if fresh_old else 'kv у фід цього прогону НЕ йдуть'}", file=sys.stderr)
        return fresh_old
    prev_n = (sel or {}).get("stats", {}).get("in_feed_candidates") or 0
    if fresh_old and prev_n and new["stats"]["in_feed_candidates"] < prev_n * MIN_KEEP_RATIO:
        print(f"[kv_eva] новий відбір підозріло менший ({new['stats']['in_feed_candidates']} проти {prev_n}) — відкинуто, лишаємо попередній", file=sys.stderr)
        return fresh_old
    rt_eva._atomic_write(SEL_FILE, new)
    return new


# ───────────────────────────── емісія ─────────────────────────────
def _validate(f: dict, valid_ids: set, name_max: int) -> str:
    if not KV_ID_RX.fullmatch(f["id"]):
        return "id"
    return rt_eva.validate_offer({**f, "id": "rt-" + f["id"][3:]}, valid_ids, name_max)      # решта перевірок спільні (назва, ціна/склад, категорія, фото, поля)


def _load_listed() -> dict:
    try:
        d = json.loads(LISTED_FILE.read_text(encoding="utf-8"))
        v = d.get("listed")
        if isinstance(v, dict):
            return {str(k): (x if isinstance(x, dict) else {}) for k, x in v.items()}
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def twin_in_stock(tid: str, live: set, catalog: dict | None, ge) -> bool:
    """Двійник «є в наявності»: активний у цьому фіді АБО в каталозі Toysi залишок > EVA_SELECTION_MIN_STOCK (та сама межа, що для відбору Toysi у EVA; слово власника — «у Тойсі немає в наявності», а не «Toysi-картки немає в EVA»)."""
    if tid in live:
        return True
    if catalog:
        it = catalog.get(tid)
        if it is None and tid.isdigit():
            it = catalog.get(int(tid))
        try:
            return bool(it) and float(it.get("stock", 0) or 0) > ge.EVA_SELECTION_MIN_STOCK
        except (TypeError, ValueError, AttributeError):
            return False
    return False


def append_kv_offers(root: ET.Element, ge, selection: dict, sup_items: dict, catalog: dict | None = None) -> dict:
    """Дописує у <shop> категорії (якщо ще нема) і offer'и KIDDISVIT за правилом з докстрінга. root — результат ge._build_xml (+ RT, якщо ввімкнено) ДО indent/CDATA. catalog — каталог Toysi {id: item} (наявність двійників)."""
    from forbidden_products import is_forbidden
    shop = root.find("shop")
    cats_el, offers_el = shop.find("categories"), shop.find("offers")
    have_cats = {c.get("id") for c in cats_el}
    valid = set(selection.get("valid_ids") or []) | have_cats
    live = {o.get("id") for o in offers_el if o.get("available") == "true"}           # активні картки EVA цього прогону (Toysi + RT)
    live_names = {ge._dedup_key(o.findtext("name_ua") or "") for o in offers_el if o.get("available") == "true"}      # назви активних карток: динамічна перевірка двійників (мапи dup_of/rt_kv — знімки, а двійник може з'явитись/повернутись між їх перебудовами; аудит #1705 B1)
    toysi_names = {ge._dedup_key(o.findtext("name_ua") or "") for o in offers_el}
    rt_twins = load_rt_twins()
    stats = Counter()
    if rt_twins is None:
        stats["abort_rt_kv_map_unreadable"] += 1               # без мапи RT↔kv не знаємо, чи нема RT-двійника в EVA: нічого нового не додаємо, лише підтримуємо вже листоване (нижче)
        rt_twins = {}
        twins_ok = False
    else:
        twins_ok = True
    prev = _load_listed()
    items = selection.get("items", {})
    cand = {}
    for sid, si in items.items():
        if KV_ID_RX.fullmatch(sid) and si.get("cls") in SEL_CLASSES:
            cand[sid] = dict(si)
        elif si.get("cls") == "GRAY":
            stats["skip_gray"] += 1                             # «схожий, але не точний дубль» — власник: лише те, чого немає у каталозі
    for sid, snap in prev.items():                              # гістерезис: колись листована kv-картка лишається кандидатом (категорія липка)
        if sid not in cand and snap.get("cat"):
            cand[sid] = {"eva_id": snap["cat"], "eva_name": snap.get("catname") or "", "cls": "HOLD", "src": "sticky"}
    for sid, si in cand.items():
        sc = (prev.get(sid) or {}).get("cat")
        if sc and sc in valid:
            si["eva_id"], si["eva_name"] = sc, (prev[sid].get("catname") or si.get("eva_name") or "")
    excluded_cats = {k for k, v in load_overrides().items() if v is None}
    active = {}
    for sid, sel_item in cand.items():
        r = sup_items.get(sid)
        if not r:
            stats["skip_not_in_supplier"] += 1
            continue
        if (r.get("category") or "").strip() in excluded_cats:
            stats["skip_override_exclude"] += 1
            continue
        if not catalog and sid not in prev:
            stats["skip_no_catalog"] += 1                        # без каталогу Toysi наявність двійників не перевірити → нових карток не додаємо
            continue
        tw, unknown = twins_of(sid, r, sel_item, rt_twins)
        if unknown:
            stats["skip_dup_twin_unknown"] += 1
            continue
        if any(twin_in_stock(t, live, catalog, ge) for t in tw):
            stats["skip_twin_in_stock"] += 1                    # двійник активний (Toysi/RT має в наявності) → kv не показуємо; вже листоване піде у деактивацію нижче
            continue
        if not twins_ok and sid not in prev:
            continue                                            # нова картка без перевірки RT-двійників — ні
        f, why = rt_eva.build_offer_fields(sid, r, sel_item, ge, is_forbidden)
        if f is None:
            stats["skip_" + why] += 1
            continue
        bad = _validate(f, valid, ge.EVA_NAME_MAX_LEN)
        if bad:
            stats["invalid_" + bad] += 1
            continue
        if leaks(f["name"], f["vendor"], f["description"], *[v for _n, v in f["params"]]):
            stats["skip_supplier_leak"] += 1                    # ім'я постачальника/службовий id не мають потрапити в EVA-картку (рішення власника: покупець не бачить постачальника)
            continue
        if ge._dedup_key(f["name"]) in live_names:
            stats["skip_name_matches_live_card"] += 1           # та сама назва, що в активній картці Toysi/RT → вважаємо двійником (замість суфікса «(артикул)»)
            continue
        active[sid] = (f, sel_item, bool(tw))
    seen = set(toysi_names)
    for sid, (f, sel_item, _d) in sorted(active.items()):
        key = ge._dedup_key(f["name"])
        if key in seen:
            code = (sup_items[sid].get("vendor_code") or "").strip()
            suffix = f" ({code})" if code else f" ({sid})"
            f["name"] = ge._truncate(f["name"], ge.EVA_NAME_MAX_LEN - len(suffix)) + suffix
            key = ge._dedup_key(f["name"])
            stats["renamed_dup"] += 1
        seen.add(key)
    new_prev = dict(prev)
    for sid, (f, sel_item, is_dup) in sorted(active.items()):
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
        new_prev[sid] = rt_eva._snapshot(f, sel_item)
        stats["emitted"] += 1
        stats["emitted_as_dup_replacement" if is_dup else "emitted_unique"] += 1
    for sid in sorted(set(new_prev) - set(active)):             # деактивація: колись листовані, зараз не активні (двійник повернувся / вичерпано / зникло з відбору)
        snap = new_prev.get(sid) or {}
        r = sup_items.get(sid) or {}
        price = rt_eva._num(r.get("price")) or rt_eva._num(snap.get("price"))
        name = snap.get("name") or r.get("name") or ""            # назва зі знімка (з артикулом, якщо додавався): назва існуючого id не міняється при деактивації (аудит #1705)
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
        pics = rt_eva._pictures(r, ge.EVA_MAX_PICTURES) if r else []
        for p in (pics or snap.get("pics") or []):
            ET.SubElement(o, "picture").text = p
        vend = (snap.get("vendor") or r.get("vendor") or "").strip()          # зі знімка першим: що вже було в EVA, те й лишається; живий запис постачальника міг змінитись (аудит #1705)
        if vend and not leaks(vend):
            ET.SubElement(o, "vendor").text = vend if snap.get("vendor") else ge._clean_text(ge.normalize_vendor(vend))
        d = snap.get("desc") or ""
        if len(d) >= ge.EVA_DESCRIPTION_MIN_LEN:
            ET.SubElement(o, "description_ua").text = d
        stats["deactivated"] += 1
    try:
        rt_eva._atomic_write(LISTED_FILE, {"v": 1, "listed": new_prev, "saved_at": datetime.now().isoformat(timespec="seconds")})
    except OSError as e:
        print(f"[kv_eva] не вдалось зберегти {LISTED_FILE.name}: {e}", file=sys.stderr)
    return dict(stats)


def _csv_safe(v):
    """Клітинка, що починається з = + - @ таб/CR, у Excel виконується як формула (назви беруться з даних постачальника) — префікс апострофа."""
    t = "" if v is None else str(v)
    return "'" + t if t[:1] in ("=", "+", "-", "@", "	", "") else t


def preview(eva_xml: str, out_csv: str) -> int:
    """ПРОБНИЙ розрахунок (нічого не пише у фід і в стан-файли): «скільки і які kv-картки пішли б у EVA» на ПОТОЧНОМУ eva_feed.xml. Відбір будується в пам'яті, стан листованого — тимчасовий. Вихід — CSV."""
    import csv
    import tempfile
    from parser import fetch_toysi_catalog
    import generate_eva_feed as ge
    ty = fetch_toysi_catalog()
    sup = load_supplier()
    if len(ty) < 25_000 or not sup:
        print("[kv_eva] каталог Toysi/supplier_kv порожній або усічений — прев'ю неможливе", file=sys.stderr)
        return 1
    tmpd = Path(tempfile.mkdtemp())
    global LISTED_FILE
    real_listed = LISTED_FILE
    LISTED_FILE = tmpd / "listed.json"                         # справжній kv_eva_listed_state.json НЕ чіпаємо
    try:
        sel = build_selection(ty, sup, ge)
        sel["built_at"] = datetime.now().isoformat(timespec="seconds")
        root = ET.parse(eva_xml).getroot()
        before = {o.get("id") for o in root.find("shop").find("offers")}
        st = append_kv_offers(root, ge, sel, sup, ty)
    finally:
        LISTED_FILE = real_listed
    rows = []
    rt_tw = load_rt_twins() or {}
    items_sel = sel.get("items", {})
    for o in root.find("shop").find("offers"):
        i = o.get("id")
        if i in before or not KV_ID_RX.fullmatch(str(i)):
            continue
        r = sup.get(i, {})
        is_dup = bool(r.get("dup_of")) or i in rt_tw or (items_sel.get(i) or {}).get("cls") == "DUP"
        rows.append([i, o.findtext("name_ua"), o.findtext("price"), o.findtext("stock_quantity"), o.findtext("categoryId"), o.findtext("vendor"), "дубль (двійника немає в наявності)" if is_dup else "унікальний"])
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "назва", "ціна", "залишок", "категорія_EVA", "бренд", "тип"])
        w.writerows([[_csv_safe(c) for c in r] for r in rows])
    print("[kv_eva] прев'ю:", json.dumps(st, ensure_ascii=False), "→", out_csv, len(rows), "карток")
    return 0


def main(argv=None) -> int:
    """CLI: python kv_eva.py --build → перебудувати kv_eva_selection.json (важко; лише VPS/читання каталогів). --preview eva_feed.xml OUT.csv → пробний розрахунок без запису у фід."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--preview", nargs=2, metavar=("EVA_XML", "OUT_CSV"))
    a = ap.parse_args(argv)
    if a.preview:
        return preview(*a.preview)
    if not a.build:
        ap.print_help()
        return 0
    from parser import fetch_toysi_catalog
    import generate_eva_feed as ge
    ty = fetch_toysi_catalog()
    sup = load_supplier()
    if len(ty) < 25_000 or not sup:
        print("[kv_eva] каталог Toysi/supplier_kv порожній або усічений — відбір не чіпаю", file=sys.stderr)
        return 1
    sel = build_selection(ty, sup, ge)
    rt_eva._atomic_write(SEL_FILE, sel)
    print("[kv_eva] записано", SEL_FILE.name, json.dumps(sel["stats"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
