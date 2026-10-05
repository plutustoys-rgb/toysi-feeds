# -*- coding: utf-8 -*-
"""kodv_return_receipt.py — вузький скрипт: фіскальний чек ПОВЕРНЕННЯ (RETURN) Checkbox на ПОВНЕ повернення
одного товару за серіалом чека продажу.

Замовлення: КОДВ_CHANNEL.md, запис «Головний бухгалтер → Код» 2026-10-05 (рішення власника: скрипти пише Код).
Тижневий аудит 05.10 знайшов продажі, де гроші повернуто / чек зайвий, а чека RETURN немає (каса ≠ книга на 288,29 ₴).

ВИКЛИК:
    python kodv_return_receipt.py --sell-serial N --dry-run [--expect-fiscal КОД] [--expect-sum СУМА]
    python kodv_return_receipt.py --sell-serial N --expect-fiscal КОД [--expect-sum СУМА]      ← ЖИВИЙ запуск
  --dry-run        лише пошук чека, перевірки й тіло запиту; НІЧОГО не створює (Checkbox читається тільки GET).
  --expect-fiscal  фіскальний код чека продажу, який має знайтись. ОБОВ'ЯЗКОВИЙ для живого запуску (аудит PR #613:
                   фіскальний RETURN незворотний, помилка в серіалі = RETURN на чужий продаж; з --dry-run — необов'язковий).
  --expect-sum     те саме для суми (грн).

ЩО РОБИТЬ (усе з САМОГО чека продажу, не з аргументів):
  1. Шукає чек за серіалом через /receipts/search ланцюжком 89-денних вікон назад (як checkbox_registry_sync;
     одне вікно на 90+ днів API відхиляє з 400 date.wrong_interval). Заодно збирає всі НОВІШІ чеки (повернення
     завжди пізніші за продаж) для перевірки дубля.
  2. ВІДМОВЛЯЄ (код виходу 2), якщо: чек не SELL/DONE або тестовий; до нього вже є RETURN з тим самим
     related_receipt_id (будь-який статус, крім ERROR — щоб повторний запуск після збою не задвоїв); товарів
     більше одного (часткове повернення не підтримується); оплат не одна; сума товару ≠ сумі оплати; у чеку є
     податки/знижки (їх скрипт не переносить — сума збіглась би, а чек повернення розійшовся б з чеком продажу);
     не збігся --expect-*; є файл-замок попереднього запуску з невідомим результатом.
  3. Тіло: POST /receipts/sell, goods[].is_return=true, related_receipt_id = id продажу, id = uuid4, оплата того
     ж типу на всю суму, телефон (delivery) з чека продажу. Формат звірено з чеком RETURN 122 (St4CpAB5qXM, 02.10).
  4. Перед POST — ПОВТОРНА перевірка дубля по свіжому першому вікну (між першим пошуком і POST міг пройти інший запуск).
  5. _ensure_shift_open → створення → опитування GET /receipts/{id} до DONE/ERROR; друкує серіал, фіскальний №,
     суму, дату.

ЗАМОК: `.local_secrets/kodv_return_<serial>.lock` створюється ПЕРЕД POST. Знімається при успіху (DONE) і при
відмові/помилці ДО відправки. Якщо POST відправлено, а результат невідомий (таймаут, обрив, Ctrl+C) — замок ЛИШАЄТЬСЯ:
наступний запуск відмовить, доки людина не перевірить касу й не видалить файл (захист від подвійного фіскального чека).

Коди виходу: 0 — створено/dry-run ок; 2 — відмова перевірки (нічого не створено); 3 — помилка API/мережі
(якщо POST уже відправлено — СТАН ЧЕКА НЕВІДОМИЙ, замок лишається).
"""
import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import checkbox_client as cb
import checkbox_registry_sync as cs

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MAX_LOOKBACK_WINDOWS = cs.MAX_LOOKBACK_WINDOWS
POLL_ATTEMPTS = 30
POLL_INTERVAL_SEC = 3
POLL_MAX_CONSECUTIVE_ERRORS = 3
KYIV = ZoneInfo("Europe/Kyiv")
LOCK_DIR = Path(__file__).parent / ".local_secrets"


class Refusal(Exception):
    """Перевірка не пройдена — чек повернення НЕ створюється (код виходу 2)."""


def _log(msg: str) -> None:
    print(msg, flush=True)


