#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_observed_discount.py — регрес-тест per-SKU кешу спостережених знижок Toysi
(наскрізний аудит 2026-09-27, "ми торгуєм зі збитком").

Живий інцидент: EVA-замовлення 8-081895735 («Курчатко», toysi_code 268183) продане
в мінус −2.30₴, бо код вважав знижку 15% (правило за категорією), а Toysi реально
дав лише 5%. Повний скан 129 реальних замовлень показав: виняток НЕ категорійний і
НЕ брендовий — той самий бренд MIC + категорія «Соски і прорізувачі» + країна Китай
дав ОДНОЧАСНО 15% («Білочка») і 5% («Овечка») різним SKU. Жодне правило за
метаданими каталогу цього не відрізнить.

ІНВАРІАНТИ:
  1. competitor_pricing.observed_discount_pct(): є свіжий запис → повертає його
     pct; нема запису / запис застарів (>90 днів) → None (фолбек на правило).
  2. competitor_pricing.toysi_discounted_price(): спостережена знижка ПЕРЕВАЖАЄ
     категорію/бренд/країну (живий кейс «Курчатко»: категорія не виключена,
     країна Китай — правило дало б 15%, спостереження 5% МАЄ перемогти).
  3. Товар БЕЗ спостереження — фолбек на старе правило, без регресу (Kinsmart 0%,
     Україна-кап 5%, звичайний Китай 15% — усе як раніше).
  4. order_status_tracker._maybe_record_observed_discount(): парсить
     order_positions, пропускає pid Збірки (33340), ідемпотентний
     (discount_recorded_at), fail-open на виняток API.
  5. record_observed_discounts(): аномальний pct (поза [0, TOYSI_OBSERVED_DISCOUNT_
     SANITY_MAX]) НЕ записується — санітарна межа проти мовчазного відтворення
     оригінального money-збитку через одну спотворену точку в кеші (знахідка
     незалежного аудиту PR перед мержем).

Мережа/файли замокані (tmp-файл кешу). `python test_observed_discount.py` → exit 0/1.
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import competitor_pricing as cp

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


# ── 1+2+3: observed_discount_pct / toysi_discounted_price ──
with tempfile.TemporaryDirectory() as tmpdir:
    tmp_file = Path(tmpdir) / "toysi_observed_discount_state.json"

    with mock.patch.object(cp, "TOYSI_OBSERVED_DISCOUNT_FILE", tmp_file), \
         mock.patch.object(cp, "_observed_discount_cache", None):

        # 1a. Кеш порожній (файл не існує) → None
        _chk("порожній кеш: observed_discount_pct = None", cp.observed_discount_pct("999") is None)

        # 1b. Живий кейс «Курчатко» (id 268183): реальне спостереження 5%
        cp.record_observed_discounts({
            "268183": {"pct": 0.05, "observed_at": datetime.now().isoformat(timespec="seconds"),
                       "source_order": "eva_8-081895735"},
        })
        _chk("після запису: observed_discount_pct повертає 0.05", cp.observed_discount_pct("268183") == 0.05)
        _chk("файл кешу дійсно створено на диску", tmp_file.exists())

        # 1c. Застарілий запис (>90 днів) → None (фолбек)
        old_ts = (datetime.now() - timedelta(days=91)).isoformat(timespec="seconds")
        cp.record_observed_discounts({
            "111": {"pct": 0.05, "observed_at": old_ts, "source_order": "old_order"},
        })
        _chk("застарілий (>90д) запис → None", cp.observed_discount_pct("111") is None)

        # 1d. Свіжий (89 днів) — ще дійсний
        fresh_ts = (datetime.now() - timedelta(days=89)).isoformat(timespec="seconds")
        cp.record_observed_discounts({
            "112": {"pct": 0.15, "observed_at": fresh_ts, "source_order": "fresh_order"},
        })
        _chk("свіжий (89д) запис ще дійсний", cp.observed_discount_pct("112") == 0.15)

        # 2. ЖИВИЙ КЕЙС: toysi_discounted_price для «Курчатко» — sposterežena 5% ПЕРЕВАЖАЄ
        #    категорію/країну (Китай, «Заводні іграшки» — не в жодному винятку, правило дало б 15%)
        kurchatko = {"id": "268183", "price": "95", "vendor": "MIC", "country": "Китай",
                     "category_name": "Заводні іграшки"}
        got = cp.toysi_discounted_price(kurchatko)
        _chk("Курчатко: спостережені 5% перемагають правило (95*0.95=90.25, НЕ 95*0.85=80.75)",
             abs(got - 90.25) < 0.01)

        # 3a. Товар БЕЗ спостереження, звичайний Китай → фолбек 15% (регрес-варта старої поведінки)
        no_observation = {"id": "302902", "price": "144.9", "vendor": "MIC", "country": "Китай",
                           "category_name": "Іграшки антистрес"}
        got = cp.toysi_discounted_price(no_observation)
        _chk("без спостереження: фолбек 15% (144.9*0.85=123.165)", abs(got - 123.165) < 0.01)

        # 3b. Товар БЕЗ спостереження, Україна → фолбек 5%-кап (регрес-варта)
        ukraine_item = {"id": "27715", "price": "61.22", "vendor": "Strateg", "country": "Україна",
                         "category_name": "Розважальні"}
        got = cp.toysi_discounted_price(ukraine_item)
        _chk("без спостереження, Україна: фолбек 5%-кап (61.22*0.95=58.159)", abs(got - 58.159) < 0.01)

        # 3c. Товар БЕЗ спостереження, виключений бренд Kinsmart → фолбек 0% (регрес-варта)
        kinsmart_item = {"id": "118489", "price": "176.24", "vendor": "Kinsmart", "country": "Китай",
                          "category_name": "Металеві моделі"}
        got = cp.toysi_discounted_price(kinsmart_item)
        _chk("без спостереження, Kinsmart: фолбек 0% (без знижки)", abs(got - 176.24) < 0.01)

        # ПРЕ-ФІКС інваріант: без цього фіксу «Курчатко» пішло б за старим правилом (15%) —
        # доводимо, що тест реально стереже регрес, порівнюючи з тим, що дало б СТАРЕ правило.
        old_rule_price = 95 * (1 - cp.TOYSI_DISCOUNT_RATE)
        _chk("ПРЕ-ФІКС інваріант: старе правило дало б ІНШУ (вищу знижку) ціну, ніж спостереження",
             abs(old_rule_price - 90.25) > 1.0)

        # 5. САНІТАРНА МЕЖА (аудит PR перед мержем): аномальний pct НЕ записується й НЕ довіряється —
        #    захист від мовчазного відтворення оригінального money-збитку через ОДНУ брудну точку.
        cp.record_observed_discounts({
            "888": {"pct": 0.90, "observed_at": datetime.now().isoformat(timespec="seconds"),
                    "source_order": "corrupted"},
        })
        _chk("аномальний pct=0.90 (поза санітарною межею) НЕ записаний", cp.observed_discount_pct("888") is None)

        cp.record_observed_discounts({
            "889": {"pct": -0.05, "observed_at": datetime.now().isoformat(timespec="seconds"),
                    "source_order": "corrupted"},
        })
        _chk("від'ємний pct НЕ записаний", cp.observed_discount_pct("889") is None)

        # Межове значення (рівно на межі) — МАЄ пройти (межа інклюзивна)
        cp.record_observed_discounts({
            "890": {"pct": 0.20, "observed_at": datetime.now().isoformat(timespec="seconds"),
                    "source_order": "boundary"},
        })
        _chk("pct=0.20 (рівно на межі) записаний", cp.observed_discount_pct("890") == 0.20)

        # Змішаний батч: один валідний + один аномальний pid — валідний МАЄ пройти, аномальний — ні
        cp.record_observed_discounts({
            "891": {"pct": 0.05, "observed_at": datetime.now().isoformat(timespec="seconds"),
                    "source_order": "mixed"},
            "892": {"pct": 5.0, "observed_at": datetime.now().isoformat(timespec="seconds"),
                    "source_order": "mixed"},
        })
        _chk("змішаний батч: валідний pid записаний", cp.observed_discount_pct("891") == 0.05)
        _chk("змішаний батч: аномальний pid НЕ записаний", cp.observed_discount_pct("892") is None)


