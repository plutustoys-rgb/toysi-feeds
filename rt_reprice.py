#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rt_reprice.py — конкурентна ціна для товарів RoyalToys (rt-) у Prom-фіді (рішення власника 22.11.2026: «роби без ручної роботи», репрайсер МАЄ працювати й по rt-).

ЩО РОБИТЬ. rt_prom.py віддає rt-картки з ФІКСОВАНОЮ ціною «без конкурента» (cost×1,75). Тут — той самий конкурентний цикл, що й для Toysi, але для rt-:
  1. ПУЛ = rt_prom.repricer_pool(): ТОЙ САМИЙ відбір і порядок, що в емісії (rt_prom.eligible_offers: клас, надгробки cleanup, фільтри, _rank_key), ≤rt_hard_max(). Весь каталог RT НЕ скануємо.
  2. ПАРТІЯ за ротацією (pick_batch: «ніколи не перевірені» першими, чверть слотів — найстарішим перевіреним; підтверджено неконкурентні — раз на 7 діб), бюджет RUN_BUDGET_S.
  3. На кожен rt-id — ТІ САМІ функції, що й для Toysi: find_best_competitor → decide_action (довіра score/розмір/фото) → verify_competitor_really_available для не-buyBox delist. Додатково для rt-
     (СТРОГИЙ ГЕЙТ збігу): числові токени назв (штуки/елементи/розміри/«3 шт») МАЮТЬ збігатись (strict_name_conflict) — інакше конкурент відкидається цілком (текстовий пошук/recommended; buyBox — ні).
  4. Рішення пишеться в ОКРЕМИЙ стан rt_prom_reprice_state.json (ключ «rt-<id>»; Toysi-стани не чіпаються).
  5. ФІД (rt_prom.build_offer_fields → feed_price): ціна зі стану за правилами generate_prom_feed._build_xml, але НІКОЛИ вища за дефолт «без конкурента» (cost×1,75); floor-гард + min_safe; мінімум 60 ₴.
     Неконкурентний rt- НЕ емітується; НОВА картка без свіжого запису adjust (≤48 год) НЕ створюється (check-before-create).

БЕЗПЕКА ЗНЯТТЯ (rt- хибні збіги → зняття, аудит 22.11): rt- знімається з емісії лише після 2 ПОСЛІДОВНИХ delist-рішень з інтервалом ≥20 год (лічильник streak у стані); ДОБОВИЙ кап зняттів ≤5 % пулу (мін. 5)
поверх попрогонового (100) і breaker'а (≥60 % delist серед ≥30 перевірених); гістерезис: зняту/повернену за останні 3 доби картку стан не міняє знову; пам'ять «неконкурентний» живе до 45 діб (fail-closed:
протермінований delist = не емітувати, поки не переперевірено), переперевірка раз на 7 діб (лише в пулі). Ціна рішення «floor» — division-floor 3 % (compute_floor), не нульовий прибуток.

РЕЖИМИ БРАМИ PROM_RT_REPRICE: порожньо/інше — вимкнено; observe — лише рішення в reports/rt_reprice_observe.jsonl (стан, ціни, фід НЕ міняються); 1 — застосовує. CLI `python rt_reprice.py --dry` — перегляд
пар «наша назва | конкурент | score | рішення» без запису у стан/Prom (бере каталог Toysi лише для перевірки двійників). Потребує ще й PROM_RT_IN_FEED=1 (фаза).

БЕЗ ЗАПИСУ В PROM API. Єдиний канал rt-карток — фід; API-видалення необоротне й перетнулось би з автоприбиранням осиротілих (rt_prom_cleanup). «delist» для rt- = «не публікувати в фіді».

