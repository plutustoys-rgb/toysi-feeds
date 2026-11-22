#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rt_reprice.py — конкурентна ціна для товарів RoyalToys (rt-) у Prom-фіді (рішення власника 22.11.2026: «роби без ручної роботи», репрайсер МАЄ працювати й по rt-).

ЩО РОБИТЬ. rt_prom.py віддає rt-картки з ФІКСОВАНОЮ ціною «без конкурента» (cost×1,75). Тут — той самий конкурентний цикл, що й для Toysi, але для rt-:
  1. ПУЛ = rt_prom.repricer_pool(): лише rt-id, які зараз можуть бути в емісії rt_prom (відбір свіжий, постачальник свіжий, усі фільтри build_offer_fields пройдено, двійника Toysi у наявності нема),
     не більше rt_hard_max() (≤1500). Весь каталог RT НЕ скануємо.
  2. ПАРТІЯ за ротацією (спершу «ніколи не перевірені», чверть слотів гарантовано найстарішим перевіреним — див. pick_batch), ліміт RT_REPRICE_BATCH (400) і бюджет часу RUN_BUDGET_S (20 хв).
  3. На кожен rt-id — ТІ САМІ функції, що й для Toysi: find_best_competitor (buyBox за own_link → recommended → текстовий пошук) → decide_action (довіра score/розмір/фото, decide_price_for_platform
     з PRICE_STEP і MIN_PROFIT_COMPETITOR_FLOOR) → для «delist» — verify_competitor_really_available (не-buyBox). Нічого з цього не переписано.
  4. РІШЕННЯ пишеться в ОКРЕМИЙ стан rt_prom_reprice_state.json (ключ — рядок «rt-<id>»; Toysi-файл prom_competitor_price_state.json і full_catalog_scan_state.json НЕ чіпаються, тож Toysi-логіка
     бітово та сама, а будь-який код, що ітерує Toysi-стан і сподівається на числові pid, rt- не бачить).
  5. ФІД (rt_prom.build_offer_fields → feed_price): ціна rt- = запис стану за тими САМИМИ правилами, що generate_prom_feed._build_xml для override Toysi (свіжість 45 діб; без свіжого конкурента ≤30 год —
     стеля cap_stale_prom_override; floor-гард за свіжою собівартістю; min_safe). Нема запису → ціна «без конкурента» (як було). Неконкурентні (delist-рішення, запис свіжіший за NONCOMP_TTL_H) —
     НЕ емітуються (skip_noncompetitive): rt_prom віддає їх заглушкою available="false" як осиротілих (журнал rt_prom_listed_state.json) — далі їх бере окреме автоприбирання; ми цей журнал лише ЧИТАЄМО.

ЧОМУ БЕЗ ЗАПИСУ В PROM API. Для Toysi репрайсер ще й PATCH-ить ціну/видаляє картку через API (apply_price/delist). Для rt- — НІ: (а) єдиний канал створення/оновлення rt-карток — фід, ціну з нього Prom підхоплює
при кожному імпорті; (б) API-видалення необоротне (status=deleted) і перетнулось би з автоприбиранням осиротілих rt- (окремий PR) та журналом rt_prom_listed_state.json; (в) не потрібні ліміти зміни цін API.
Тому «delist» для rt- = «не публікувати в фіді».

ПРЕДОХОРОНИ (грошові). Ціна ніколи не нижча за floor (decide_price_for_platform: канонічна формула + min_safe «net ≥ собівартість»), у фіді — повторний floor-гард за свіжою собівартістю, мінімум 60 ₴ (rt_prom.MIN_PROM_PRICE).
Хибний конкурент: усі гейти decide_action (score, розмір, фото, buyBox; delist лише buyBox або score ≥0.85/фото + verify сторінки конкурента). Масовість: не більше MAX_DELIST_PER_RUN зняттів за прогін;
якщо частка delist серед перевірених ≥ BREAKER_DELIST_SHARE (при ≥ BREAKER_MIN_CHECKED перевірених) — зняття ЗАБЛОКОВАНІ цілим прогоном (схоже на збій пошуку/buyBox), ціни лишаються на безпечному floor.
Категорія на дефолтній комісії (не підтверджена) — без delist, як у Toysi.

