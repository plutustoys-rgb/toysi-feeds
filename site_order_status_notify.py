# -*- coding: utf-8 -*-
"""site_order_status_notify.py — Telegram-сповіщення про СТАН замовлення САЙТУ від передачі в Toysi до «отримано» або «скасовано» (рішення власника 08.11.2026: «роби у телеграм. до статусу отримано або скасовано»).

ЧОМУ: у замовлень сайту немає кабінету (як у Prom), де видно статус; Telegram іде лише на «Нове замовлення» і на збої, тож стан відправлення був видний лише в orders.db на VPS.
ДЖЕРЕЛА СТАНУ (ті самі, що вже читає order_status_tracker.track_orders; нових запитів до Toysi нема):
  • Toysi order_status → delivery_status (processing → assembling → packed → shipped; cancelled/returned);
  • Нова Пошта по ТТН (один пакетний запит на цикл для замовлень сайту): статус тексту НП. «Отримано» = текст статусу НП містить «отримано/одержано» (НЕ відмова; той самий принцип, що nova_poshta.get_tracking_status:382–393).
    Toysi-код 70 «Доставлено» Toysi поки НЕ повертає (toysi_order_submit.ORDER_STATUS_MESSAGES[70]), тому «отримано» береться з НП.
ДЕДУП: останній сповіщений стан — orders.site_tg_state. Повідомлення йде, лише коли стан ЗМІНИВСЯ; після фінального (received/cancelled/returned) — тиша. Стан пишеться, лише якщо Telegram прийняв повідомлення (інакше повторимо в наступному циклі).
ЗАСІВ: замовлення, передані в Toysi ДО SITE_TG_SINCE (дата запуску функції), отримують стан мовчки — без шквалу повідомлень про старі замовлення.
ПРИВАТНІСТЬ: у тексті лише номер замовлення (PT-…), номер Toysi, ТТН і статус; ПІБ/телефон/адреса не включаються.
Вхід у Telegram — виняток із режиму «лише замовлення»: telegram_triage.is_site_order_status (початок тексту «<іконка> Замовлення сайту »)."""
import sys

SITE_TG_SINCE = "2026-11-08T00:00:00"      # дата запуску: передані в Toysi раніше — лише засів стану, без повідомлень
FINAL_STATES = ("received", "cancelled", "returned")
_PREFIX = "Замовлення сайту "
_TOYSI_TEXT = {
    "processing": "передано в Toysi, прийнято в обробку",
    "assembling": "на збиранні в Toysi",
    "packed": "запаковано в Toysi",
    "shipped": "відвантажено з Toysi",
}


def _rank(state) -> int:
    """Порядок стадій: стан іде лише ВПЕРЕД (тимчасова відсутність даних НП не має відкочувати «np:7» назад до «відвантажено» й слати зайве повідомлення)."""
    s = state or ""
    if s in FINAL_STATES:
        return 9
    if s.startswith("np:"):
        return 5
    return {"processing": 1, "assembling": 2, "packed": 3, "shipped": 4}.get(s, 0)


def np_received(text: str) -> bool:
    """НП: вручено одержувачу. Відмова/повернення («Відмова…», документ повернення) вручених не рахується."""
    t = (text or "").lower()
    return "отримано" in t or "одержано" in t


def label(order: dict) -> str:
    return str(order.get("public_number") or order.get("internal_order_id") or order.get("order_id") or "?")


def compute_stage(order: dict, toysi_id, delivery_status, ttn, np_info):
    """→ (state, text) або None, якщо повідомляти нічого. np_info — запис пакета НП для цього ТТН (або None)."""
    lab = _PREFIX + label(order)
    ds = delivery_status or ""
    if ds in ("cancelled", "returned"):
        icon, word = ("❌", "скасовано") if ds == "cancelled" else ("↩️", "повернення")
        return ds, f"{icon} {lab}: {word} (Toysi #{toysi_id}" + (f", ТТН {ttn}" if ttn else "") + ")"
    ntext = (np_info or {}).get("status") or ""
    if ds == "delivered" or (ttn and np_info and np_received(ntext)):
        return "received", f"✅ {lab}: отримано · ТТН {ttn or '—'}" + (f" · НП: {ntext[:100]}" if ntext else "")
    if ttn and np_info and ntext:
        return f"np:{np_info.get('status_code') or ntext[:30]}", f"🚚 {lab}: ТТН {ttn} · НП: {ntext[:100]}"
    if ds in _TOYSI_TEXT:
        return ds, f"📦 {lab}: {_TOYSI_TEXT[ds]} (Toysi #{toysi_id})" + (f" · ТТН {ttn}" if ttn else "")
    return None


def maybe_notify(order: dict, toysi_id, delivery_status, ttn, np_info, send, save_state, orders_only: bool) -> str | None:
    """Надсилає повідомлення про зміну стану. send(text)->bool; save_state(state). Повертає записаний стан або None. Ніколи не кидає виняток (трекер не ламаємо)."""
    try:
        if (order.get("platform") or "") != "site":
            return None
        prev = order.get("site_tg_state")
        if prev in FINAL_STATES:
            return None
        st = compute_stage(order, toysi_id, delivery_status, ttn, np_info)
        if st is None:
            return None
        state, text = st
        if state == prev or (prev is not None and _rank(state) < _rank(prev)):
            return None
        if prev is None and (order.get("forwarded_to_toysi_at") or "") < SITE_TG_SINCE:
            save_state(state)                  # засів: старе замовлення — без повідомлення
            return state
        if state in ("cancelled", "returned") and not orders_only:
            save_state(state)                  # у повному режимі Telegram скасування/повернення вже шле сам трекер (order_status_tracker.track_orders) — без дубля
            return state
        if send(text):
            save_state(state)
            return state
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[site_order_status_notify] {type(e).__name__}: {e}", file=sys.stderr)
        return None
