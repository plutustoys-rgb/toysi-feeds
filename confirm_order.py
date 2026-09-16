#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ручне підтвердження оплати замовлення — розблоковує форвард у Toysi.

Для випадків, коли оплата РЕАЛЬНА (кабінет/власник підтвердив), але авто-джерела
не спрацювали одночасно:
  • Rozetka `is_order_paid` порожній (тип «оплата на рахунок продавця» або ручна
    зміна статусу замовлення в кабінеті — ендпоінт status-payment тоді віддає {});
  • банк-Автоклієнт Приват24 не підключений на VPS (BANK_AVAILABLE=False), тож
    bank_check не може звірити з випискою.
У такому разі замовлення висить payment_confirmed=0 і money-safe гейт правильно
НЕ форвардить. Ця команда — ЯВНЕ рішення власника «оплата є, пропусти».

    python confirm_order.py rozetka_906058641

Ставить payment_confirmed=1 → наступний цикл order_pipeline (~15 хв) форвардить у
Toysi (order_router.get_orders_ready_to_forward бере payment_confirmed=1). Нічого
не шле сам — лише знімає гейт оплати.

Викликати ЛИШЕ переконавшись, що гроші справді надійшли (кабінет «Оплачено» /
виписка). Ідемпотентно: якщо вже підтверджено — нічого не робить.
"""
import sys

from orders_db import get_connection, mark_payment_confirmed, _row_to_dict

# UTF-8-вивід: інакше emoji/стрілки у print валять UnicodeEncodeError на cp1251-консолі
# (Windows-десктоп без PYTHONUTF8). Критично тут: крах друку стається ПІСЛЯ
# mark_payment_confirmed, але ДО коміту контекст-менеджера → rollback → підтвердження
# НЕ збереглося б. reconfigure знімає цей ризик (аудит PR #538).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1].strip() in ("-h", "--help", ""):
        print("Використання: python confirm_order.py <internal_order_id>")
        print("  напр.: python confirm_order.py rozetka_906058641")
        sys.exit(2)

    internal_id = sys.argv[1].strip()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE internal_order_id = ?", (internal_id,)
        ).fetchone()
        if row is None:
            print(f"❌ Немає замовлення {internal_id} в orders.db "
                  f"(перевір internal_order_id = '<платформа>_<order_id>').")
            sys.exit(1)

        o = _row_to_dict(row)
        if o.get("payment_confirmed"):
            print(f"ℹ️ {internal_id}: вже payment_confirmed=1 "
                  f"(forwarded_to_toysi_at={o.get('forwarded_to_toysi_at')}, "
                  f"toysi_order_id={o.get('toysi_order_id')}). Нічого не змінюю.")
            return

        print(f"Замовлення {internal_id}: platform={o.get('platform')} "
              f"status={o.get('status')} payment_method={o.get('payment_method')} "
              f"forwarded_to_toysi_at={o.get('forwarded_to_toysi_at')}")
        mark_payment_confirmed(conn, internal_id)
        # commit — контекст-менеджер get_connection на нормальному виході з with.
        print(f"✅ {internal_id}: payment_confirmed=1. Наступний цикл order_pipeline "
              f"(~15 хв) форварднe в Toysi. Перевір журнал: "
              f"journalctl -u order-pipeline.service --since '-20 min'.")


if __name__ == "__main__":
    main()