# ── пошук ───────────────────────────────────────────────────────────────────────────────────────
def find_sell(serial: int, headers: dict, fetch_window=None, now: datetime | None = None):
    """Повертає (чек продажу, список УСІХ чеків, зібраних до й включно з вікном знахідки).
    Сканує вікна назад, доки не знайде серіал; не знайшов за MAX_LOOKBACK_WINDOWS → Refusal."""
    fetch_window = fetch_window or cs._fetch_window
    to_dt = now or datetime.now(KYIV)
    collected: list = []
    for _ in range(MAX_LOOKBACK_WINDOWS):
        from_dt = to_dt.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=89)
        window = fetch_window(headers, from_dt, to_dt) or []
        collected.extend(window)
        hits = [r for r in window if r.get("serial") == serial]
        if hits:
            if len(hits) > 1:
                raise Refusal(f"серіал {serial} зустрівся {len(hits)} разів — неоднозначно")
            return hits[0], collected
        to_dt = from_dt
    raise Refusal(f"чек із серіалом {serial} не знайдено за {MAX_LOOKBACK_WINDOWS} вікон по 89 днів")


# ── перевірки (чисті функції) ───────────────────────────────────────────────────────────────────
def find_duplicate(sell_id: str, receipts: list):
    for r in receipts:
        if r.get("type") == "RETURN" and r.get("related_receipt_id") == sell_id and r.get("status") != "ERROR":
            return r
    return None


def validate(sell: dict, all_receipts: list, expect_fiscal: str | None = None, expect_sum: float | None = None) -> None:
    """Кидає Refusal з поясненням; повертає None, якщо повернення можна створювати."""
    if sell.get("type") != "SELL":
        raise Refusal(f"чек {sell.get('serial')} має type={sell.get('type')}, потрібен SELL")
    if sell.get("status") != "DONE":
        raise Refusal(f"чек {sell.get('serial')} має status={sell.get('status')}, потрібен DONE")
    if sell.get("is_test") is True:
        raise Refusal(f"чек {sell.get('serial')} тестовий (is_test) — не повертаємо")
    sid = sell.get("id")
    if not sid:
        raise Refusal("у чека продажу нема id")
    d = find_duplicate(sid, all_receipts)
    if d:
        raise Refusal(f"до чека {sell.get('serial')} УЖЕ є RETURN (серіал {d.get('serial')}, статус {d.get('status')}, "
                      f"фіскальний {d.get('fiscal_code')}) — дубль не створюю")
    goods = sell.get("goods") or []
    if len(goods) != 1:
        raise Refusal(f"у чека {len(goods)} позицій; частково/багатопозиційно скрипт не повертає (потрібна рівно 1)")
    pays = sell.get("payments") or []
    if len(pays) != 1:
        raise Refusal(f"у чека {len(pays)} оплат; потрібна рівно 1")
    if sell.get("taxes") or sell.get("discounts") or goods[0].get("taxes") or goods[0].get("discounts"):
        raise Refusal("у чека продажу є податки/знижки — скрипт їх не переносить у чек повернення; створи вручну в кабінеті Checkbox")
    g = goods[0]
    gd = g.get("good") or {}
    if not gd.get("name"):
        raise Refusal("у товарі чека нема назви (good.name)")
    price, qty = gd.get("price"), g.get("quantity")
    if not isinstance(price, int) or not isinstance(qty, int) or price <= 0 or qty <= 0:
        raise Refusal(f"некоректні ціна/кількість у чеку (price={price}, quantity={qty})")
    line_sum = round(price * qty / 1000)
    pay_val = pays[0].get("value")
    if pays[0].get("type") not in ("CASH", "CASHLESS"):
        raise Refusal(f"тип оплати {pays[0].get('type')} не підтримується (лише CASH/CASHLESS)")
    if pay_val != line_sum or sell.get("total_sum") != line_sum:
        raise Refusal(f"суми не сходяться: товар {line_sum}, оплата {pay_val}, total_sum {sell.get('total_sum')} (копійки)")
    if expect_fiscal is not None and sell.get("fiscal_code") != expect_fiscal:
        raise Refusal(f"фіскальний код чека {sell.get('fiscal_code')} ≠ очікуваний {expect_fiscal} — перевір серіал")
    if expect_sum is not None and abs(line_sum / 100 - float(expect_sum)) > 0.005:
        raise Refusal(f"сума чека {line_sum / 100:.2f} ≠ очікувана {float(expect_sum):.2f} — перевір серіал")


