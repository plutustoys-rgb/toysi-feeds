# -*- coding: utf-8 -*-
"""zoobaza_intake.py — «claim» ZooBaza-замовлень до того, як їх побачить Toysi-роутер (схема: ZooBaza_схема_від_А_до_Я_2026-10-07.md, §3).

ПРОБЛЕМА: замовлення з Prom/Rozetka/EVA потрапляють в ТУ САМУ `orders.db`, а `order_router.route_pending_orders` бере ВСІ неперелані
(`orders_db.get_orders_ready_to_forward`, фільтра за постачальником нема). ZooBaza-SKU там = «немає в наявності» → кожні 15 хв повтор +
Telegram «залишок Toysi недостатній» + через 25 хв алерт вотчдога «застрягло». Гірше: числовий код ZooBaza міг збігтись з id Toysi.

РІШЕННЯ (мінімальні точки дотику — ДОДАВАННЯ, не зміна логіки; підключаються окремим PR після підтвердження власником D1):
  1. `order_pipeline.py` виконує poll → bank_check → route ПОСЛІДОВНО в одному процесі; між poll і route викликається `claim_new()`.
  2. До списку статусів-виключень `get_orders_ready_to_forward` (патерн є: toysi_error, *_cancelled_before_forward) додаються
     `zoobaza_hold` і `zoobaza_mixed_hold` — той самий відбір бере service_watchdog, тож хибних «застрягло» теж нема.
  3. `daily_report._open_orders_detail_section` (669-671) має власний список статусів-виключень — додати ті самі два статуси
     (інакше утримані замовлення дадуть «🔴 застрягло», аудит PR #634 M-1). Усі три правки — ОДНИМ PR, claim_new викликати
     ЛИШЕ з order_pipeline (послідовно з роутером; оптимістичний замок `status IS ?` закриває і гонку з іншим процесом).
  ⚠️ Утримане замовлення більше не проходить `_check_*_not_cancelled` (вони в route_order) — скасування покупцем після hold
     має ловити власний ланцюг ZooBaza (Фаза 1; аудит M-3).

ВПІЗНАННЯ: позиція ZooBaza = `toysi_code` з префіксом `zb-` (генератор фіду `zoobaza_prom_feed.py` ставить його в offer id/vendorCode;
Prom віддає це як sku/external_id, EVA — offer id, Rozetka — article). Решта — Toysi.
  • всі позиції zb-  → статус `zoobaza_hold` (Toysi-ланцюг не бачить; далі — власний ланцюг ZooBaza);
  • змішаний кошик (є і zb-, і не-zb-) → `zoobaza_mixed_hold`: НЕ йде ні в Toysi, ні автоматично в ZooBaza; один голосний алерт власнику
    (дві посилки, дві ТТН, платник одержувач — рішення «розділити чи скасувати» за власником).

ВЛАСНИЙ СТАН: `zoobaza_state.json` (атомарно), не `orders.db` і не файли Toysi. У orders.db пишеться ЛИШЕ поле `status` замовлення,
яке claim забирає (UPDATE одного рядка за internal_order_id, лише якщо воно ще не переслане Toysi).
Алерти: один раз на замовлення (власний стан), без ПІБ/телефону покупця.
"""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ID_PREFIX = "zb-"
STATUS_HOLD = "zoobaza_hold"
STATUS_MIXED = "zoobaza_mixed_hold"
STATE_FILE = Path(os.environ.get("ZB_STATE_FILE", "") or (Path(__file__).resolve().parent / "zoobaza_state.json"))


def is_zb_code(code) -> bool:
    return str(code or "").strip().lower().startswith(ID_PREFIX)


def classify_items(items) -> str:
    """'zoobaza' — усі позиції zb-; 'mixed' — є і zb-, і інші; 'other' — жодної zb- (Toysi-шлях); 'empty' — позицій нема."""
    codes = [it.get("toysi_code") for it in (items or []) if isinstance(it, dict)]
    if not codes:
        return "empty"
    zb = [is_zb_code(c) for c in codes]
    if all(zb):
        return "zoobaza"
    if any(zb):
        return "mixed"
    return "other"