БРАМА: PROM_RT_REPRICE=1 (за замовчуванням ВИМКНЕНО; потребує ще й PROM_RT_IN_FEED=1). Вимкнена → ні скану, ні файлу стану, ні зміни фіду (бітово як до цього PR). dry-run — за замовчуванням (запис лише з --apply
у prom_competitor_pricer, SAFETY_HOLD примусово повертає dry-run).

БЮДЖЕТ ЧАСУ. Виміряний темп скану — 2,52 с/SKU (full_catalog_competitor_scan, 2026-07-13). Повний прохід 1500 rt-id ≈ 63 хв. Партія 400 за прогін ≈ 17 хв (feed-pipeline кожні 6 год → ~1600/добу ≥ 1500: повний
круг ≈ 22 год, тобто кожен rt-id оновлюється щонайменше раз на ~24 год). Фаза йде ПІСЛЯ усього Toysi-циклу репрайсера (його час/черга не змінюються), має власний бюджет RUN_BUDGET_S і пропускається, якщо Toysi уже вичерпав
свій MAX_RUNTIME_SECONDS."""
import json
import math
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
STATE_FILE = Path(os.environ.get("RT_PROM_REPRICE_STATE_FILE", str(HERE / "rt_prom_reprice_state.json")))
PLATFORM = "prom"
BATCH_DEFAULT = 400             # rt-id за прогін (≈ 400 × 2,52 с ≈ 17 хв)
BATCH_ABS_MAX = 1500            # env PROM_RT_REPRICE_BATCH не може піднятись вище (= RT_DEFAULT_MAX: повний пул)
RUN_BUDGET_S = 1200.0           # жорсткий бюджет фази (20 хв); env PROM_RT_REPRICE_BUDGET_S може змінити (≥60)
SEC_PER_SKU = 2.52              # виміряно full_catalog_competitor_scan (2026-07-13): find_best_competitor + verify
MAX_DELIST_PER_RUN = 100        # кап зняттів (аналог MAX_DELIST_PER_RUN=250 Toysi, пул у ~4 рази менший)
BREAKER_MIN_CHECKED = 30
BREAKER_DELIST_SHARE = 0.6      # частка delist серед перевірених за прогін → усі зняття блокуємо
NONCOMP_TTL_H = 168.0           # delist-запис старший за це (7 діб) не знімає rt- з емісії (страховка: пул переперевіряє значно частіше)
LINK_RESOLVE_LIMIT = 100        # скільки own_link дорезолвлювати за прогін (як OWN_LINK_RESOLVE_BATCH_LIMIT=400 у Toysi, з урахуванням меншого пулу)
LINK_RETRY_H = 24.0             # «картки ще нема в Prom» — повторний запит не раніше за
PRUNE_AFTER_H = 24.0 * 45       # записи поза пулом старші за це видаляються при apply


def gate_on() -> bool:
    return os.environ.get("PROM_RT_REPRICE") == "1"


def batch_size() -> int:
    v = os.environ.get("PROM_RT_REPRICE_BATCH", "").strip()
    return max(1, min(int(v), BATCH_ABS_MAX)) if v.isdigit() else BATCH_DEFAULT


def run_budget_s() -> float:
    v = os.environ.get("PROM_RT_REPRICE_BUDGET_S", "").strip()
    return max(60.0, float(v)) if v.isdigit() else RUN_BUDGET_S


def estimate_minutes(n: int) -> float:
    return n * SEC_PER_SKU / 60.0


# ───────────────────────────── стан ─────────────────────────────
def _empty_state() -> dict:
    return {"v": 1, "items": {}, "links": {}, "_meta": {}}


def _backup_path() -> Path:
    return STATE_FILE.with_suffix(".json.bak")


def _norm_state(d) -> dict:
    out = _empty_state()
    if isinstance(d, dict):
        for k in ("items", "links"):
            v = d.get(k)
            out[k] = {str(a): b for a, b in v.items() if isinstance(b, dict)} if isinstance(v, dict) else {}
        if isinstance(d.get("_meta"), dict):
            out["_meta"] = d["_meta"]
    return out


def load_state() -> dict:
    """{"items": {"rt-<id>": запис}, "links": {...}, "_meta": {...}}. Нема файлу → порожній стан без шуму; пошкоджений → резервна копія, інакше порожній (фід тоді віддає ціну «без конкурента», як до цього PR)."""
    try:
        text = STATE_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty_state()
    except OSError:
        return _empty_state()
    try:
        return _norm_state(json.loads(text))
    except ValueError:
        try:
            st = _norm_state(json.loads(_backup_path().read_text(encoding="utf-8")))
            print(f"[rt_reprice] {STATE_FILE.name} пошкоджений — відновлено з {_backup_path().name}", file=sys.stderr)
            return st
        except (OSError, ValueError):
            print(f"[rt_reprice] {STATE_FILE.name} пошкоджений і резервної копії нема — стан порожній", file=sys.stderr)
            return _empty_state()


def save_state(state: dict) -> None:
    """Атомарний запис (tmp + os.replace); попередній ВАЛІДНИЙ файл лишається як .json.bak (як save_prom_price_state)."""
    data = json.dumps(state, ensure_ascii=False, indent=1)
    tmp = STATE_FILE.with_name(f"{STATE_FILE.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(data, encoding="utf-8")
        if STATE_FILE.exists():
            try:
                json.loads(STATE_FILE.read_text(encoding="utf-8"))
                _backup_path().write_bytes(STATE_FILE.read_bytes())
            except (ValueError, OSError):
                pass
        os.replace(tmp, STATE_FILE)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def feed_context(now: datetime | None = None) -> dict:
    """Контекст для rt_prom.build_offer_fields(reprice=…): знімок записів стану (читання) + момент часу."""
    return {"items": load_state()["items"], "now": now or datetime.now()}


# ───────────────────────────── ціна у фіді ─────────────────────────────
def _age_h(ts_text, now: datetime):
    try:
        return (now - datetime.fromisoformat(str(ts_text))).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def feed_price(sid: str, cost: float, tcat, pid_int: int, default_price: float, ctx: dict):
    """→ (ціна, "") або (None, "noncompetitive"). Дзеркало логіки generate_prom_feed._build_xml для override Toysi (рядки з `if item_id in overrides`), на rt-записі стану:
      • нема запису / запис битий / старший PROM_PRICE_STATE_MAX_AGE_HOURS (45 діб) → default_price (ціна «без конкурента», як було);
      • action == "delist" і запис не старший NONCOMP_TTL_H → (None, "noncompetitive") — rt- не емітується;
      • інакше retail = price запису; без СВІЖОГО конкурента (≤PROM_COMPETITOR_STATE_MAX_AGE_HOURS) — стеля cap_stale_prom_override(retail, default_price);
        floor: є свіжий конкурент → canonical_competitor_floor(cost_eff, comp−PRICE_STEP, commission), інакше division-floor compute_floor(…, MIN_PROFIT_COMPETITOR_FLOOR); потім max(floor, cost_eff/(1−commission)); retail<floor → ceil до копійки."""
    import competitor_pricing as cp
    now = ctx.get("now") or datetime.now()
    e = (ctx.get("items") or {}).get(sid)
    if not isinstance(e, dict):
        return default_price, ""
    age = _age_h(e.get("timestamp"), now)
    if age is None:
        return default_price, ""
    if e.get("action") == "delist" and age <= NONCOMP_TTL_H:
        return None, "noncompetitive"
    if age > cp.PROM_PRICE_STATE_MAX_AGE_HOURS:
        return default_price, ""
    try:
        retail = float(e["price"])
    except (KeyError, TypeError, ValueError):
        return default_price, ""
    if not retail > 0 or retail != retail or retail == float("inf"):
        return default_price, ""
    comp = None
    try:
        c = float(e.get("competitor_price") or 0)
    except (TypeError, ValueError):
        c = 0.0
    if c > 0 and e.get("competitor_alive") is not False and age <= cp.PROM_COMPETITOR_STATE_MAX_AGE_HOURS:
        comp = c
    if not comp:
        retail = cp.cap_stale_prom_override(retail, default_price, False)
    commission = cp.compute_total_commission(PLATFORM, tcat, retail, prom_category_id=pid_int)
    cost_eff = cp.prom_effective_cost(cost)
    if comp:
        floor = cp.canonical_competitor_floor(cost_eff, round(comp - cp.PRICE_STEP, 2), commission)
    else:
        floor = cp.compute_floor(cost_eff, commission, cp.MIN_PROFIT_COMPETITOR_FLOOR)
    if commission < 1:
        floor = max(floor, round(cost_eff / (1 - commission), 2))
    if retail < floor:
        retail = math.ceil(floor * 100 - 1e-6) / 100
    return retail, ""


# ───────────────────────────── партія (ротація) ─────────────────────────────
def pick_batch(pool_ids, items: dict, limit: int) -> list:
    """Партія за ротацією. «Ніколи не перевірені» (нема запису → rt- висить на ціні «без конкурента») йдуть ПЕРШИМИ, але не більше limit − limit//4: чверть партії ГАРАНТОВАНО — найстарішим перевіреним
    (оновлення), як у Toysi (там половина/половина — захист від голодування оновлень; пул rt- ≤1500 і партія 400 дають повне першочергове покриття за ~4 прогони, тож Toysi-пропорція 50/50 лише б
    розтягнула його вдвічі). Решта слотів, якщо перевірених менше за квоту, добирається новими. Порядок пулу стабільний (pool_ids)."""
    def last(sid):
        return (items.get(sid) or {}).get("timestamp") or ""

    never = [s for s in pool_ids if not last(s)]
    checked = [s for s in pool_ids if last(s)]
    nb = never[: limit - limit // 4]
    rb = sorted(checked, key=last)[: max(0, limit - len(nb))]
    out = nb + rb
    if len(out) < limit:
        out += never[len(nb): len(nb) + limit - len(out)]
    return out


# ───────────────────────────── мережеві залежності (підміняються в тестах) ─────────────────────────────
def default_net():
    import random
    import prom_competitor_pricer as pcp
    from generate_google_feed import _resolve_url_text, SEARCH_JITTER_RANGE
    from prom_catalog_sync import fetch_prom_products_by_external_ids
    from telegram_notify import send_telegram_message
    return SimpleNamespace(
        find_best_competitor=pcp.find_best_competitor, decide_action=pcp.decide_action,
        verify=pcp.verify_competitor_really_available, identity=pcp._competitor_identity_key,
        commission_is_default=pcp._category_commission_is_default, search_delay=pcp.SEARCH_DELAY, sleep=time.sleep,
        fetch_by_ext_ids=fetch_prom_products_by_external_ids, resolve_url_text=_resolve_url_text,
        jitter=lambda: time.sleep(random.uniform(*SEARCH_JITTER_RANGE)), notify=send_telegram_message,
    )


# ───────────────────────────── прогін ─────────────────────────────
def _resolve_links(batch, pool, links, listed, net, now_iso, now, stats):
    """own_link (prom_id+urlText) для rt-id партії, які ВЖЕ були в фіді (є в журналі rt_prom_listed_state.json — картка в Prom імовірно існує) і яких ще нема в links. Той самий механізм, що в
    full_catalog_competitor_scan._resolve_own_links_for_batch: пакетний GET /products/by_external_id (оминає сліпу пляму груп) + _resolve_url_text. Не знайдено → absent_at (повтор не раніше LINK_RETRY_H)."""
    todo = []
    for sid in batch:
        if sid not in listed:
            continue
        ln = links.get(sid) or {}
        if ln.get("prom_id") and ln.get("url_text"):
            continue
        age = _age_h(ln.get("absent_at"), now) if ln.get("absent_at") else None
        if age is not None and age < LINK_RETRY_H:
            continue
        todo.append(sid)
    todo = todo[:LINK_RESOLVE_LIMIT]
    if not todo:
        return
    found, indeterminate = net.fetch_by_ext_ids(set(todo))
    for sid in todo:
        if sid in indeterminate:
            stats["link_indeterminate"] += 1
            continue
        product = found.get(sid)
        if not product:
            links[sid] = {"absent_at": now_iso}
            stats["link_absent"] += 1
            continue
        prom_id = product.get("id")
        url_text = net.resolve_url_text(prom_id) if prom_id else None
        net.jitter()
        if prom_id and url_text:
            links[sid] = {"prom_id": prom_id, "url_text": url_text, "at": now_iso}
            stats["link_resolved"] += 1


def decide_one(p: dict, own_link, net):
    """Рішення для одного rt-id тими самими функціями, що й Toysi. → (decision, competitor)."""
    competitor = net.find_best_competitor(p["name"], p["cost"], own_link, p["pictures"])
    decision = net.decide_action(p["cost"], competitor, p["tcat"], p["name"], p["pid_int"], p["pictures"])
    net.sleep(net.search_delay)
    if decision["action"] == "delist" and (decision.get("competitor") or {}).get("source") != "buybox":
        if not net.verify(decision["competitor"]):
            decision["action"] = "adjust"          # сторінка конкурента не підтверджує InStock → не знімаємо (як presence_unconfirmed у Toysi)
        net.sleep(net.search_delay)
    if decision["action"] == "delist" and net.commission_is_default(p["tcat"], p["pid_int"]):
        decision["action"] = "adjust"              # непідтверджена комісія → без зняття (як default_commission_skipped у Toysi)
    return decision, competitor


def run_phase(catalog: dict, apply: bool = False, *, net=None, now: datetime | None = None, time_exceeded=None, notify: bool = True) -> dict:
    """Одна партія репрайсингу rt-. apply=False → лише розрахунок і друк (жодного запису: ні стану, ні посилань). Повертає статистику (dict)."""
    import rt_prom
    stats = Counter()
    if not gate_on():
        return {"skipped": "gate_off"}
    if not rt_prom.gate_on():
        print("[rt_reprice] PROM_RT_IN_FEED не ввімкнено — rt- у фіді нема, репрайсинг пропущено.")
        return {"skipped": "rt_not_in_feed"}
    if not catalog or len(catalog) < rt_prom.MIN_CATALOG_N:
        print("[rt_reprice] каталог Toysi порожній/усічений — пропущено (двійники Toysi не перевірити).", file=sys.stderr)
        return {"skipped": "catalog"}
    if not rt_prom.supplier_fresh():
        print("[rt_reprice] supplier_rt.json застарів — пропущено.", file=sys.stderr)
        return {"skipped": "supplier_stale"}
    sel = rt_prom.load_selection()
    if not sel or not rt_prom.selection_fresh(sel):
        print("[rt_reprice] відбір rt_prom відсутній/застарілий — пропущено.", file=sys.stderr)
        return {"skipped": "selection_stale"}
    if time_exceeded and time_exceeded():
        print("[rt_reprice] бюджет часу Toysi-циклу вичерпано — rt- пропущено цього прогону.")
        return {"skipped": "toysi_budget"}
    now = now or datetime.now()
    now_iso = now.isoformat()
    net = net or default_net()
    pool = rt_prom.repricer_pool(catalog, sel, rt_prom.load_supplier())
    state = load_state()
    items, links = state["items"], state["links"]
    batch = pick_batch(list(pool), items, batch_size())
    print(f"[rt_reprice] пул {len(pool)} rt-id (≤{rt_prom.rt_hard_max()}); партія {len(batch)} (≈{estimate_minutes(len(batch)):.0f} хв за {SEC_PER_SKU} с/SKU); режим {'APPLY' if apply else 'DRY-RUN'}.")
    listed = rt_prom.load_listed()
    deadline = time.monotonic() + run_budget_s()
    _resolve_links(batch, pool, links, listed, net, now_iso, now, stats)
    results = []
    for sid in batch:
        if time.monotonic() >= deadline or (time_exceeded and time_exceeded()):
            stats["budget_stop"] = 1
            print(f"[rt_reprice] бюджет часу вичерпано — {len(results)} з {len(batch)} перевірено, решта в наступний прогін.")
            break
        p = pool[sid]
        ln = links.get(sid) or {}
        own_link = {"prom_id": ln["prom_id"], "url_text": ln["url_text"]} if ln.get("prom_id") and ln.get("url_text") else None
        try:
            decision, competitor = decide_one(p, own_link, net)
        except Exception as e:  # noqa: BLE001 — збій одного id не валить партію
            stats["error"] += 1
            print(f"[rt_reprice] {sid}: збій рішення ({type(e).__name__}: {str(e)[:120]}) — пропущено", file=sys.stderr)
            continue
        if not decision["price"] or decision["price"] < p["cost"]:
            stats["skip_price_below_cost"] += 1          # недосяжно за формулою (min_safe), остання лінія оборони
            continue
        results.append((sid, decision, own_link is not None))
        print(f"{sid}\tcost={p['cost']:.0f}\tprice={decision['price']:.0f}\tmargin={decision['margin_pct']:.1f}%\t[{decision['action']}/{decision['category']}]\t"
              + (f"конкурент {decision['competitor']['price']:.0f} (score={decision['competitor']['score']:.2f}, {decision['competitor'].get('source') or 'search'})" if decision.get("competitor") else "конкурента нема"))
    checked = len(results)
    delist = [r for r in results if r[1]["action"] == "delist"]
    breaker = checked >= BREAKER_MIN_CHECKED and len(delist) / checked >= BREAKER_DELIST_SHARE
    allowed = set()
    if breaker:
        stats["breaker_blocked_delist"] = len(delist)
        print(f"[rt_reprice] BREAKER: {len(delist)}/{checked} ({len(delist) / checked:.0%}) delist ≥ {BREAKER_DELIST_SHARE:.0%} — зняття заблоковано цілим прогоном (схоже на збій пошуку/buyBox), ціни лишаються на floor.", file=sys.stderr)
    else:
        delist.sort(key=lambda r: (r[1]["price"] / r[1]["competitor_price"]) if r[1].get("competitor_price") else 0.0, reverse=True)
        allowed = {r[0] for r in delist[:MAX_DELIST_PER_RUN]}
        stats["delist_deferred_by_cap"] = max(0, len(delist) - MAX_DELIST_PER_RUN)
    new_items = {}
    for sid, decision, had_link in results:
        action = "delist" if (decision["action"] == "delist" and sid in allowed) else "adjust"
        stats["noncompetitive" if action == "delist" else "adjust"] += 1
        if decision.get("competitor") is None:
            stats["no_competitor"] += 1
        new_items[sid] = {"price": decision["price"], "timestamp": now_iso, "action": action, "category": decision["category"], "competitor_price": decision["competitor_price"],
                          "competitor_key": net.identity(decision.get("competitor")), "cost": pool[sid]["cost"], "margin_pct": decision["margin_pct"]}
    stats["checked"] = checked
    print(f"[rt_reprice] підсумок: перевірено {checked}; конкурентні/на floor з цінами {stats['adjust']}; неконкурентні (знімаються з емісії) {stats['noncompetitive']}; без конкурента {stats['no_competitor']}; "
          f"помилок {stats['error']}; відкладено капом {stats['delist_deferred_by_cap']}.")
    if apply:
        items.update(new_items)
        for sid in [s for s in items if s not in pool and (_age_h(items[s].get("timestamp"), now) or 0) > PRUNE_AFTER_H]:
            items.pop(sid, None)                      # осиротілі записи поза пулом старші за 45 діб — прибираємо (стан не росте без меж)
        for sid in [s for s in links if s not in pool and s not in listed]:
            links.pop(sid, None)
        state["_meta"] = {"last_run": now_iso, "pool": len(pool), "checked": checked, "noncompetitive": stats["noncompetitive"]}
        save_state(state)
        if notify and (stats["noncompetitive"] or stats["breaker_blocked_delist"]):
            try:
                net.notify(f"💰 rt_reprice (--apply, feed-only): rt- перевірено {checked}, знімаємо з емісії як неконкурентні {stats['noncompetitive']}"
                           + (f", ЗАБЛОКОВАНО breaker'ом {stats['breaker_blocked_delist']}" if stats["breaker_blocked_delist"] else "") + ".")
            except Exception as e:  # noqa: BLE001
                print(f"[rt_reprice] сповіщення не надіслано ({type(e).__name__})", file=sys.stderr)
    else:
        print("[rt_reprice] DRY-RUN: стан не записано.")
    return dict(stats)
