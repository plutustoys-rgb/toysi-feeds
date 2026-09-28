#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_rozetkapay_registry_kandydaty.py — регрес-тест реєстру для rozetkapay_registry_kandydaty.py
(Аудитор, КОДВ_CHANNEL.md, 2026-09-28, п.1 — "перевірити той самий клас курсора... в
NovaPay/RozetkaPay-кандидатах: їх у реєстрі теж немає").

Курсор `seen_finop` росте монотонно — фінансова операція (сторно чи еквайринг), побачена раз
і не внесена бухгалтером до наступного прогону, раніше зникала НАЗАВЖДИ. Ключ реєстру —
"order_id:kind" (storno/acquiring окремо — те саме замовлення може мати обидва одночасно).
Сторно рахується за abs(sum) (книга документує сторно позитивною сумою, не з мінусом).

Мережа/файли реєстрів НЕ потрібні: _lookup_book_row замокано. Джерело `source_freshness`
НЕ викликається тут (main() не тестується напряму, лише collect/sync_registry/resolve_against_book).
`python test_rozetkapay_registry_kandydaty.py` → exit 0/1.
"""
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import rozetkapay_registry_kandydaty as rp
import kandydaty_registry as kr

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


def _row(finop, order_id, sum_val, commission, type_="", project="Rozetka.ua"):
    return {"seq": "1", "date_pay": "2026-09-28", "date_transfer": "2026-09-28",
            "sum": sum_val, "commission": commission, "project": project,
            "order_id": order_id, "type": type_, "finop": finop}


_tmp_reg = Path(tempfile.mktemp())
kr.REGISTRY_PATH = _tmp_reg

_BOOK = {}


def _mock_lookup(order_id):
    return _BOOK.get(order_id, {})


rp._lookup_book_row = _mock_lookup

# ── 1: перший запуск — базова лінія ──
rp._load_cursor = lambda: set()
candidates1, batch1 = rp.collect([_row("F1", "900001", -133.50, -5.0, type_="Повернення")])
_chk("перший запуск: кандидатів немає (базова лінія)", candidates1 == [])

# ── 2: сторно, не в курсорі → кандидат ──
rp._load_cursor = lambda: {"F0"}  # непорожній (не перший запуск), не містить F1
_BOOK.clear()
_BOOK["900001"] = {"row": 50, "current_i9": 100.0, "e_text": "Rozetka №900001, ще не сторновано."}
candidates2, batch2 = rp.collect([_row("F1", "900001", -133.50, -5.0, type_="Повернення")])
_chk("2-й прогін, сторно, не в курсорі: кандидат є", len(candidates2) == 1 and candidates2[0]["kind"] == "storno")
sync_result = rp.sync_registry(candidates2)
_chk("sync_registry: відкрито з ключем order_id:kind", sync_result["newly_opened"] == ["rozetkapay_registry:900001:storno"])
reg = kr._load_registry(_tmp_reg)
_chk("реєстр: sum = abs(sum) (133.50, не -133.50)", reg["rozetkapay_registry:900001:storno"]["sum"] == 133.50)

# ПРЕ-ФІКС ВІДТВОРЕННЯ: курсор тепер "бачив" F1 — collect() більше не поверне цю операцію,
# навіть якщо бухгалтер так і не відреагував на сторно. Раніше факт зникав тут назавжди.
rp._load_cursor = lambda: {"F0", "F1"}
candidates3, _ = rp.collect([_row("F1", "900001", -133.50, -5.0, type_="Повернення")])
_chk("ПРЕ-ФІКС інваріант: наступний прогін НЕ бачить кандидата (курсор уже містить F1) "
     "— це й був корінь бага", candidates3 == [])
_chk("ФІКС: кандидат УСЕ ОДНО 'open' у реєстрі — курсор джерела його не стирає",
     kr._load_registry(_tmp_reg)["rozetkapay_registry:900001:storno"]["status"] == "open")

# ── 3: resolve_against_book() — сума з'явилась (позитивна!) у Графі 5 → закрито ──
_BOOK["900001"]["e_text"] = "Rozetka №900001, сторновано 133,50 грн 28.09.2026."
resolved = rp.resolve_against_book()
_chk("resolve_against_book: закрито (133,50 знайдено в Графі 5, попри мінус у джерелі)",
     resolved["resolved"] == ["rozetkapay_registry:900001:storno"])
_chk("реєстр: status=resolved", kr._load_registry(_tmp_reg)["rozetkapay_registry:900001:storno"]["status"] == "resolved")

# ── 4: еквайринг-кандидат — окремий ключ (той самий order_id) ──
_tmp_reg2 = Path(tempfile.mktemp())
kr.REGISTRY_PATH = _tmp_reg2
rp._load_cursor = lambda: {"F0"}
_BOOK.clear()
_BOOK["900002"] = {"row": 60, "current_i9": 40.0, "e_text": "Rozetka №900002, роялті внесено."}
candidates4, _ = rp.collect([_row("F2", "900002", 300.0, -6.0)])
_chk("еквайринг: kind=acquiring", candidates4[0]["kind"] == "acquiring")
sync4 = rp.sync_registry(candidates4)
_chk("sync_registry: ключ order_id:acquiring", sync4["newly_opened"] == ["rozetkapay_registry:900002:acquiring"])
_chk("реєстр: sum = acquiring (6.0, не sum замовлення 300.0)",
     kr._load_registry(_tmp_reg2)["rozetkapay_registry:900002:acquiring"]["sum"] == 6.0)

# storno і acquiring для ОДНОГО order_id — незалежні записи (не перезаписують один одного)
rp._load_cursor = lambda: {"F0", "F2"}
_BOOK["900002"]["e_text"] = "Rozetka №900002, ще не внесено."
candidates5, _ = rp.collect([_row("F3", "900002", -50.0, -2.0, type_="Повернення")])
_chk("той самий order_id, ІНША операція (сторно): теж кандидат", candidates5[0]["kind"] == "storno")
sync5 = rp.sync_registry(candidates5)
reg5 = kr._load_registry(_tmp_reg2)
_chk("реєстр: обидва записи (storno + acquiring) співіснують для 900002",
     "rozetkapay_registry:900002:storno" in reg5 and "rozetkapay_registry:900002:acquiring" in reg5)


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Реєстр RozetkaPay-кандидатів (сторно/еквайринг) незалежний від курсора seen_finop — "
      "факт не зникає, поки сума не знайдена в книзі (аудит 2026-09-28)")
sys.exit(0)