def build_body(sell: dict, new_id: str | None = None) -> dict:
    """Тіло POST /receipts/sell для повного повернення. Викликати ПІСЛЯ validate()."""
    g = sell["goods"][0]
    gd = g["good"]
    pay = sell["payments"][0]
    body = {
        "id": new_id or str(uuid.uuid4()),
        "related_receipt_id": sell["id"],
        "goods": [{
            "good": {"code": str(gd.get("code") or gd["name"])[:50], "name": str(gd["name"])[:200], "price": gd["price"]},
            "quantity": g["quantity"],
            "is_return": True,
        }],
        "payments": [{"type": pay["type"], "value": pay["value"]}],
    }
    phone = cb._normalize_delivery_phone((sell.get("delivery") or {}).get("phone"))
    if phone:
        body["delivery"] = {"phone": phone}
    return body


def _masked(body: dict) -> dict:
    """Копія тіла для ДРУКУ: телефон покупця маскується (вивід часто вставляють у спільні файли)."""
    b = json.loads(json.dumps(body))
    if b.get("delivery", {}).get("phone"):
        p = b["delivery"]["phone"]
        b["delivery"]["phone"] = p[:6] + "****" + p[-3:]
    return b


# ── замок ───────────────────────────────────────────────────────────────────────────────────────
def _lock_path(serial: int, lock_dir: Path | None = None) -> Path:
    return (lock_dir or LOCK_DIR) / f"kodv_return_{serial}.lock"