# ── 4: order_status_tracker._maybe_record_observed_discount ──
import order_status_tracker as ost  # noqa: E402


class _Cap:
    def __init__(self):
        self.recorded = []
        self.marked = []


def _run_capture(order, positions=None, raise_exc=False):
    cap = _Cap()
    ost.record_observed_discounts = lambda updates: cap.recorded.append(updates)
    ost.mark_discount_recorded = lambda conn, iid: cap.marked.append(iid)

    def _fetch(toysi_order_id):
        if raise_exc:
            raise RuntimeError("мережа впала")
        return positions

    ost.fetch_order_positions = _fetch
    ost._maybe_record_observed_discount(object(), order)
    return cap


def _order(**kw):
    base = dict(internal_order_id="eva_8-081895735", toysi_order_id="100452547",
                discount_recorded_at=None)
    base.update(kw)
    return base


# 4a. Живий кейс: order_positions з товаром + Збірка → знижка знята, Збірка пропущена
live_positions = {
    "positions_price": {"268183": "95", "33340": "15"},
    "positions_discount_price": {"268183": "90.25", "33340": "15"},
}
cap = _run_capture(_order(), positions=live_positions)
_chk("капчер: рівно 1 виклик record_observed_discounts", len(cap.recorded) == 1)
_chk("капчер: Збірка (33340) НЕ потрапила в записи", "33340" not in cap.recorded[0])
_chk("капчер: pid 268183 записаний з правильним pct (0.05)",
     abs(cap.recorded[0]["268183"]["pct"] - 0.05) < 0.001)
_chk("капчер: source_order = internal_order_id", cap.recorded[0]["268183"]["source_order"] == "eva_8-081895735")
_chk("капчер: mark_discount_recorded викликано", cap.marked == ["eva_8-081895735"])

# 4b. Ідемпотентність: discount_recorded_at уже стоїть → жодного виклику API/запису
cap = _run_capture(_order(discount_recorded_at="2026-09-27T10:00:00"), positions=live_positions)
_chk("ідемпотентність: discount_recorded_at уже є → без запису", cap.recorded == [] and cap.marked == [])

# 4c. Немає toysi_order_id → без виклику (немає що знімати)
cap = _run_capture(_order(toysi_order_id=None), positions=live_positions)
_chk("без toysi_order_id → без запису", cap.recorded == [] and cap.marked == [])

# 4d. Fail-open: API впав → жодного запису, НЕ падає (best-effort)
cap = _run_capture(_order(), raise_exc=True)
_chk("API впав: fail-open, без запису", cap.recorded == [] and cap.marked == [])

# 4e. order_positions повернув None (замовлення не знайдено) → без запису, без падіння
cap = _run_capture(_order(), positions=None)
_chk("order_positions=None → без запису, без падіння", cap.recorded == [] and cap.marked == [])


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Per-SKU кеш спостережених знижок Toysi: перемагає правило за категорією, "
      "фолбек не зламаний, капчер ідемпотентний і fail-open (аудит 2026-09-27)")
sys.exit(0)