БЮДЖЕТ ЧАСУ. Реалістично на SKU: виміряні 2,52 с (full_catalog_competitor_scan) + 2×SEARCH_DELAY пауз + сторінка verify ≈ 4,3 с. Партія 300 ≈ 22 хв, бюджет фази 25 хв (і всередині _resolve_links); feed-pipeline 4×/добу →
повний круг пулу 1500 ≈ 5 прогонів ≈ 30 год. Фаза йде ПІСЛЯ усього Toysi-циклу, пропускається, якщо Toysi вичерпав свій MAX_RUNTIME_SECONDS."""
import argparse
import json
import math
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
STATE_FILE = Path(os.environ.get("RT_PROM_REPRICE_STATE_FILE", str(HERE / "rt_prom_reprice_state.json")))
OBSERVE_FILE = Path(os.environ.get("RT_PROM_OBSERVE_FILE", str(HERE / "reports" / "rt_reprice_observe.jsonl")))
PLATFORM = "prom"
BATCH_DEFAULT = 300             # rt-id за прогін (≈ 300 × 4,3 с ≈ 22 хв)
BATCH_ABS_MAX = 1500
RUN_BUDGET_S = 1500.0           # жорсткий бюджет фази (25 хв); env PROM_RT_REPRICE_BUDGET_S може змінити (≥60)
SEC_PER_SKU = 2.52              # виміряно full_catalog_competitor_scan (2026-07-13): find_best_competitor + verify
VERIFY_S = 1.0                  # сторінка конкурента (verify_competitor_really_available) — оцінка
SEARCH_DELAY_ASSUMED = 0.4      # pricer.SEARCH_DELAY (дві паузи на SKU)
SEC_PER_SKU_REALISTIC = SEC_PER_SKU + 2 * SEARCH_DELAY_ASSUMED + VERIFY_S
MAX_DELIST_PER_RUN = 100        # попрогоновий кап зняттів
DAILY_CAP_SHARE = 0.05          # добовий кап зняттів: ≤5 % пулу…
DAILY_CAP_MIN = 5               # …але не менше 5
BREAKER_MIN_CHECKED = 30
BREAKER_DELIST_SHARE = 0.6
STREAK_MIN = 2                  # delist-рішень поспіль для зняття
STREAK_GAP_H = 20.0             # мінімальний інтервал між зарахованими delist-рішеннями
STREAK_MAX_GAP_H = 168.0        # серія старша за це починається заново
HYSTERESIS_H = 72.0             # знята/повернена за останні 3 доби картка не міняє стан знову
NONCOMP_RECHECK_H = 168.0       # підтверджено неконкурентний rt- переперевіряється раз на 7 діб (лише в пулі)
OBSERVE_MAX_LINES = 50_000      # observe-журнал: понад це — ротація в .jsonl.1 (одна попередня генерація)
STATE_STALE_H = 36.0            # стан репрайсера старший за це при увімкненій брамі → ⚠️-сповіщення
LINK_RESOLVE_LIMIT = 100
LINK_RETRY_H = 24.0
PRUNE_AFTER_H = 24.0 * 45
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def noncomp_memory_h() -> float:
    """Пам'ять «неконкурентний» = той самий строк, що й чинність цінового запису (45 діб): fail-closed — протермінований delist не емітується, доки не переперевірено; після 45 діб запис недійсний."""
    import competitor_pricing as cp
    return float(cp.PROM_PRICE_STATE_MAX_AGE_HOURS)


def mode() -> str:
    v = os.environ.get("PROM_RT_REPRICE", "").strip().lower()
    return "on" if v == "1" else ("observe" if v == "observe" else "off")


def warn_unknown_gate() -> bool:
    """Значення PROM_RT_REPRICE не з {порожньо, 0, 1, observe} (напр. «true»/«yes») тихо означало б «вимкнено» — власник думав би, що ввімкнув. → ⚠️-сповіщення (тротлінг 12 год); True, якщо значення невідоме."""
    v = os.environ.get("PROM_RT_REPRICE", "").strip().lower()
    if v in ("", "0", "1", "observe"):
        return False
    _alert("gate_value", f"PROM_RT_REPRICE={os.environ.get('PROM_RT_REPRICE')!r} — невідоме значення (дозволено лише 1, observe, порожньо/0): брама ВВАЖАЄТЬСЯ ВИМКНЕНОЮ, rt-репрайсинг не працює.")
    return True


def gate_on() -> bool:
    """True лише для PROM_RT_REPRICE=1 (застосування: стан, фід). observe фід і стан НЕ міняє."""
    return mode() == "on"


def batch_size() -> int:
    v = os.environ.get("PROM_RT_REPRICE_BATCH", "").strip()
    return max(1, min(int(v), BATCH_ABS_MAX)) if v.isdigit() else BATCH_DEFAULT


def run_budget_s() -> float:
    v = os.environ.get("PROM_RT_REPRICE_BUDGET_S", "").strip()
    return max(60.0, float(v)) if v.isdigit() else RUN_BUDGET_S


def estimate_minutes(n: int) -> float:
    return n * SEC_PER_SKU_REALISTIC / 60.0


def daily_cap(pool_n: int) -> int:
    return max(DAILY_CAP_MIN, math.ceil(DAILY_CAP_SHARE * pool_n))