def acquire_lock(serial: int, lock_dir: Path | None = None) -> Path:
    p = _lock_path(serial, lock_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise Refusal(f"є замок {p}: попередній запуск для серіалу {serial} або ще йде, або закінчився з НЕВІДОМИМ результатом. "
                      f"Перевір у Checkbox, чи існує RETURN до цього чека; якщо ні — видали файл і повтори.")
    with os.fdopen(fd, "w") as fh:
        fh.write(f"{datetime.now(KYIV).isoformat()} pid={os.getpid()}\n")
    return p


def release_lock(p: Path | None) -> None:
    if p is not None:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


# ── Checkbox API ────────────────────────────────────────────────────────────────────────────────
def _post_receipt(token: str, body: dict) -> dict:
    headers = {"X-License-Key": cb.CHECKBOX_API_KEY, "Authorization": f"Bearer {token}"}
    r = None
    try:
        r = requests.post(f"{cb.CHECKBOX_API_URL}/receipts/sell", headers=headers, json=body, timeout=cb.REQUEST_TIMEOUT)
        r.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise cb.CheckboxAPIError(f"POST /receipts/sell відхилено: {e} — тіло відповіді: {r.text[:500] if r is not None else ''}") from e
    except requests.exceptions.RequestException as e:
        raise cb.CheckboxAPIError(f"помилка з'єднання (POST /receipts/sell): {e}") from e
    try:
        return r.json()
    except ValueError:
        raise cb.CheckboxAPIError(f"невалідна відповідь (не JSON): {r.text[:300]}")


def _get_receipt(token: str, receipt_id: str) -> dict:
    headers = {"X-License-Key": cb.CHECKBOX_API_KEY, "Authorization": f"Bearer {token}"}
    try:
        r = requests.get(f"{cb.CHECKBOX_API_URL}/receipts/{receipt_id}", headers=headers, timeout=cb.REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json() or {}
    except (requests.exceptions.RequestException, ValueError) as e:
        raise cb.CheckboxAPIError(f"GET /receipts/{receipt_id}: {e}") from e


def wait_done(token: str, receipt_id: str, get=None, sleep=time.sleep) -> dict:
    get = get or _get_receipt
    last, errs = {}, 0
    for _ in range(POLL_ATTEMPTS):
        try:
            last = get(token, receipt_id)
            errs = 0
        except cb.CheckboxAPIError:
            errs += 1                       # разова 5xx/мережева помилка опитування не означає, що чека нема
            if errs >= POLL_MAX_CONSECUTIVE_ERRORS:
                raise
            sleep(POLL_INTERVAL_SEC)
            continue
        st = last.get("status")
        if st == "DONE":
            return last
        if st == "ERROR":
            raise cb.CheckboxAPIError(f"чек {receipt_id} у статусі ERROR: {str(last)[:400]}")
        sleep(POLL_INTERVAL_SEC)
    raise cb.CheckboxAPIError(f"чек {receipt_id} не став DONE за {POLL_ATTEMPTS * POLL_INTERVAL_SEC} с (статус {last.get('status')})")


def _fmt_dt(iso: str | None) -> str:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).astimezone(KYIV).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return iso or "?"


# ── оркестрація ─────────────────────────────────────────────────────────────────────────────────
def run(serial: int, dry_run: bool, expect_fiscal=None, expect_sum=None, *, auth=None, fetch_window=None,
        ensure_shift=None, post=None, get=None, sleep=time.sleep, lock_dir=None, now=None) -> int:
    auth = auth or cb._authenticate_cashier
    ensure_shift = ensure_shift or cb._ensure_shift_open
    post = post or _post_receipt
    sent = False
    lock = None
    try:
        if not dry_run and not expect_fiscal:
            raise Refusal("для ЖИВОГО запуску обов'язковий --expect-fiscal <фіскальний код чека продажу> (захист від помилки в серіалі; "
                          "без --dry-run чек RETURN незворотний)")
        token = auth()
        headers = {"X-License-Key": cb.CHECKBOX_API_KEY, "Authorization": f"Bearer {token}"}
        sell, allr = find_sell(serial, headers, fetch_window, now)
        _log(f"Чек продажу: серіал {sell.get('serial')}, фіскальний {sell.get('fiscal_code')}, "
             f"{(sell.get('total_sum') or 0) / 100:.2f} грн, {_fmt_dt(sell.get('created_at'))}, статус {sell.get('status')}, тип {sell.get('type')}")
        validate(sell, allr, expect_fiscal, expect_sum)
        body = build_body(sell)
        _log("Перевірки пройдено. Тіло запиту POST /receipts/sell (телефон замасковано у виводі):")
        _log(json.dumps(_masked(body), ensure_ascii=False, indent=2))
        if dry_run:
            _log("--dry-run: чек НЕ створено.")
            return 0
        lock = acquire_lock(serial, lock_dir)
        # повторна перевірка дубля по СВІЖОМУ першому вікню: між першим пошуком і POST міг пройти інший запуск
        fresh = (fetch_window or cs._fetch_window)(headers, (now or datetime.now(KYIV)).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=89),
                                                   now or datetime.now(KYIV)) or []
        d = find_duplicate(sell["id"], fresh)
        if d:
            raise Refusal(f"перед POST знайдено RETURN (серіал {d.get('serial')}, статус {d.get('status')}) — дубль не створюю")
        ensure_shift(token)
        sent = True    # з цього моменту стан чека у Checkbox може бути невідомим
        resp = post(token, body)
        rid = (resp or {}).get("id") or body["id"]
        done = wait_done(token, rid, get, sleep)
        _log(f"✅ RETURN створено: серіал {done.get('serial')}, фіскальний {done.get('fiscal_code')}, "
             f"{(done.get('total_sum') or 0) / 100:.2f} грн, {_fmt_dt(done.get('created_at'))}, до продажу серіал {serial}")
        release_lock(lock)
        lock = None
        return 0
    except Refusal as e:
        _log(f"⛔ ВІДМОВА: {e}")
        if not sent:
            release_lock(lock)
        return 2
    except (cb.CheckboxAPIError, requests.exceptions.RequestException, KeyboardInterrupt, Exception) as e:  # noqa: BLE001
        _log(f"🚨 ПОМИЛКА: {type(e).__name__}: {e}")
        if sent:
            _log(f"СТАН ЧЕКА НЕВІДОМИЙ — перевір касу в Checkbox. Замок {lock} ЛИШЕНО: повторний запуск відмовить, "
                 f"доки не перевіриш і не видалиш його.")
        else:
            release_lock(lock)
            _log("Запит на створення чека ще НЕ надсилався — нічого не створено.")
        return 3


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Чек повернення (RETURN) Checkbox за серіалом чека продажу")
    ap.add_argument("--sell-serial", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--expect-fiscal", default=None)
    ap.add_argument("--expect-sum", type=float, default=None)
    a = ap.parse_args(argv)
    return run(a.sell_serial, a.dry_run, a.expect_fiscal, a.expect_sum)


if __name__ == "__main__":
    sys.exit(main())
