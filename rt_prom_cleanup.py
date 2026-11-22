#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rt_prom_cleanup.py — автоматичне прибирання ОСИРОТІЛИХ rt-карток RoyalToys у кабінеті Prom (рішення власника 22.11.2026: «роби без ручної роботи»).

ПРОБЛЕМА. rt_prom.py додає в Prom-фід товари RoyalToys (offer id = `rt-<id>`). Ті, що випали з відбору/фіду, лишаються в кабінеті «Опубліковано / Немає в наявності» й займають слоти тарифу
(6000): prom_catalog_sync.find_stale_external_ids чистить лише ext_id із Toysi-id, prom_pushed_ledger rt- не містить. rt_prom.append_rt_offers тому віддає для «осиротілих» заглушки available="false"
і віднімає їх від ємності — але видалити їх могла лише людина вручну.

ЩО РОБИТЬ. Кандидат на видалення = rt-id з журналу rt_prom_listed_state.json, який ОДНОЧАСНО:
  (а) НЕ в активній емісії поточного фіду (feeds/prom_feed_top.xml: offer available="true"); фід свіжий (≤14 год), цілий, містить активні rt- (доказ, що RT-хук у цьому фіді відпрацював);
  (б) осиротілий НЕПЕРЕРВНО ≥ 48 год (мітка `orphan_since` у знімку журналу ставиться rt_prom.append_rt_offers при першому прогоні, де id не активний; активація скидає мітку) — тимчасове зникнення
      залишку/двійника не вбиває картку; невалідна/відсутня мітка = НЕ кандидат;
  (в) ЖИВА звірка в кабінеті (GET /products/by_external_id/{id} — той самий шлях, що в find_stale_via_ledger): картка існує, не deleted, external_id збігається, Prom сам показує її
      недоступною (presence не «available» і quantity_in_stock порожній), і за останні 60 днів НЕМАЄ жодного не-скасованого замовлення з цією позицією (GET /orders/list: products[].external_id/sku/id);
  (г) ≤ 50 видалень за прогін (env PROM_RT_CLEANUP_MAX, стеля 200) і ≤ 150 за добу (за мітками часу надгробків); мас-запобіжник БЕЗ абсолютного мінімуму: кандидатів >50% журналу при будь-якому розмірі
      журналу ≥4 → нічого (ймовірний збій відбору, а не природне випадіння), для журналу <4 — не більше половини записів; кожен прогін із видаленнями дає сповіщення (кількість + перші 10 id) і рядок у reports/rt_prom_cleanup_log.jsonl;
  (д) лише id виду `rt-…`: Toysi-id та будь-який інший id недоторкані структурно.
Видалення — ІСНУЮЧИЙ prom_catalog_sync.deactivate → prom_api_client.delist (POST /products/edit_by_external_id status=deleted ПО ОДНОМУ з живою GET-звіркою результату). Після підтвердженого видалення:
надгробок у rt_prom_deleted_state.json (rt_prom більше не віддає цей id у фід — інакше Prom міг би «відновити» видалене) і прибирання id з журналу (атомарно) → слоти звільняються в обліку.
Надгробок діє 14 діб (потім id може знову емітуватись; `--untomb <id>` знімає вручну). Надгробки молодші за 3 доби перевіряються ще раз (картка раптом ожила через старий опублікований фід → видаляється повторно, ті самі запобіжники).
Картка, яку Prom відхиляє 3 прогони поспіль, виключається з кандидатів до наступного дня (rt_prom_cleanup_stuck_state.json) й дає алерт — не їсть кап.