def load_state(path: Path = None) -> dict:
    p = Path(path or STATE_FILE)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: Path = None) -> None:
    p = Path(path or STATE_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _alert(text: str) -> None:
    if os.environ.get("AUDIT_NO_TELEGRAM") == "1":
        return
    try:
        from telegram_notify import send_telegram_message
        send_telegram_message(text)
    except Exception as e:  # noqa: BLE001 — алерт best-effort, claim не падає
        print(f"[ZB-intake] Telegram не надіслано: {e}", file=sys.stderr)


def _describe(order: dict) -> str:
    parts = [f"{it.get('toysi_code')} × {it.get('qty', 1)}" for it in order["items"] if isinstance(it, dict)]
    return f"{order['internal_order_id']} ({order.get('payment_method')}): " + "; ".join(parts)


def claim_new(conn, state_path: Path = None, notify: bool = True) -> dict:
    """Забирає ZooBaza-замовлення, ще не передані Toysi. Ідемпотентно. Повертає {'claimed': [...], 'mixed': [...]}."""
    import orders_db   # лінивий імпорт: модуль класифікації/стану не залежить від БД
    rows = conn.execute(
        """
        SELECT * FROM orders
        WHERE forwarded_to_toysi_at IS NULL
          AND (status IS NULL OR (status NOT IN (?, ?) AND status NOT LIKE '%cancel%' AND status != 'toysi_error'))
        """, (STATUS_HOLD, STATUS_MIXED)).fetchall()
    state = load_state(state_path)
    claimed, mixed = [], []
    for row in rows:
        order = orders_db._row_to_dict(row)
        kind = classify_items(order["items"])
        if kind not in ("zoobaza", "mixed"):
            continue
        new_status = STATUS_HOLD if kind == "zoobaza" else STATUS_MIXED
        # UPDATE лише цього рядка й лише поки він не переданий Toysi (захист від гонки з роутером у іншому процесі)
        cur = conn.execute(
            "UPDATE orders SET status = ? WHERE internal_order_id = ? AND forwarded_to_toysi_at IS NULL AND status IS ?",
            (new_status, order["internal_order_id"], order.get("status")))   # оптимістичний замок: статус не змінився з моменту SELECT (аудит M-1b)
        if cur.rowcount != 1:
            continue
        rec = state.setdefault(order["internal_order_id"], {
            "first_seen": datetime.now().isoformat(timespec="seconds"), "kind": kind, "stage": "claimed",
            "platform": order["platform"], "items": [{"code": it.get("toysi_code"), "qty": it.get("qty", 1)} for it in order["items"]],
            "alerted": False})
        rec["kind"] = kind
        (claimed if kind == "zoobaza" else mixed).append(order["internal_order_id"])
        if notify and not rec.get("alerted"):
            if kind == "zoobaza":
                _alert("🐾 ZooBaza: нове замовлення — " + _describe(order) +
                       ". Toysi-ланцюг його НЕ бере. Далі — ланцюг ZooBaza (лист постачальнику → наявність → рахунок → оплата → ТТН).")
            else:
                _alert("🔴 ZooBaza: ЗМІШАНИЙ кошик (іграшки + зоотовари) — " + _describe(order) +
                       ". Ні в Toysi, ні в ZooBaza автоматично НЕ піде. Потрібне рішення: розділити на дві посилки (дві ТТН, платник одержувач) або скасувати.")
            rec["alerted"] = True
    if claimed or mixed:
        save_state(state, state_path)
    return {"claimed": claimed, "mixed": mixed}


if __name__ == "__main__":
    import orders_db
    with orders_db.get_connection() as c:
        print(claim_new(c, notify=os.environ.get("ZB_INTAKE_NOTIFY", "0") == "1"))
