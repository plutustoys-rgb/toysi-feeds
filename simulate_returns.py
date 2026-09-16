#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""READ-ONLY: моделює, скільки посилок ЗАРАЗ треба повернути до Toysi і від кого.

НІЧОГО НЕ СТВОРЮЄ й НЕ ПИШЕ (ні orders.db, ні зворотних ТТН) — лише рахує й друкує.
Дзеркалить логіку тригера автоповернення (`order_status_tracker._create_np_return`),
але без дії — щоб побачити беклог перед вмиканням NP_RETURN_APPLY=1.

Два набори:
  A. Уже скасовані/повернені ВІДПРАВЛЕНІ замовлення без оформленого повернення:
     forwarded_to_toysi_at IS NOT NULL AND delivery_status IN ('cancelled','returned')
     AND np_return_created_at IS NULL. Це «треба повернути» з наявних даних (без API).
  B. АКТИВНІ відправлені Rozetka-замовлення, скасовані в КАБІНЕТІ, але Toysi-статус
     ще не відобразив — жива перевірка rozetka_client.get_order_status (те, що ловить
     _maybe_ticket_rozetka_cancelled). Prom/EVA пост-форвардної перевірки ще нема.

Запуск на VPS: `cd /opt/plutustoys && venv/bin/python3 simulate_returns.py`
"""
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from orders_db import get_connection, get_active_toysi_orders, _row_to_dict
import rozetka_client


def _line(o: dict, reason: str) -> str:
    return (f"  {o.get('internal_order_id')} | {o.get('platform') or '?'} | "
            f"{o.get('customer_name') or '?'} | ТТН {o.get('toysi_ttn') or o.get('np_branch') or '?'} | {reason}")


def main() -> None:
    need = []  # (order, reason)
    with get_connection() as conn:
        # A. Уже скасовані/повернені відправлені, без оформленого повернення.
        rows = conn.execute(
            """
            SELECT * FROM orders
            WHERE forwarded_to_toysi_at IS NOT NULL
              AND toysi_ttn IS NOT NULL
              AND np_return_created_at IS NULL
              AND delivery_status IN ('cancelled', 'returned')
            """
        ).fetchall()
        for r in rows:
            o = _row_to_dict(r)
            need.append((o, f"delivery_status={o.get('delivery_status')}"))

        # B. Активні Rozetka з ТТН — скасовано в кабінеті, Toysi ще не відобразив.
        active = get_active_toysi_orders(conn)

    seen_b = 0
    for o in active:
        if o.get("platform") != "rozetka" or not o.get("toysi_ttn"):
            continue
        if o.get("np_return_created_at"):
            continue
        try:
            st = rozetka_client.get_order_status(o["order_id"])
        except Exception as e:  # noqa: BLE001 — read-only симуляція, не валимо через один збій
            print(f"[simulate] Rozetka #{o.get('order_id')}: статус не зчитано ({e})", file=sys.stderr)
            continue
        seen_b += 1
        if st in rozetka_client.ROZETKA_CANCELLED_STATUSES:
            need.append((o, f"Rozetka кабінет статус={st} (скасовано, Toysi ще не відобразив)"))

    # Звіт
    by_plat = {}
    for o, _ in need:
        p = o.get("platform") or "?"
        by_plat[p] = by_plat.get(p, 0) + 1

    print(f"=== МОДЕЛЮВАННЯ ПОВЕРНЕНЬ (read-only, нічого не створено) ===")
    print(f"Треба повернути ЗАРАЗ: {len(need)} посилок  |  розбивка: {dict(by_plat) or '—'}")
    print(f"(перевірено активних Rozetka наживо: {seen_b})")
    if need:
        print("Список (кого й куди повертати):")
        for o, reason in need:
            print(_line(o, reason))
    else:
        print("Беклогу немає — нічого повертати.")


if __name__ == "__main__":
    main()
