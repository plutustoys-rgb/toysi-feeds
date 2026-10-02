# -*- coding: utf-8 -*-
"""rozetka_returns_monitor.py — незабрані/відмовлені Rozetka-посилки (ROZETKA Delivery, RMP-ТТН).

НАВІЩО (2026-10-02, живий кейс 905260801/905484851 + ще 3, 1065 грн): NP-автоповернення тут не діє —
Rozetka-замовлення їдуть RZ-Delivery (довідник rozetka.md). Незабрана посилка САМА проходить:
«Готово до видачі» → ~11 діб → «Вийшов термін зберігання» → повернення на РЦ → «Очікує відправника»
у «м. Київ, Алматинська вул., 4» (точка здачі) → «Повернено». Забрати її мусить ПРЕДСТАВНИК магазину:
прийти у те саме відділення, назвати магазин на маркетплейсі + ФОП, отримати й підписати «Реєстр повернення
відправлень» (sellerhelp.rozetka.com.ua/p733). Ніхто про це не сповіщав — тому цей монітор.

ДЖЕРЕЛА (лише читання): Rozetka Orders API (rozetka_client, ROZETKA_API_TOKEN) — замовлення зі статусами
11/12/19 (не забрано / відмова / повернення) і ТТН «RMP-…»; публічний трекінг
rz-delivery-octopus.rozetka.ua/api/track/status-group (без токена). Передоплата — rozetka_client.is_order_paid.

ЩО РОБИТЬ: на ПЕРЕХОДІ стадії шле ОДИН Telegram-підсумок (дедуп через .local_secrets/rozetka_returns_state.json):
  refused/expired/lost → FYI (+ нагадування про повернення коштів, якщо передплачене);
  collect («Очікує відправника») → ДІЯ: їхати забирати; returned → закриття.
Перший запуск (нема state) — базова лінія: мовчки запам'ятовує, сповіщає лише про «collect» (дію).
НІЧОГО не створює й не змінює в Rozetka; повернення коштів покупцю / RETURN-чек / сторно — людина.
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import rozetka_client
from telegram_notify import send_telegram_message, send_throttled_alert

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / ".local_secrets" / "rozetka_returns_state.json"
TRACK_URL = "https://rz-delivery-octopus.rozetka.ua/api/track/status-group"
LOOKBACK_DAYS = 60
POST_SHIP_STATUSES = {11, 12, 19}       # не забрано / відмова / повернення (ROZETKA_CANCELLED_STATUSES їх свідомо не має)
PICKUP_ADDRESS = "м. Київ, вул. Алматинська, 4"
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

# Остання статус-подія RZ-Delivery (id зі /api/track-status, звірено живо 2026-10-02) → стадія.
_STAGE_BY_STATUS = {
    40040: "waiting", 40030: "waiting", 40080: "waiting",       # у точці покупця
    40050: "refused",                                           # Одержувач відмовився
    40060: "refused",                                           # Відмова відправника
    40070: "expired",                                           # Вийшов термін зберігання
    50010: "returning", 40045: "returning", 50011: "returning", 50012: "returning",
    50013: "returning", 50015: "returning", 50021: "returning", 50030: "returning",
    50020: "collect",                                           # Очікує відправника → ДІЯ
    60040: "returned", 60030: "delivered", 60025: "delivered",
    10080: "lost",
}
_NOTIFY_STAGES = {"refused", "expired", "collect", "returned", "lost"}


def stage_of(last_status_id) -> str:
    try:
        return _STAGE_BY_STATUS.get(int(last_status_id), "other")
    except (TypeError, ValueError):
        return "other"


def fetch_last_status(ttn: str):
    """(status_id, status_name) останньої події треку або None при збої/невідомому ТТН."""
    try:
        r = requests.get(TRACK_URL, params={"id": [ttn]}, headers={"Content-Language": "uk"}, timeout=30)
        data = r.json().get("data") or []
        last = (data[0] or {}).get("last_status") or {}
        return (last.get("id"), last.get("name")) if last.get("id") else None
    except Exception as e:  # noqa: BLE001 — один збій не валить решту
        print(f"[RzReturns] трекінг {ttn}: {e}", file=sys.stderr)
        return None


def collect_cases() -> list:
    """Замовлення Rozetka у статусах 11/12/19 з RMP-ТТН + стадія RZ-трекінгу."""
    today = datetime.now().date()
    orders = rozetka_client.fetch_orders_by_date_range(
        (today - timedelta(days=LOOKBACK_DAYS)).isoformat(), (today + timedelta(days=1)).isoformat())
    cases = []
    for o in orders:
        ttn = str(o.get("ttn") or "")
        if o.get("status") not in POST_SHIP_STATUSES or not ttn.startswith("RMP-"):
            continue
        last = fetch_last_status(ttn)
        cases.append({
            "order_id": str(o["id"]), "ttn": ttn, "amount": o.get("amount"), "created": (o.get("created") or "")[:10],
            "rz_status": o.get("status"),
            "status_id": last[0] if last else None, "status_name": last[1] if last else None,
            "stage": stage_of(last[0]) if last else "unknown",
        })
    return cases


def decide(cases: list, state: dict, baseline: bool) -> list:
    """Повертає [(case, stage)] до сповіщення: перехід стадії у _NOTIFY_STAGES; baseline — лише 'collect'."""
    out = []
    for c in cases:
        prev = state.get(c["order_id"])
        if c["stage"] == "unknown" or prev == c["stage"]:
            continue
        if c["stage"] in _NOTIFY_STAGES and (not baseline or c["stage"] == "collect"):
            out.append((c, c["stage"]))
    return out


_TEXT = {
    "refused": "покупець відмовився від отримання",
    "expired": "вийшов термін зберігання в точці — посилка поїде назад",
    "collect": f"ПОВЕРНУТО В ТОЧКУ ВІДПРАВКИ — забрати: {PICKUP_ADDRESS} («Очікує відправника»)",
    "returned": "статус «Повернено»",
    "lost": "ПОСИЛКУ ВТРАЧЕНО — звернутись до підтримки відправлень Rozetka Delivery",
}


def build_message(items: list) -> str:
    lines = ["📦 Rozetka Delivery — незабрані/повернені посилки:"]
    for c, stage in items:
        pre = " · ПЕРЕДОПЛАТА: повернути кошти покупцю (кабінет, RETURN-чек, сторно)" if c.get("prepaid") else ""
        lines.append(f"№{c['order_id']} ({c['amount']} грн, {c['ttn']}): {_TEXT[stage]}{pre}")
    if any(s == "collect" for _, s in items):
        lines.append("Як забрати: прийти у відділення Алматинська, 4; назвати магазин на маркетплейсі (Plutonix) і "
                     "ФОП; отримати й підписати «Реєстр повернення відправлень».")
    return "\n".join(lines)


def run() -> int:
    baseline = not STATE_FILE.exists()
    state = {}
    if not baseline:
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            state, baseline = {}, True
    try:
        cases = collect_cases()
    except Exception as e:  # noqa: BLE001 — API збій: сказати, а не мовчати
        msg = f"🚨 rozetka_returns_monitor: Orders API збій — {e}"
        print(f"[RzReturns] {msg}", file=sys.stderr)
        if not _NO_TELEGRAM:
            send_throttled_alert("rozetka_returns_monitor_api", msg, cooldown_sec=24 * 3600)
        return 1
    if cases and all(c["stage"] == "unknown" for c in cases):
        msg = "🚨 rozetka_returns_monitor: трекінг RZ-Delivery не відповів ні для однієї посилки"
        print(f"[RzReturns] {msg}", file=sys.stderr)
        if not _NO_TELEGRAM:
            send_throttled_alert("rozetka_returns_monitor_track", msg, cooldown_sec=24 * 3600)
        return 1
    todo = decide(cases, state, baseline)
    for c, stage in todo:
        c["prepaid"] = stage in ("refused", "expired", "collect", "lost") and rozetka_client.is_order_paid(c["order_id"])
    print(f"[RzReturns] випадків: {len(cases)}; до сповіщення: {len(todo)}" + ("; базова лінія" if baseline else ""))
    for c in cases:
        print(f"  №{c['order_id']} {c['ttn']} rz={c['rz_status']} стадія={c['stage']} ({c['status_name']})")
    if todo:
        text = build_message(todo)
        print(text)
        if not _NO_TELEGRAM:
            send_telegram_message(text)
    for c in cases:
        if c["stage"] != "unknown":
            state[c["order_id"]] = c["stage"]
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(run())
