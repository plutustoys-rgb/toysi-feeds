#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_eva_status_sync.py — регрес-тест вхідної синхронізації EVA-статусу (_maybe_sync_eva_status).

Закриває дві сліпі зони, знайдені живо 2026-09-17 на 15 EVA-COD:
  • статус 7 (Отримано) → payment_confirmed=1 + delivery_status='delivered' (COD-реалізація);
  • статус 9/10 (Скасовано) → mark_cancelled (раніше висіли активними 'forwarded_to_supplier').

ІНВАРІАНТИ:
  - лише platform=eva чіпається; інші — жодного EVA-виклику/запису;
  - 7 → оплачено+доставлено (лише якщо ще не так); 9/10 → cancelled;
  - 5 (в дорозі)/інші → нічого;
  - fail-open: EvaAPIError → жодного запису;
  - вже врегульоване (наш cancelled / delivered+оплачено) → EVA API НЕ смикаємо.

Мережа/БД замокані. `python test_eva_status_sync.py` → exit 0/1.
"""
import sys
import order_status_tracker as ost
import eva_orders_client as eva

_FAILS = []


def _check(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


class _Cap:
    def __init__(self):
        self.paid = []
        self.cancelled = []
        self.delivery = []
        self.api_calls = 0


def _run(order, eva_status=None, raise_api=False):
    cap = _Cap()
    ost.mark_payment_confirmed = lambda conn, iid: cap.paid.append(iid)
    ost.mark_cancelled = lambda conn, iid, reason: cap.cancelled.append((iid, reason))
    ost.update_delivery_status = lambda conn, iid, **kw: cap.delivery.append((iid, kw.get("delivery_status")))

    def _get(oid):
        cap.api_calls += 1
        if raise_api:
            raise eva.EvaAPIError("boom")
        return {"status": eva_status}
    ost.eva_orders_client.get_order = _get
    ost._maybe_sync_eva_status(object(), order)
    return cap


def _order(**kw):
    base = dict(platform="eva", order_id="8-100", internal_order_id="eva_8-100",
                status="forwarded_to_supplier", delivery_status="shipped",
                payment_confirmed=0, payment_method="cod")
    base.update(kw)
    return base


# 7 (Отримано) → оплачено + доставлено
c = _run(_order(), eva_status=7)
_check("7→payment_confirmed", c.paid == ["eva_8-100"])
_check("7→delivery=delivered", c.delivery == [("eva_8-100", "delivered")])
_check("7→не cancelled", c.cancelled == [])

# 9 (скасовано покупцем) → cancelled, не оплата/доставка
c = _run(_order(), eva_status=9)
_check("9→cancelled", len(c.cancelled) == 1 and c.cancelled[0][0] == "eva_8-100")
_check("9→не payment/delivery", c.paid == [] and c.delivery == [])

# 10 (скасовано продавцем) → cancelled
c = _run(_order(), eva_status=10)
_check("10→cancelled", len(c.cancelled) == 1)

# 5 (в дорозі) → нічого
c = _run(_order(), eva_status=5)
_check("5→нічого", c.paid == [] and c.cancelled == [] and c.delivery == [])

# non-eva → жодного API-виклику/запису
c = _run(_order(platform="prom"), eva_status=7)
_check("non-eva→без API", c.api_calls == 0)
_check("non-eva→без записів", c.paid == [] and c.cancelled == [] and c.delivery == [])

# fail-open: EvaAPIError → нічого
c = _run(_order(), raise_api=True)
_check("EvaAPIError→fail-open (без записів)", c.paid == [] and c.cancelled == [] and c.delivery == [])
_check("EvaAPIError→API справді викликано", c.api_calls == 1)

# вже cancelled у нас → EVA API не смикаємо
c = _run(_order(status="cancelled"), eva_status=9)
_check("вже cancelled→без API", c.api_calls == 0 and c.cancelled == [])

# вже delivered+оплачено → EVA API не смикаємо
c = _run(_order(delivery_status="delivered", payment_confirmed=1), eva_status=7)
_check("delivered+оплачено→без API", c.api_calls == 0 and c.paid == [])

# 7, але вже оплачено (частковий стан) → не дублюємо оплату, лише доставку
c = _run(_order(payment_confirmed=1, delivery_status="shipped"), eva_status=7)
_check("7 при вже-оплаченому→без повторної оплати", c.paid == [])
_check("7 при вже-оплаченому→доставку все одно ставимо", c.delivery == [("eva_8-100", "delivered")])


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Усі інваріанти EVA-синхронізації виконано")
sys.exit(0)