БРАМА І РЕЖИМИ. Реально видаляє лише `PROM_RT_CLEANUP=1` (ЗА ЗАМОВЧУВАННЯМ ВИМКНЕНО) І apply=True. CLI: `python rt_prom_cleanup.py` = --dry (лише перелік; читає API, нічого не пише), `--apply` потребує брами.
Fail-closed: журнал відсутній/пошкоджений, фід відсутній/старий/битий/без активних rt-, збій отримання замовлень, ≥10% звірок невизначені, мас-запобіжник → НІЧОГО не видаляємо й алерт (Telegram, тротлінг 12 год).
Підключення: prom_catalog_sync.main() викликає run() (той самий таймер prom-catalog-sync 4×/день); нових systemd-юнітів нема."""
import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import requests

import rt_prom

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
FEED_FILE = Path(os.environ.get("RT_PROM_FEED_FILE", str(HERE / "feeds" / "prom_feed_top.xml")))
PROM_API_URL = "https://my.prom.ua/api/v1"
REQUEST_TIMEOUT = 30

RT_ID_RE = re.compile(r"rt-[A-Za-z0-9][A-Za-z0-9_.-]*")      # лише fullmatch (не match/«$»: пропускали б хвостовий символ нового рядка)
MIN_ORPHAN_HOURS = 48.0
MIN_ORPHAN_HOURS_FLOOR = 24.0          # env PROM_RT_CLEANUP_MIN_HOURS не може опустити поріг нижче цього
MAX_PER_RUN = 50
MAX_PER_RUN_ABS = 200
LOOKUP_LIMIT = 150                     # звірок by_external_id за прогін (кандидати) — із запасом над капом, бо частина відсіється (замовлення/доступна)
RECHECK_LIMIT = 100
RECHECK_DAYS = 3
FEED_MAX_AGE_H = 14.0                  # feed-pipeline стартує кожні 6 год і біжить 3–4,5 год → свіжий фід не старший за ~10 год
ORDER_LOOKBACK_DAYS = 60
ORDER_PAGE = 100
ORDER_MAX_PAGES = 200
MASS_GUARD_FRACTION = 0.5
MASS_GUARD_MIN_JOURNAL = 4             # журнал <4 записів: «масового» ефекту нема, але кап ≤ половини записів
DAILY_CAP = 150                        # стеля видалень за добу (мітки часу надгробків)
STUCK_STATE_FILE = HERE / "rt_prom_cleanup_stuck_state.json"
STUCK_THRESHOLD = 3                    # N прогонів поспіль, коли Prom відхиляє видалення → виключаємо до наступного дня
LOG_FILE = HERE / "reports" / "rt_prom_cleanup_log.jsonl"
INDETERMINATE_MAX_FRACTION = 0.10
_AVAILABLE_PRESENCE = ("available", "avail", "true", "1")   # значення presence, що означають «картка продається» (prom_chat_bot.py:593, export_catalog_scorecard.py:109)
_UNAVAILABLE_PRESENCE = ("not_available", "not_avail")      # ЯВНИЙ список «недоступна»: `presence=not_avail` — фільтр кабінету в prom_catalog_sync.py:535 / prom_pushed_ledger.py:10, `not_available` — значення публічного Prom API. Будь-що інше (None/відсутнє/«order»/невідоме) → НЕ видаляємо


class CleanupAbort(Exception):
    """Прибирання цього прогону зупинене (fail-closed): нічого не видалено."""


def gate_on() -> bool:
    return os.environ.get("PROM_RT_CLEANUP") == "1"


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    v = os.environ.get(name, "").strip()
    return max(lo, min(int(v), hi)) if v.isdigit() else default


def cap_per_run() -> int:
    return _env_int("PROM_RT_CLEANUP_MAX", MAX_PER_RUN, 0, MAX_PER_RUN_ABS)


def min_orphan_hours() -> float:
    v = os.environ.get("PROM_RT_CLEANUP_MIN_HOURS", "").strip()
    return max(MIN_ORPHAN_HOURS_FLOOR, float(int(v))) if v.isdigit() else MIN_ORPHAN_HOURS


def _alert(key: str, text: str) -> None:
    """Telegram-алерт із тротлінгом 12 год (best-effort: збій алерту не валить прогін)."""
    print(f"[rt_prom_cleanup] {text}", file=sys.stderr)
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert("rt_prom_cleanup_" + key, "⚠️ rt_prom_cleanup: " + text, cooldown_sec=12 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[rt_prom_cleanup] алерт не надіслано: {type(e).__name__}", file=sys.stderr)


def _notify(text: str) -> None:
    """Сповіщення про прогін із видаленнями: КОЖЕН прогін окремим повідомленням (без тротлінгу) через send_telegram_message → завжди пишеться в reports/telegram_alerts.md (читає health_check),
    а в Telegram іде, якщо дозволяє сито telegram_triage (режим «лише замовлення» його приглушує — виняток у сито потребує рішення власника, тут НЕ додається)."""
    print(f"[rt_prom_cleanup] {text}", file=sys.stderr)
    try:
        from telegram_notify import send_telegram_message
        send_telegram_message("⚠️ rt_prom_cleanup (увага, необоротні видалення карток): " + text)
    except Exception as e:  # noqa: BLE001
        print(f"[rt_prom_cleanup] сповіщення не надіслано: {type(e).__name__}", file=sys.stderr)


def _log_run(now: datetime, ids: list, extra: dict) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now.isoformat(timespec="seconds"), "deleted": ids, **extra}, ensure_ascii=False) + chr(10))
    except OSError as e:
        print(f"[rt_prom_cleanup] журнал прогону не записано: {e}", file=sys.stderr)


def _load_stuck() -> dict:
    try:
        d = json.loads(STUCK_STATE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_stuck(st: dict) -> None:
    try:
        rt_prom._atomic_write(STUCK_STATE_FILE, st)
    except OSError as e:
        print(f"[rt_prom_cleanup] не вдалось зберегти {STUCK_STATE_FILE.name}: {e}", file=sys.stderr)


def _stuck_blocked(st: dict, now: datetime) -> set:
    """id, виключені з кандидатів до наступного дня (skip_until у майбутньому)."""
    out = set()
    for i, e in st.items():
        u = _parse_iso((e or {}).get("skip_until")) if isinstance(e, dict) else None
        if u is not None and u > now:
            out.add(i)
    return out


def _update_stuck(st: dict, errors: dict, processed: list, now: datetime) -> tuple:
    """Лічильник «Prom відхиляє видалення» (як _update_stuck_state_and_alert для Toysi). Успіх скидає. Повертає (стан, щойно застряглі id)."""
    for i in processed:
        st.pop(i, None)
    newly = []
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    for i in errors:
        e = st.get(i) if isinstance(st.get(i), dict) else {}
        e["fails"] = int(e.get("fails", 0)) + 1
        if e["fails"] >= STUCK_THRESHOLD:
            e["skip_until"] = nxt.isoformat(timespec="seconds")
            newly.append(i)
        st[i] = e
    return st, newly


def _parse_iso(s):
    try:
        return datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


# ───────────────────────── (а) фід ─────────────────────────
def read_feed_rt(path: Path | None = None, now: datetime | None = None) -> tuple:
    """→ (active_ids, all_rt_ids) із поточного prom_feed_top.xml. Відсутній / старший за FEED_MAX_AGE_H / не розбирається (обрізаний) / без жодного АКТИВНОГО rt- → CleanupAbort."""
    path = Path(path or FEED_FILE)
    now = now or datetime.now()
    try:
        age_h = (now - datetime.fromtimestamp(path.stat().st_mtime)).total_seconds() / 3600
    except OSError as e:
        raise CleanupAbort(f"фід {path.name} недоступний ({type(e).__name__})")
    if age_h > FEED_MAX_AGE_H:
        raise CleanupAbort(f"фід {path.name} застарів ({age_h:.1f} год > {FEED_MAX_AGE_H:g}) — стан активної емісії невідомий")
    active, allr = set(), set()
    try:
        for _ev, el in ET.iterparse(str(path), events=("end",)):
            if el.tag == "offer":
                oid = el.get("id") or ""
                if oid.startswith("rt-"):
                    allr.add(oid)
                    if el.get("available") == "true":
                        active.add(oid)
                el.clear()
    except (ET.ParseError, OSError) as e:
        raise CleanupAbort(f"фід {path.name} не розбирається ({type(e).__name__}) — можливо обрізаний")
    if not active:
        raise CleanupAbort(f"у фіді {path.name} нема жодного активного rt- — не доведено, що RT-хук відпрацював у цьому фіді (брама PROM_RT_IN_FEED вимкнена / збій)")
    return active, allr


# ───────────────────────── (а)(б)(д) кандидати: чиста функція ─────────────────────────
def select_candidates(listed: dict, active_in_feed: set, now: datetime, min_hours: float, deleted: dict | None = None) -> tuple:
    """→ (кандидати [найдавніші осиротілі першими], лічильник причин відсіву). Без мережі й файлів."""
    deleted = deleted or {}
    why = {"not_rt_id": 0, "active_in_feed": 0, "no_orphan_since": 0, "too_young": 0, "tombstoned": 0}
    out = []
    for sid, snap in listed.items():
        if not RT_ID_RE.fullmatch(str(sid)):
            why["not_rt_id"] += 1                    # Toysi-id та будь-який чужий id — недоторкані
            continue
        if sid in deleted:
            why["tombstoned"] += 1
            continue
        if sid in active_in_feed:
            why["active_in_feed"] += 1
            continue
        since = _parse_iso((snap or {}).get("orphan_since"))
        if since is None:
            why["no_orphan_since"] += 1              # мітки нема/невалідна → відлік ще не почато (старий журнал: мітка з'явиться з наступного прогону rt_prom)
            continue
        if (now - since).total_seconds() / 3600 < min_hours:
            why["too_young"] += 1
            continue
        out.append((since, sid))
    out.sort()
    return [sid for _s, sid in out], why


# ───────────────────────── (в) замовлення ─────────────────────────
def fetch_protected_keys(now: datetime | None = None, http_get=None, api_key: str | None = None) -> set:
    """Ключі (external_id / sku / id) позицій у НЕ скасованих замовленнях Prom за ORDER_LOOKBACK_DAYS (GET /orders/list, пагінація last_id — як orders_watcher.fetch_new_orders_prom).
    Будь-який збій / невалідна відповідь / вичерпано ліміт сторінок → CleanupAbort (fail-closed: «замовлень нема» не припускаємо)."""
    now = now or datetime.now()
    http_get = http_get or requests.get
    key = api_key if api_key is not None else os.environ.get("PROM_API_KEY", "")
    if not key:
        raise CleanupAbort("PROM_API_KEY не задано — замовлення перевірити неможливо")
    date_from = (now - timedelta(days=ORDER_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    keys, last_id = set(), None
    for _ in range(ORDER_MAX_PAGES):          # кінець — ЛИШЕ порожня сторінка (коротша за ORDER_PAGE ≠ кінець: Prom може віддавати менше за limit)
        params = {"date_from": date_from, "limit": ORDER_PAGE}
        if last_id is not None:
            params["last_id"] = last_id
        try:
            r = http_get(f"{PROM_API_URL}/orders/list", headers={"Authorization": f"Bearer {key}"}, params=params, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            page = r.json().get("orders")
        except (requests.exceptions.RequestException, ValueError, AttributeError) as e:
            raise CleanupAbort(f"не вдалось отримати замовлення Prom ({type(e).__name__}) — видалення без перевірки замовлень заборонене")
        if not isinstance(page, list):
            raise CleanupAbort("відповідь /orders/list без списку orders — видалення без перевірки замовлень заборонене")
        for o in page:
            if str((o or {}).get("status") or "") == "canceled":
                continue
            for p in (o or {}).get("products") or []:
                for k in ("external_id", "sku", "id"):
                    v = (p or {}).get(k)
                    if v not in (None, ""):
                        keys.add(str(v))
        if not page:
            return keys
        new_last = page[-1].get("id") if isinstance(page[-1], dict) else None
        if new_last is None or new_last == last_id:
            raise CleanupAbort("пагінація замовлень не просувається (нема id або він не змінився) — повного списку не маємо")
        last_id = new_last
    raise CleanupAbort(f"замовлень більше за {ORDER_MAX_PAGES * ORDER_PAGE} за {ORDER_LOOKBACK_DAYS} діб — повного списку не отримано")


def _product_blocker(rid: str, p: dict, protected: set) -> str | None:
    """Причина НЕ видаляти живу картку (або None). "presence_unknown" — presence відсутнє/невідоме: викликач трактує як indeterminate (не видаляє)."""
    ext = p.get("external_id")
    if ext not in (None, "") and str(ext) != rid:
        return "external_id_mismatch"
    pres = p.get("presence")
    if pres is True or str(pres).strip().lower() in _AVAILABLE_PRESENCE:
        return "presence_available"                  # Prom показує картку як таку, що продається — це не осиротіла заглушка
    if pres is None or str(pres).strip().lower() not in _UNAVAILABLE_PRESENCE:
        return "presence_unknown"                    # лише ЯВНО недоступна картка (_UNAVAILABLE_PRESENCE) може бути видалена
    q = p.get("quantity_in_stock")
    try:
        if q not in (None, "") and float(q) > 0:
            return "has_quantity"
    except (TypeError, ValueError):
        return "has_quantity"
    for k in (rid, p.get("sku"), p.get("id")):
        if k not in (None, "") and str(k) in protected:
            return "has_orders"
    return None


# ───────────────────────── головний прогін ─────────────────────────
def run(apply: bool, lookup, delist_many, now: datetime | None = None, feed_path=None, orders_fetch=None, allow_mass: bool = False) -> dict:
    """lookup(set_of_ids) → (found{id: product}, indeterminate{ids}) — prom_catalog_sync.fetch_prom_products_by_external_ids;
    delist_many(list_of_ids) → (processed[ids], errors{id: msg}) — prom_catalog_sync.deactivate (→ prom_api_client.delist ПО ОДНОМУ з живою GET-звіркою).
    Повертає словник-звіт; status: nothing | dry | applied | aborted | gate_off. Виняток назовні НЕ кидає (збій → aborted + алерт)."""
    now = now or datetime.now()
    res = {"status": "nothing", "apply": bool(apply)}
    if apply and not gate_on():
        res["status"] = "gate_off"
        return res
    try:
        listed, lst = rt_prom.load_listed_status()
        if lst != "ok":
            raise CleanupAbort(f"журнал {rt_prom.LISTED_FILE.name} {'відсутній' if lst == 'missing' else 'пошкоджений'} — нічого не видаляю")
        deleted, dst = rt_prom.load_deleted_status()
        if dst == "corrupt":
            raise CleanupAbort(f"файл надгробків {rt_prom.DELETED_FILE.name} пошкоджений — нічого не видаляю")
        recheck = [i for i, ts in deleted.items() if RT_ID_RE.fullmatch(i) and (_parse_iso(ts) is not None) and (now - _parse_iso(ts)) <= timedelta(days=RECHECK_DAYS)]
        if not listed and not recheck:
            return res
        active, _all = read_feed_rt(feed_path, now)
        cands, why = select_candidates(listed, active, now, min_orphan_hours(), deleted)
        res["candidates"], res["skipped"] = len(cands), why
        J = len(listed)
        if J >= MASS_GUARD_MIN_JOURNAL and len(cands) > MASS_GUARD_FRACTION * J and not allow_mass:
            raise CleanupAbort(f"осиротілих-кандидатів {len(cands)} із {J} в журналі (>{int(MASS_GUARD_FRACTION * 100)}%) — схоже на збій відбору, а не природне випадіння; потрібен огляд людиною (--allow-mass)")
        stuck = _load_stuck()
        stuck_skip = _stuck_blocked(stuck, now)
        if stuck_skip:
            res["stuck_skipped"] = len(stuck_skip & set(cands))
            cands = [c for c in cands if c not in stuck_skip]            # Prom відхиляє видалення ≥3 прогони поспіль → до наступного дня не їмо кап
        if not cands and not recheck:
            return res
        protected = (orders_fetch or fetch_protected_keys)()
        batch = cands[:LOOKUP_LIMIT]
        recheck = recheck[:RECHECK_LIMIT]
        found, indet = lookup(set(batch) | set(recheck))
        indet = set(indet)
        n_lookup = len(batch) + len(recheck)
        seen_presence = {}
        for _rid, _p in found.items():
            _k = repr((_p or {}).get("presence"))
            seen_presence[_k] = seen_presence.get(_k, 0) + 1
        res["presence_seen"] = seen_presence
        to_delete, gone, blocked = [], [], {}
        for rid in batch + recheck:
            if rid in indet:
                continue
            p = found.get(rid)
            if p is None:
                if rid in batch:
                    gone.append(rid)                 # 404: картки в кабінеті нема → лише прибрати з журналу
                continue
            if str(p.get("status")) == "deleted":
                if rid in batch:
                    gone.append(rid)
                continue
            why_not = _product_blocker(rid, p, protected)
            if why_not == "presence_unknown":
                indet.add(rid)                       # presence відсутнє/невідоме → не видаляємо, рахуємо як «невизначену» звірку
                continue
            if why_not:
                blocked[why_not] = blocked.get(why_not, 0) + 1
                continue
            if rid not in to_delete:
                to_delete.append(rid)
        res["indeterminate"], res["lookup"] = len(indet), n_lookup
        if n_lookup and len(indet) > INDETERMINATE_MAX_FRACTION * n_lookup:
            raise CleanupAbort(f"звірка з кабінетом Prom неповна/невпевнена: {len(indet)} із {n_lookup} невизначені (збій API або presence поза списком {_UNAVAILABLE_PRESENCE}; бачили {seen_presence}) (> {int(INDETERMINATE_MAX_FRACTION * 100)}%) — нічого не видаляю")
        def _age_ok(ts):
            t = _parse_iso(ts)
            return t is not None and (now - t) < timedelta(hours=24)
        deleted_24h = sum(1 for ts in deleted.values() if _age_ok(ts))
        daily_left = max(0, DAILY_CAP - deleted_24h)
        cap = min(cap_per_run(), daily_left)
        if J < MASS_GUARD_MIN_JOURNAL:
            cap = min(cap, J // 2)                   # малий журнал: не більше половини записів
        res.update(to_delete=len(to_delete), gone=len(gone), blocked=blocked, cap=cap, deleted_24h=deleted_24h, deferred=max(0, len(to_delete) - cap))
        to_delete = to_delete[:cap]
        print(f"[rt_prom_cleanup] кандидатів {len(cands)} (відсів: {why}), перевірених {n_lookup}, до видалення {len(to_delete)} (кап {cap}), уже відсутні в Prom {len(gone)}, заблоковані {blocked}")
        if not apply:
            res["status"] = "dry"
            res["list"] = to_delete + gone
            print(f"[rt_prom_cleanup] DRY: видалив би {len(to_delete)} (перші 10: {to_delete[:10]}); з журналу прибрав би ще {len(gone)} вже відсутніх")
            return res
        processed, errors = ([], {})
        if to_delete:
            processed, errors = delist_many(to_delete)
        done = list(dict.fromkeys(list(processed) + gone))
        res.update(status="applied", deleted=len(processed), errors=len(errors))
        stuck, newly = _update_stuck(stuck, errors, list(processed), now)
        stuck = {i: e for i, e in stuck.items() if i in listed}      # прунимо записи про id, яких уже нема в журналі
        _save_stuck(stuck)
        if newly:
            _alert("stuck", f"{len(newly)} rt-карток Prom відхиляє видалення {STUCK_THRESHOLD}+ прогони поспіль — виключено до наступного дня: {newly[:10]}")
        if done:
            if rt_prom.mark_deleted(done, now.isoformat(timespec="seconds"), overwrite=processed):
                n = rt_prom.remove_listed(done)
                res["journal_removed"] = n
                if n < 0:
                    _alert("journal", f"{len(done)} карток видалено, але прибрати їх із {rt_prom.LISTED_FILE.name} не вдалось — облік слотів неповний, надгробки є (повтор у наступному прогоні)")
            else:
                res["journal_removed"] = 0
                _alert("tombstone", f"{len(done)} карток видалено в Prom, але надгробки в {rt_prom.DELETED_FILE.name} не записано — з журналу НЕ прибираю (інакше фід відновить картки); перевір файл")
        print(f"[rt_prom_cleanup] видалено {len(processed)}, помилок {len(errors)}, прибрано з журналу {res.get('journal_removed', 0)} (звільнено слотів в обліку)")
        if errors:
            _alert("errors", f"{len(errors)} rt-карток не вдалось видалити через Prom API (перші: {list(errors.items())[:3]})")
        if processed:
            _log_run(now, list(processed), {"gone_pruned": len(gone), "errors": len(errors), "deleted_24h_before": deleted_24h})
            _notify(f"видалено {len(processed)} осиротілих rt-карток у Prom (за добу разом {deleted_24h + len(processed)} із {DAILY_CAP}); перші 10: {list(processed)[:10]}")
        return res
    except CleanupAbort as e:
        res.update(status="aborted", reason=str(e))
        if apply:
            _alert("abort", f"прибирання осиротілих rt- ПРОПУЩЕНО (fail-closed): {e}")
        else:
            print(f"[rt_prom_cleanup] DRY: {e}", file=sys.stderr)
        return res
    except Exception as e:  # noqa: BLE001 — будь-який збій = нічого не видалено, не валимо prom_catalog_sync
        res.update(status="aborted", reason=f"{type(e).__name__}: {str(e)[:200]}")
        if apply:
            _alert("error", f"прибирання осиротілих rt- зупинено збоєм ({res['reason']}) — нічого не видалено цим прогоном")
        else:
            print(f"[rt_prom_cleanup] DRY: збій {res['reason']}", file=sys.stderr)
        return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Прибирання осиротілих rt-карток у Prom (за замовчуванням --dry: лише перелік, читає Prom API, нічого не пише).")
    ap.add_argument("--apply", action="store_true", help="реально видаляти (потребує PROM_RT_CLEANUP=1)")
    ap.add_argument("--untomb", nargs="+", metavar="ID", help="зняти надгробок(и) rt-id (id знову можна емітувати). Змінює лише файл надгробків, Prom не чіпає, брама не потрібна")
    ap.add_argument("--allow-mass", action="store_true", help="обійти мас-запобіжник (>50%% журналу) — лише після огляду dry-переліку людиною")
    a = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    if a.untomb:
        bad = [i for i in a.untomb if not RT_ID_RE.fullmatch(i)]
        if bad:
            print(f"[rt_prom_cleanup] --untomb: не rt-id: {bad}", file=sys.stderr)
            return 2
        n = rt_prom.unmark_deleted(a.untomb)
        print(f"[rt_prom_cleanup] знято надгробків: {n} із {len(a.untomb)}" if n >= 0 else "[rt_prom_cleanup] файл надгробків пошкоджений/запис не вдався", file=sys.stderr if n < 0 else sys.stdout)
        return 0 if n > 0 else 1
    if a.apply and not gate_on():
        print("[rt_prom_cleanup] брама PROM_RT_CLEANUP=1 не ввімкнена — --apply відхилено (запускаю лише --dry).", file=sys.stderr)
        return 2
    import prom_catalog_sync as pcs
    r = run(a.apply, pcs.fetch_prom_products_by_external_ids, pcs.deactivate, allow_mass=a.allow_mass)
    print(json.dumps({k: v for k, v in r.items() if k != "list"}, ensure_ascii=False))
    return 1 if r["status"] == "aborted" else 0


if __name__ == "__main__":
    sys.exit(main())
