#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_rz_delivery_label.py — регрес-тест RZ-Delivery-наклейки (інцидент Toysi №100451689).

Що сталось у бойовому: юзербот сказав Toysi «Наклейка ТТН — у файлі», але ФАЙЛА не було.
Корінь (звірено живо 2026-09-16 на 906231670): маркування друкувало КУР'ЄРСЬКИЙ track_num
('723-3467245'), який /delivery-rozetka/ttn-print-batch відхиляє (code 1005), а фолбек
слав ТЕКСТ, що брехливо казав «у файлі». Друк приймає лише RMP-номер ('RMP-834041233').

ІНВАРІАНТИ, що тут закріплюються:
  1. extract_delivery_ttn → пріоритет RMP track_num над carrier_track_num.
  2. printable_delivery_ttn → self-heal: кур'єрський номер → канонічний RMP із замовлення.
  3. _maybe_send_rz_delivery_marking:
     • наклейку тягне за RMP-номером (self-heal з кур'єрського);
     • sent=True ЛИШЕ коли файл РЕАЛЬНО пішов (send_marking_file);
     • на збої файлу — НЕ шле текст (жодного «у файлі» без файлу), sent=False (ретрай);
     • підпис файлу містить «Наклейка ТТН — у файлі» лише для реально надісланого файлу.

Самодостатній: `python test_rz_delivery_label.py` → exit 0/1. Мережа/БД/telegram — замокані,
бойові дані не чіпаються.
"""
import sys
import types

import rozetka_client
import order_router as orr

_FAILS = []


def _check(name, got, exp):
    ok = got == exp
    print(f"[{'OK ' if ok else 'FAIL'}] {name}: got={got!r} exp={exp!r}")
    if not ok:
        _FAILS.append(name)


def _true(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


# ── 1. extract_delivery_ttn: пріоритет RMP ────────────────────────────────────
_check("extract: RMP track_num має пріоритет над carrier",
       rozetka_client.extract_delivery_ttn(
           {"carrier_track_num": "723-3467245", "track_num": "RMP-834041233"}),
       "RMP-834041233")
_check("extract: лише carrier → крайній фолбек",
       rozetka_client.extract_delivery_ttn({"carrier_track_num": "723-3467245"}),
       "723-3467245")
_check("extract: RMP з original_info (JSON-рядок)",
       rozetka_client.extract_delivery_ttn(
           {"carrier_track_num": "723-0000000",
            "original_info": '{"track_num": "RMP-999"}'}),
       "RMP-999")
_check("extract: нема нічого → None",
       rozetka_client.extract_delivery_ttn({}), None)


# ── 2. printable_delivery_ttn: self-heal ──────────────────────────────────────
def _fake_details(order_id):
    return {"content": {"ttn": "RMP-834041233"}}


_orig_details = rozetka_client.get_order_details
rozetka_client.get_order_details = _fake_details
try:
    _check("printable: RMP на вході → без запиту, як є",
           rozetka_client.printable_delivery_ttn(906231670, "RMP-834041233"),
           "RMP-834041233")
    _check("printable: кур'єрський → канонічний RMP із замовлення",
           rozetka_client.printable_delivery_ttn(906231670, "723-3467245"),
           "RMP-834041233")
    _check("printable: None → канонічний RMP із замовлення",
           rozetka_client.printable_delivery_ttn(906231670, None),
           "RMP-834041233")
finally:
    rozetka_client.get_order_details = _orig_details


# ── 3. Поведінка маркування ───────────────────────────────────────────────────
def _order(**kw):
    base = dict(
        internal_order_id="rztest_1", order_id=906231670, platform="rozetka",
        carrier="rozetka_delivery", toysi_order_id="100451689",
        customer_name="Тест Тестенко", phone="+380500000000", payment_method="prepaid",
        np_branch="Відділення №1", items=[{"toysi_code": "1", "name": "x", "qty": 1, "price": 100}],
    )
    base.update(kw)
    return base


class _Cap:
    """Захоплює виклики telegram-функцій + DB-маркерів."""
    def __init__(self):
        self.file_calls = []
        self.text_calls = []
        self.sent_marker = 0
        self.alerts = 0


def _run_marking(cap, *, ttn_created, label_ok, stored_ttn=None):
    """Проганяє _maybe_send_rz_delivery_marking з мок-оточенням, повертає sent."""
    # DB-хелпери, імпортовані в order_router — на no-op/capture
    orr.bump_rz_marking_attempt = lambda conn, iid: 1
    orr.mark_rozetka_delivery_ttn = lambda conn, iid, ttn: None
    orr.mark_rz_marking_sent = lambda conn, iid: setattr(cap, "sent_marker", cap.sent_marker + 1)
    orr._alert_marking_failed = lambda order, attempts, ttn: setattr(cap, "alerts", cap.alerts + 1)

    # rozetka_client — мок ТТН/наклейки
    orr.rozetka_client.create_delivery_ttn = lambda *a, **k: {"ok": True}
    orr.rozetka_client.extract_delivery_ttn = lambda resp: ttn_created  # напр. кур'єрський/None
    orr.rozetka_client.printable_delivery_ttn = (
        lambda order_id, known: ("RMP-834041233" if known else None))

    def _fetch(t):
        if not label_ok:
            raise rozetka_client.RozetkaAPIError("ttn-print-batch: code=1005")
        assert t == "RMP-834041233", f"наклейку тягнуть НЕ за RMP: {t!r}"
        return b"%PDF-1.4 fake"
    orr.rozetka_client.fetch_delivery_label = _fetch

    # fake telegram_userbot_client у sys.modules (order_router імпортує його всередині функції)
    fake_tg = types.ModuleType("telegram_userbot_client")
    fake_tg.send_marking_file = lambda pdf, filename=None, caption=None, to_toysi=False: (
        cap.file_calls.append({"filename": filename, "caption": caption}) or True)
    fake_tg.send_marking = lambda text, to_toysi=False: (
        cap.text_calls.append(text) or True)
    sys.modules["telegram_userbot_client"] = fake_tg

    order = _order(rozetka_delivery_ttn=stored_ttn)
    return orr._maybe_send_rz_delivery_marking(object(), order)


# Case A: ТТН створено (кур'єрський), наклейка ОК → файл пішов, тексту НЕ шлемо
capA = _Cap()
sentA = _run_marking(capA, ttn_created="723-3467245", label_ok=True)
_true("A: sent=True (файл реально пішов)", sentA is True)
_check("A: send_marking_file викликано рівно раз", len(capA.file_calls), 1)
_check("A: send_marking (текст) НЕ викликано", len(capA.text_calls), 0)
_true("A: підпис файлу каже «у файлі»",
      bool(capA.file_calls) and "у файлі" in capA.file_calls[0]["caption"])
_true("A: у назві файлу RMP-номер",
      bool(capA.file_calls) and "RMP-834041233" in capA.file_calls[0]["filename"])
_true("A: у підписі ТТН = RMP",
      bool(capA.file_calls) and "RMP-834041233" in capA.file_calls[0]["caption"])
_check("A: rz_marking_sent позначено", capA.sent_marker, 1)

# Case B: наклейка ПАДАЄ → НЕ шлемо текст-брехню, sent=False, ретрай (без алерту, спроб<MAX)
capB = _Cap()
sentB = _run_marking(capB, ttn_created="723-3467245", label_ok=False)
_true("B: sent=False (файл не пішов)", sentB is False)
_check("B: send_marking_file НЕ зараховано (raise)", len(capB.file_calls), 0)
_check("B: send_marking (текст) НЕ викликано — жодної брехні «у файлі»", len(capB.text_calls), 0)
_check("B: rz_marking_sent НЕ позначено (ретрай допробує)", capB.sent_marker, 0)
_check("B: алерт не спрацював (спроб < MAX)", capB.alerts, 0)

# Case C: ТТН не створено (None) → нічого не шлемо, sent=False
capC = _Cap()
sentC = _run_marking(capC, ttn_created=None, label_ok=True)
_true("C: sent=False (нема ТТН)", sentC is False)
_check("C: файл не слався", len(capC.file_calls), 0)
_check("C: текст не слався", len(capC.text_calls), 0)


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Усі інваріанти RZ-Delivery-наклейки виконано")
sys.exit(0)