def _alert(key: str, text: str) -> None:
    """⚠️-сповіщення із тротлінгом 12 год (категорія warning для health_check — як rt_prom_cleanup, #2017). Best-effort."""
    print(f"[rt_reprice] {text}", file=sys.stderr)
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert("rt_reprice_" + key, "⚠️ rt_reprice: " + text, cooldown_sec=12 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[rt_reprice] алерт не надіслано: {type(e).__name__}", file=sys.stderr)


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


def load_state_status() -> tuple:
    """→ (стан, "ok" | "missing" | "restored" (з .bak) | "lost" (пошкоджений і .bak нема/биту) | "unreadable")."""
    try:
        text = STATE_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty_state(), "missing"
    except OSError:
        return _empty_state(), "unreadable"
    try:
        return _norm_state(json.loads(text)), "ok"
    except ValueError:
        try:
            st = _norm_state(json.loads(_backup_path().read_text(encoding="utf-8")))
            print(f"[rt_reprice] {STATE_FILE.name} пошкоджений — відновлено з {_backup_path().name}", file=sys.stderr)
            return st, "restored"
        except (OSError, ValueError):
            print(f"[rt_reprice] {STATE_FILE.name} пошкоджений і резервної копії нема — стан порожній", file=sys.stderr)
            return _empty_state(), "lost"


def load_state() -> dict:
    return load_state_status()[0]


def save_state(state: dict) -> None:
    """Атомарний запис (tmp + os.replace); попередній ВАЛІДНИЙ файл лишається як .json.bak."""
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


def check_state_health(now: datetime | None = None) -> str:
    """Лише при увімкненій брамі (викликає фід): стан відсутній / пошкоджений без .bak / старший STATE_STALE_H → ⚠️-сповіщення (тротлінг 12 год). → "" або код проблеми."""
    now = now or datetime.now()
    st, status = load_state_status()
    if status in ("lost", "unreadable"):
        _alert("state_lost", f"стан {STATE_FILE.name} пошкоджений без резервної копії (або нечитабельний) при увімкненій брамі PROM_RT_REPRICE=1 — rt- у фіді за ціною «без конкурента», зняття втрачено.")
        return status
    if status == "missing":
        _alert("state_missing", f"стану {STATE_FILE.name} нема при увімкненій брамі PROM_RT_REPRICE=1 — репрайсинг rt- не відбувся жодного разу (фаза пропускається чи не запускалась).")
        return "missing"
    age = _age_h(st["_meta"].get("last_run"), now)
    if age is None or age > STATE_STALE_H:
        _alert("state_stale", f"стан репрайсера rt- застарів (останній прогін {st['_meta'].get('last_run') or 'невідомо'}, поріг {STATE_STALE_H:g} год) при увімкненій брамі — фаза не працює.")
        return "stale"
    return ""


def feed_context(now: datetime | None = None) -> dict:
    """Контекст для rt_prom.build_offer_fields(reprice=…): знімок записів стану (читання) + момент часу."""
    return {"items": load_state()["items"], "now": now or datetime.now()}


# ───────────────────────────── строгий гейт збігу ─────────────────────────────
def _numbers(name: str) -> list:
    out = []
    for m in NUM_RE.findall((name or "").lower()):
        x = m.replace(",", ".")
        try:
            out.append(f"{float(x):g}")
        except ValueError:
            out.append(x)
    return sorted(out)


def strict_name_conflict(our_name: str, competitor_name: str) -> bool:
    """СТРОГИЙ розмірний/кількісний гейт збігу для rt-: множини ЧИСЛОВИХ токенів (штуки, елементи, розміри, «3 шт», 35/24) у назвах мусять бути ОДНАКОВІ. Відрізняється від Toysi-гейту
    pricer._size_tokens_conflict (конфлікт лише коли токени є в ОБОХ назвах і не перетинаються): тут «Качечка» vs «Качечка 3 шт» (токен лише з одного боку) і «35 елементів» vs «24 елементи» — конфлікт."""
    return _numbers(our_name) != _numbers(competitor_name)


# ───────────────────────────── ціна у фіді ─────────────────────────────
def _age_h(ts_text, now: datetime):
    try:
        return (now - datetime.fromisoformat(str(ts_text))).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def new_card_checked(sid: str, ctx: dict, max_age_h: float) -> bool:
    """check-before-create: є запис стану action=adjust, НЕ старший max_age_h і БЕЗ незавершеної delist-серії (streak == 0): картка, що вже отримала перше сире delist-рішення, ще не підтверджена —
    НОВУ таку не створюємо (інакше її створили б, а після другого delist зняли/видалили)."""
    e = (ctx.get("items") or {}).get(sid)
    if not isinstance(e, dict) or e.get("action") == "delist" or int(e.get("streak") or 0) >= 1:
        return False
    age = _age_h(e.get("timestamp"), ctx.get("now") or datetime.now())
    return age is not None and 0 <= age <= max_age_h


def feed_price(sid: str, cost: float, tcat, pid_int: int, default_price: float, ctx: dict):
    """→ (ціна, "") або (None, "noncompetitive"). Дзеркало generate_prom_feed._build_xml для override Toysi, АЛЕ:
      • delist-запис (підтверджено неконкурентний) молодший за пам'ять noncomp_memory_h() (45 діб) → (None, "noncompetitive"): fail-closed, також при битому timestamp. У пулі (ctx["for_pool"]) такий запис
        повертається, коли минуло NONCOMP_RECHECK_H (7 діб) — на переперевірку;
      • ціна НІКОЛИ не вища за default_price (cost×1,75 «без конкурента») — дорогий хибний збіг не піднімає ціну;
      • нижня межа: floor (канонічний за свіжим конкурентом ≤30 год, інакше division-floor 3 %) обмежений default_price, але НЕ нижче min_safe (net ≥ собівартість+10 ₴): floor = max(min(floor, default), min_safe)."""
    import competitor_pricing as cp
    now = ctx.get("now") or datetime.now()
    e = (ctx.get("items") or {}).get(sid)
    if not isinstance(e, dict):
        return default_price, ""
    age = _age_h(e.get("timestamp"), now)
    if e.get("action") == "delist":
        if age is None or age <= noncomp_memory_h():
            if ctx.get("for_pool") and age is not None and (age >= NONCOMP_RECHECK_H or (int(e.get("ret_streak") or 0) >= 1 and age >= STREAK_GAP_H)):
                return default_price, ""
            return None, "noncompetitive"
    if age is None:
        return default_price, ""
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
    retail = min(retail, default_price)
    commission = cp.compute_total_commission(PLATFORM, tcat, retail, prom_category_id=pid_int)
    cost_eff = cp.prom_effective_cost(cost)
    if comp:
        floor = cp.canonical_competitor_floor(cost_eff, round(comp - cp.PRICE_STEP, 2), commission)
    else:
        floor = cp.compute_floor(cost_eff, commission, cp.MIN_PROFIT_COMPETITOR_FLOOR)
    min_safe = round(cost_eff / (1 - commission), 2) if commission < 1 else 0.0
    floor = max(min(floor, default_price), min_safe)
    if retail < floor:
        retail = math.ceil(floor * 100 - 1e-6) / 100
    return retail, ""


# ───────────────────────────── партія (ротація) ─────────────────────────────
def pick_batch(pool_ids, items: dict, limit: int, now: datetime | None = None, refresh_share: bool = True) -> list:
    """«Ніколи не перевірені» першими, але не більше limit − limit//4: чверть партії ГАРАНТОВАНО — найстарішим перевіреним (refresh_share=False — без цієї квоти: режим observe, де оновлювати нічого,
    а покриття пулу важливіше). Підтверджено неконкурентні (action=delist) беруться лише коли минуло NONCOMP_RECHECK_H від останньої перевірки АБО вони чекають другого підтвердження повернення
    (ret_streak ≥ 1) і минуло STREAK_GAP_H. Не вистачає перевірених — слоти добираються новими. Порядок пулу стабільний."""
    now = now or datetime.now()

    def last(sid):
        return (items.get(sid) or {}).get("timestamp") or ""

    def noncomp_recent(sid):
        e = items.get(sid) or {}
        if e.get("action") != "delist":
            return False
        age = _age_h(e.get("timestamp"), now)
        if age is not None and int(e.get("ret_streak") or 0) >= 1 and age >= STREAK_GAP_H:
            return False
        return age is None or age < NONCOMP_RECHECK_H

    ids = [s for s in pool_ids if not noncomp_recent(s)]
    never = [s for s in ids if not last(s)]
    checked = [s for s in ids if last(s)]
    nb = never[: limit - (limit // 4 if refresh_share else 0)]
    rb = sorted(checked, key=last)[: max(0, limit - len(nb))]
    out = nb + rb
    if len(out) < limit:
        out += never[len(nb): len(nb) + limit - len(out)]
    return out


def observe_seen() -> dict:
    """{rt-id: {"timestamp": остання мітка в observe-журналі}} (поточний файл + .1): ротація БЕЗ стану для режиму observe/--dry (стан у observe не пишеться)."""
    out = {}
    for path in (OBSERVE_FILE.with_suffix(OBSERVE_FILE.suffix + ".1"), OBSERVE_FILE):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        sid, at = r["id"], r["at"]
                    except (ValueError, KeyError, TypeError):
                        continue
                    if at > (out.get(sid) or {}).get("timestamp", ""):
                        out[sid] = {"timestamp": at}
        except OSError:
            continue
    return out


def merged_for_rotation(items: dict) -> dict:
    """Стан репрайсера + observe-журнал для вибору партії: по кожному id — пізніша з двох міток."""
    seen = observe_seen()
    out = dict(items)
    for sid, v in seen.items():
        cur = out.get(sid)
        if cur is None:
            out[sid] = v
        elif v["timestamp"] > (cur.get("timestamp") or ""):
            out[sid] = dict(cur, timestamp=v["timestamp"])
    return out


def _append_observe(records: list) -> None:
    """Додає рядки в observe-журнал; перед цим, якщо рядків уже ≥ OBSERVE_MAX_LINES, поточний файл → .jsonl.1 (замінюючи попередній)."""
    OBSERVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(OBSERVE_FILE, encoding="utf-8") as f:
            n = sum(1 for _ in f)
    except OSError:
        n = 0
    if n + len(records) > OBSERVE_MAX_LINES and n > 0:
        os.replace(OBSERVE_FILE, OBSERVE_FILE.with_suffix(OBSERVE_FILE.suffix + ".1"))
    with open(OBSERVE_FILE, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ───────────────────────────── серія/гістерезис ─────────────────────────────
def advance(prev: dict, raw: str, now: datetime) -> dict:
    """Чиста функція переходу стану одного rt-id. raw — «сире» рішення цього прогону ("adjust"|"delist"). → {action, streak, streak_at, ret_streak, ret_at, status_at, wants_delist, held}.
    • delist-рішення зараховується в серію лише з інтервалом ≥ STREAK_GAP_H від попереднього зарахованого; серія старша STREAK_MAX_GAP_H або перервана adjust починається заново;
    • зняття (action=delist) — коли streak ≥ STREAK_MIN (wants_delist; остаточне слово за капами в run_phase) і картка не була змінена за останні HYSTERESIS_H;
    • повернення (adjust після delist) СИМЕТРИЧНЕ зняттю: не раніше HYSTERESIS_H після зняття І після STREAK_MIN adjust-рішень поспіль з інтервалом ≥ STREAK_GAP_H (ret_streak); до того action=delist (held=True);
      delist-рішення під час очікування повернення скидає ret_streak."""
    prev_action = prev.get("action") or "adjust"
    status_at = prev.get("status_at")
    st_age = _age_h(status_at, now) if status_at else None
    recent_change = st_age is not None and st_age < HYSTERESIS_H
    now_iso = now.isoformat()
    streak, streak_at = int(prev.get("streak") or 0), prev.get("streak_at")
    ret_streak, ret_at = int(prev.get("ret_streak") or 0), prev.get("ret_at")
    held = False
    wants = False
    if raw == "adjust":
        streak, streak_at = 0, None
        if prev_action == "delist":
            if recent_change:
                action, held, ret_streak, ret_at = "delist", True, 0, None
            else:
                ra = _age_h(ret_at, now) if ret_at else None
                if ret_streak >= 1 and ra is not None and ra <= STREAK_MAX_GAP_H:
                    if ra >= STREAK_GAP_H:
                        ret_streak, ret_at = ret_streak + 1, now_iso
                else:
                    ret_streak, ret_at = 1, now_iso
                if ret_streak >= STREAK_MIN:
                    action, status_at, ret_streak, ret_at = "adjust", now_iso, 0, None
                else:
                    action, held = "delist", True
        else:
            action, ret_streak, ret_at = "adjust", 0, None
    else:
        ret_streak, ret_at = 0, None
        if prev_action == "delist":
            action = "delist"
        else:
            sa = _age_h(streak_at, now) if streak_at else None
            if streak >= 1 and sa is not None and sa <= STREAK_MAX_GAP_H:
                if sa >= STREAK_GAP_H:
                    streak, streak_at = streak + 1, now_iso
            else:
                streak, streak_at = 1, now_iso
            action = "adjust"
            if streak >= STREAK_MIN:
                if recent_change:
                    held = True
                else:
                    wants = True
    return {"action": action, "streak": streak, "streak_at": streak_at, "ret_streak": ret_streak, "ret_at": ret_at, "status_at": status_at, "wants_delist": wants, "held": held}


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
def _resolve_links(batch, links, listed, net, now_iso, now, stats, deadline=None):
    """own_link (prom_id+urlText) для rt-id партії, які ВЖЕ були в фіді (журнал) і яких ще нема в links; пакетний GET /products/by_external_id + _resolve_url_text. Не знайдено → absent_at
    (повтор не раніше LINK_RETRY_H). Бюджет: deadline (time.monotonic) перевіряється перед пакетом і між посиланнями."""
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
    if not todo or (deadline is not None and time.monotonic() >= deadline):
        return
    found, indeterminate = net.fetch_by_ext_ids(set(todo))
    for sid in todo:
        if deadline is not None and time.monotonic() >= deadline:
            stats["link_budget_stop"] = 1
            return
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


def _apply_rt_price_rules(decision: dict, p: dict) -> dict:
    """Правила ціни rt- поверх decide_action: (1) «floor» (конкурент нижчий за нашу нижню межу) — division-floor 3 % (compute_floor), не break-even; (2) ціна ≤ default «без конкурента» (cost×1,75)."""
    import competitor_pricing as cp
    cost, tcat, pid = p["cost"], p["tcat"], p["pid_int"]
    default = cp.decide_price_for_platform(cost, None, PLATFORM, tcat, prom_category_id=pid)["price"]
    price = decision["price"]
    if decision["category"] == "floor":
        cost_eff = cp.prom_effective_cost(cost)
        for _ in range(3):
            comm = cp.compute_total_commission(PLATFORM, tcat, price, prom_category_id=pid)
            price = max(price, round(cp.compute_floor(cost_eff, comm, cp.MIN_PROFIT_COMPETITOR_FLOOR), 2))
    if price > default:
        price = default
        decision["capped_default"] = True
    comm = cp.compute_total_commission(PLATFORM, tcat, price, prom_category_id=pid)
    cost_eff = cp.prom_effective_cost(cost)
    min_safe = round(cost_eff / (1 - comm), 2) if comm < 1 else 0.0
    price = max(price, min_safe)
    decision["price"] = round(price, 2)
    decision["margin_pct"] = round((price * (1 - comm) - cost_eff) / cost_eff * 100, 1) if cost_eff else 0.0
    return decision


def _default_price(p: dict) -> float:
    import competitor_pricing as cp
    return cp.decide_price_for_platform(p["cost"], None, PLATFORM, p["tcat"], prom_category_id=p["pid_int"])["price"]


def _margin_at(p: dict, price: float) -> float:
    import competitor_pricing as cp
    comm = cp.compute_total_commission(PLATFORM, p["tcat"], price, prom_category_id=p["pid_int"])
    cost_eff = cp.prom_effective_cost(p["cost"])
    return round((price * (1 - comm) - cost_eff) / cost_eff * 100, 1) if cost_eff else 0.0


def decide_one(p: dict, own_link, net):
    """Рішення для одного rt-id тими самими функціями, що й Toysi + СТРОГИЙ гейт назви (strict_name_conflict: не-buyBox конкурент із іншими числовими токенами відкидається ЦІЛКОМ — ні ціна, ні зняття)
    + правила ціни rt- (_apply_rt_price_rules). → (decision, competitor_raw)."""
    competitor = net.find_best_competitor(p["name"], p["cost"], own_link, p["pictures"])
    raw_comp = competitor
    rejected = False
    if competitor and competitor.get("source") != "buybox" and strict_name_conflict(p["name"], competitor.get("name") or ""):
        competitor, rejected = None, True
    decision = net.decide_action(p["cost"], competitor, p["tcat"], p["name"], p["pid_int"], p["pictures"])
    net.sleep(net.search_delay)
    if decision["action"] == "delist" and (decision.get("competitor") or {}).get("source") != "buybox":
        if not net.verify(decision["competitor"]):
            decision["action"] = "adjust"
        net.sleep(net.search_delay)
    if decision["action"] == "delist" and net.commission_is_default(p["tcat"], p["pid_int"]):
        decision["action"] = "adjust"
    decision["strict_rejected"] = rejected
    return _apply_rt_price_rules(decision, p), raw_comp


def _prepare(catalog, now, net, time_exceeded=None, require_gates=True):
    """→ (pool, state, status, skip) де skip — dict причини або None. Спільне для run_phase і CLI."""
    import rt_prom
    if require_gates and not rt_prom.gate_on():
        print("[rt_reprice] PROM_RT_IN_FEED не ввімкнено — rt- у фіді нема, репрайсинг пропущено.")
        return None, None, None, {"skipped": "rt_not_in_feed"}
    if not catalog or len(catalog) < rt_prom.MIN_CATALOG_N:
        print("[rt_reprice] каталог Toysi порожній/усічений — пропущено (двійники Toysi не перевірити).", file=sys.stderr)
        return None, None, None, {"skipped": "catalog"}
    if not rt_prom.supplier_fresh():
        print("[rt_reprice] supplier_rt.json застарів — пропущено.", file=sys.stderr)
        return None, None, None, {"skipped": "supplier_stale"}
    sel = rt_prom.load_selection()
    if not sel or not rt_prom.selection_fresh(sel):
        print("[rt_reprice] відбір rt_prom відсутній/застарілий — пропущено.", file=sys.stderr)
        return None, None, None, {"skipped": "selection_stale"}
    if time_exceeded and time_exceeded():
        print("[rt_reprice] бюджет часу Toysi-циклу вичерпано — rt- пропущено цього прогону.")
        return None, None, None, {"skipped": "toysi_budget"}
    state, status = load_state_status()
    pool = rt_prom.repricer_pool(catalog, sel, rt_prom.load_supplier(), reprice={"items": state["items"], "now": now})
    return pool, state, status, None


def _decide_batch(batch, pool, links, net, deadline, time_exceeded, stats, verbose=True):
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
            decision, raw_comp = decide_one(p, own_link, net)
        except Exception as e:  # noqa: BLE001
            stats["error"] += 1
            print(f"[rt_reprice] {sid}: збій рішення ({type(e).__name__}: {str(e)[:120]}) — пропущено", file=sys.stderr)
            continue
        if not decision["price"] or decision["price"] < p["cost"]:
            stats["skip_price_below_cost"] += 1
            continue
        if decision.get("strict_rejected"):
            stats["strict_rejected"] += 1
        results.append((sid, decision, raw_comp))
        if verbose:
            c = decision.get("competitor")
            print(f"{sid}\tcost={p['cost']:.0f}\tprice={decision['price']:.0f}\tmargin={decision['margin_pct']:.1f}%\t[{decision['action']}/{decision['category']}]\t"
                  + (f"конкурент {c['price']:.0f} (score={c['score']:.2f}, {c.get('source') or 'search'})" if c else "конкурента нема" + (" (відкинуто строгим гейтом назви)" if decision.get("strict_rejected") else "")))
    return results


def _observe_record(sid, p, decision, raw_comp, now_iso) -> dict:
    return {"at": now_iso, "id": sid, "our_name": p["name"], "competitor_name": (raw_comp or {}).get("name"), "competitor_price": (raw_comp or {}).get("price"), "score": (raw_comp or {}).get("score"),
            "source": (raw_comp or {}).get("source") or ("search" if raw_comp else None), "strict_rejected": bool(decision.get("strict_rejected")), "raw_action": decision["action"],
            "category": decision["category"], "price": decision["price"], "margin_pct": decision["margin_pct"]}


def run_phase(catalog: dict, apply: bool = False, *, net=None, now: datetime | None = None, time_exceeded=None, notify: bool = True) -> dict:
    """Одна партія репрайсингу rt-. PROM_RT_REPRICE=observe → рішення лише в OBSERVE_FILE (за apply), стан/фід не міняються; =1 → застосовує (за apply). apply=False → лише розрахунок і друк."""
    import rt_prom
    stats = Counter()
    m = mode()
    if m == "off":
        return {"skipped": "gate_off"}
    now = now or datetime.now()
    now_iso = now.isoformat()
    net = net or default_net()
    pool, state, status, skip = _prepare(catalog, now, net, time_exceeded)
    if skip:
        return skip
    if status in ("lost", "unreadable"):
        _alert("state_lost", f"стан {STATE_FILE.name} пошкоджений без резервної копії — серії/гістерезис/пам'ять неконкурентних обнулено, фаза продовжує з порожнього стану.")
    items, links = state["items"], state["links"]
    batch = pick_batch(list(pool), merged_for_rotation(items) if m == "observe" else items, batch_size(), now, refresh_share=(m != "observe"))
    print(f"[rt_reprice] режим {m.upper()}{' (apply)' if apply else ' (DRY-RUN)'}; пул {len(pool)} rt-id (≤{rt_prom.rt_hard_max()}); партія {len(batch)} (≈{estimate_minutes(len(batch)):.0f} хв за {SEC_PER_SKU_REALISTIC:.1f} с/SKU).")
    listed = rt_prom.load_listed()
    deadline = time.monotonic() + run_budget_s()
    _resolve_links(batch, links, listed, net, now_iso, now, stats, deadline)
    results = _decide_batch(batch, pool, links, net, deadline, time_exceeded, stats)
    checked = len(results)
    raw_delist = [r for r in results if r[1]["action"] == "delist"]
    breaker = checked >= BREAKER_MIN_CHECKED and len(raw_delist) / checked >= BREAKER_DELIST_SHARE
    if breaker:
        stats["breaker_blocked_delist"] = len(raw_delist)
        print(f"[rt_reprice] BREAKER: {len(raw_delist)}/{checked} ({len(raw_delist) / checked:.0%}) delist ≥ {BREAKER_DELIST_SHARE:.0%} — зняття заблоковано цілим прогоном (схоже на збій пошуку/buyBox).", file=sys.stderr)
    if m == "observe":
        stats["checked"] = checked
        if apply:
            try:
                _append_observe([_observe_record(sid, pool[sid], decision, raw_comp, now_iso) for sid, decision, raw_comp in results])
            except OSError as e:
                print(f"[rt_reprice] не вдалось записати {OBSERVE_FILE}: {e}", file=sys.stderr)
        print(f"[rt_reprice] OBSERVE: перевірено {checked}, сирих delist {len(raw_delist)}, відкинуто строгим гейтом назви {stats['strict_rejected']}; стан/ціни/фід НЕ змінено.")
        return dict(stats)
    # режим «1»: серія + гістерезис + капи
    wants, new_items = [], {}
    for sid, decision, _rc in results:
        raw = "adjust" if (breaker and decision["action"] == "delist") else decision["action"]
        adv = advance(items.get(sid) or {}, raw, now)
        if breaker and decision["action"] == "delist":
            adv["streak"], adv["streak_at"] = int((items.get(sid) or {}).get("streak") or 0), (items.get(sid) or {}).get("streak_at")
        if adv["held"]:
            stats["held_hysteresis"] += 1
        new_items[sid] = {"price": decision["price"], "timestamp": now_iso, "action": adv["action"], "category": decision["category"], "competitor_price": decision["competitor_price"],
                          "competitor_key": net.identity(decision.get("competitor")), "cost": pool[sid]["cost"], "margin_pct": decision["margin_pct"], "streak": adv["streak"],
                          "streak_at": adv["streak_at"], "ret_streak": adv["ret_streak"], "ret_at": adv["ret_at"], "status_at": adv["status_at"], "held": adv["held"]}
        if adv["wants_delist"]:
            wants.append((sid, decision))
        if decision.get("competitor") is None:
            stats["no_competitor"] += 1
    today = sum(1 for s, e in items.items() if e.get("action") == "delist" and (_age_h(e.get("status_at"), now) or 1e9) < 24 and s not in new_items)
    room_day = max(0, daily_cap(len(pool)) - today)
    wants.sort(key=lambda r: (r[1]["price"] / r[1]["competitor_price"]) if r[1].get("competitor_price") else 0.0, reverse=True)
    allowed = [sid for sid, _d in wants[: min(MAX_DELIST_PER_RUN, room_day)]]
    stats["delist_deferred_by_cap"] = max(0, len(wants) - len(allowed))
    for sid in allowed:
        new_items[sid]["action"], new_items[sid]["status_at"] = "delist", now_iso
    # ціна НЕ падає до division-floor, поки зняття не підтверджено: для сирого delist без остаточного зняття (перша серія, відкладено капом/гістерезисом, breaker) тримаємо попередню ціну
    # (або default, якщо попередньої дійсної нема) — інакше ще активна картка недоотримувала б маржу на непідтвердженому (можливо хибному) конкуренті.
    for sid, decision, _rc in results:
        if decision["action"] == "delist" and new_items[sid]["action"] != "delist":
            prev_e = items.get(sid) or {}
            try:
                pp = float(prev_e.get("price"))
            except (TypeError, ValueError):
                pp = 0.0
            dflt = _default_price(pool[sid])
            hold = min(pp, dflt) if pp > 0 and prev_e.get("action") != "delist" else dflt
            new_items[sid]["price"] = round(hold, 2)
            new_items[sid]["margin_pct"] = _margin_at(pool[sid], hold)
            stats["held_price"] += 1
    for sid, e in new_items.items():
        if e["action"] == "delist" and e.get("status_at") == now_iso:
            stats["noncompetitive_new"] += 1
    stats["noncompetitive"] = sum(1 for e in new_items.values() if e["action"] == "delist")
    stats["adjust"] = checked - stats["noncompetitive"]
    stats["checked"] = checked
    stats["pending_streak"] = sum(1 for e in new_items.values() if e["action"] != "delist" and e.get("streak", 0) >= 1)
    print(f"[rt_reprice] підсумок: перевірено {checked}; з цінами {stats['adjust']}; неконкурентні (знімаються з емісії) {stats['noncompetitive']} (нових {stats['noncompetitive_new']}); "
          f"в серії (чекають 2-го delist) {stats['pending_streak']}; без конкурента {stats['no_competitor']}; відкинуто строгим гейтом {stats['strict_rejected']}; помилок {stats['error']}; відкладено капом {stats['delist_deferred_by_cap']}.")
    if apply:
        items.update(new_items)
        for sid in [s for s in items if s not in pool and (_age_h(items[s].get("timestamp"), now) or 0) > PRUNE_AFTER_H]:
            items.pop(sid, None)
        for sid in [s for s in links if s not in pool and s not in listed]:
            links.pop(sid, None)
        state["_meta"] = {"last_run": now_iso, "pool": len(pool), "checked": checked, "noncompetitive": stats["noncompetitive"]}
        save_state(state)
        if notify and (stats["noncompetitive_new"] or stats["breaker_blocked_delist"]):
            try:
                net.notify(f"💰 rt_reprice (--apply, feed-only): rt- перевірено {checked}, нових зняттів з емісії {stats['noncompetitive_new']} (всього неконкурентних {stats['noncompetitive']})"
                           + (f", ЗАБЛОКОВАНО breaker'ом {stats['breaker_blocked_delist']}" if stats["breaker_blocked_delist"] else "") + ".")
            except Exception as e:  # noqa: BLE001
                print(f"[rt_reprice] сповіщення не надіслано ({type(e).__name__})", file=sys.stderr)
    else:
        print("[rt_reprice] DRY-RUN: стан не записано.")
    return dict(stats)


# ───────────────────────────── CLI ─────────────────────────────
def main(argv=None, *, net=None, catalog=None, now: datetime | None = None) -> int:
    """`python rt_reprice.py --dry [--limit N]` — безпечний ПЕРЕГЛЯД: пари «наша назва | конкурент | score | рішення» і підсумки; НІЧОГО не пише (ні стан, ні посилання, ні Prom, ні observe-файл). Каталог Toysi береться
    лише для перевірки двійників (без Toysi-репрайсера). Не потребує брами PROM_RT_REPRICE. Читає Prom (пошук/buyBox) — лише GET."""
    ap = argparse.ArgumentParser(description="rt_reprice: перегляд рішень репрайсера для rt- без запису")
    ap.add_argument("--dry", action="store_true", help="обов'язковий прапорець: лише перегляд, без запису")
    ap.add_argument("--limit", type=int, default=30, help="скільки rt-id переглянути (за ротацією), за замовчуванням 30")
    ap.add_argument("--offset", type=int, default=None, help="взяти rt-id за позиціями пулу [offset, offset+limit) замість ротації (оглянути різні частини пулу без стану)")
    a = ap.parse_args(argv)
    if not a.dry:
        ap.print_help()
        print("\nrt_reprice.py працює лише з --dry (застосування — фаза prom_competitor_pricer за брамою PROM_RT_REPRICE).")
        return 2
    now = now or datetime.now()
    if catalog is None:
        from parser import fetch_toysi_catalog, assert_catalog_size_sane
        catalog = fetch_toysi_catalog()
        assert_catalog_size_sane(catalog)
    net = net or default_net()
    stats = Counter()
    pool, state, status, skip = _prepare(catalog, now, net, require_gates=False)
    if skip:
        print(f"[rt_reprice --dry] {skip}")
        return 1
    import rt_prom
    if a.offset is not None:
        batch = list(pool)[max(0, a.offset): max(0, a.offset) + max(1, a.limit)]
    else:
        batch = pick_batch(list(pool), merged_for_rotation(state["items"]), max(1, a.limit), now, refresh_share=False)      # ротація за станом і observe-журналом (--dry сам нічого не пише)
    links = dict(state["links"])
    deadline = time.monotonic() + run_budget_s()
    _resolve_links(batch, links, rt_prom.load_listed(), net, now.isoformat(), now, stats, deadline)      # лише в пам'яті
    results = _decide_batch(batch, pool, links, net, deadline, None, stats, verbose=False)
    print(f"\n{'наша назва':50} | {'конкурент':50} | score | рішення")
    delist = 0
    for sid, decision, raw in results:
        c = raw or {}
        if decision["action"] == "delist":
            delist += 1
        verdict = f"{decision['action']}/{decision['category']} {decision['price']:.0f} ₴ (маржа {decision['margin_pct']:.1f}%)" + (" [відкинуто строгим гейтом назви]" if decision.get("strict_rejected") else "")
        print(f"{pool[sid]['name'][:50]:50} | {(c.get('name') or '—')[:50]:50} | {('%.2f' % c['score']) if c.get('score') is not None else '  — ':5} | {sid} {verdict}")
    print(f"\nпідсумок: переглянуто {len(results)} з пулу {len(pool)}; сирих delist {delist}; відкинуто строгим гейтом {stats['strict_rejected']}; без конкурента {sum(1 for r in results if not r[1].get('competitor'))}; "
          f"помилок {stats['error']}. Нічого не записано.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
