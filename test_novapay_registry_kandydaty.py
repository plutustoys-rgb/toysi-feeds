#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_novapay_registry_kandydaty.py — регрес-тест реєстру для novapay_registry_kandydaty.py
(Аудитор, КОДВ_CHANNEL.md, 2026-09-28, п.1 — "перевірити той самий клас курсора... в
NovaPay/RozetkaPay-кандидатах: їх у реєстрі теж немає").

Курсор `seen_ttn` (novapay_registry_cursor.json) росте монотонно — ТТН, побачений раз і не
внесений бухгалтером до наступного прогону, раніше зникав із кожного наступного звіту
НАЗАВЖДИ. Той самий клас бага, що вже виправлено для rozetka/eva_commission_ledger.py.

Резолюція тут — PRESENCE-based (чи ТТН тепер ЗНАЙДЕНО в Графі 5), не сума-в-тексті:
`_book_has` уже шукає точний №замовлення/ТТН, той самий критерій, що вирішує, чи взагалі
пропонувати кандидата при генерації.

Мережа/файли реєстрів НЕ потрібні: _book_has замокано. `python test_novapay_registry_kandydaty.py`
→ exit 0/1.
"""
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import novapay_registry_kandydaty as nr
import kandydaty_registry as kr

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


_tmp_reg = Path(tempfile.mktemp())
kr.REGISTRY_PATH = _tmp_reg

_BOOK_TTNS = set()


def _mock_book_has(order_id, ttn):
    return {"row": 1, "e_text": f"знайдено {ttn}"} if ttn in _BOOK_TTNS else {}


nr._book_has = _mock_book_has
nr._load_cursor = lambda: set()  # перший запуск = базова лінія

# ── 1: перший запуск — базова лінія, кандидатів немає ──
rows1 = [{"ttn": "20111111111111", "internal_order_id": "eva_8-1", "amount_received": 100,
          "commission": 5, "amount_net": 95, "buyer_name": "Тест", "date": "2026-09-28",
          "raw_order_ref": "ref1"}]
candidates1, batch1 = nr.collect(rows1)
_chk("перший запуск: кандидатів немає (базова лінія)", candidates1 == [])
_chk("перший запуск: ttn потрапив у batch", "20111111111111" in batch1)

# ── 2: другий прогін (курсор уже НЕ порожній — базова лінія пройдена), новий ТТН, не в книзі → кандидат ──
nr._load_cursor = lambda: {"20111111111111"}  # непорожній курсор (не перший запуск), не містить новий ttn
rows2 = [{"ttn": "20222222222222", "internal_order_id": "eva_8-2", "amount_received": 200,
          "commission": 10, "amount_net": 190, "buyer_name": "Тест2", "date": "2026-09-28",
          "raw_order_ref": "ref2"}]
_BOOK_TTNS.clear()
candidates2, batch2 = nr.collect(rows2)
_chk("2-й прогін, не в курсорі, не в книзі: кандидат є", len(candidates2) == 1)
sync_result = nr.sync_registry(candidates2)
_chk("sync_registry: відкрито", sync_result["newly_opened"] == ["novapay_registry:20222222222222"])

# ПРЕ-ФІКС ВІДТВОРЕННЯ: курсор тепер "бачив" цей ТТН — collect() більше НЕ поверне його як
# кандидата, навіть якщо бухгалтер так і не вніс платіж у книгу. Раніше факт зникав тут назавжди.
nr._load_cursor = lambda: {"20222222222222"}
candidates3, _ = nr.collect(rows2)
_chk("ПРЕ-ФІКС інваріант: наступний прогін НЕ бачить кандидата (курсор уже містить ТТН) "
     "— це й був корінь бага", candidates3 == [])
_chk("ФІКС: кандидат УСЕ ОДНО 'open' у реєстрі — курсор джерела його не стирає",
     kr._load_registry(_tmp_reg)["novapay_registry:20222222222222"]["status"] == "open")

# ── 3: resolve_against_book() — ТТН тепер знайдено в книзі → закрито ──
_BOOK_TTNS.add("20222222222222")
resolved = nr.resolve_against_book()
_chk("resolve_against_book: закрито (ТТН знайдено в книзі)",
     resolved["resolved"] == ["novapay_registry:20222222222222"])
_chk("реєстр: status=resolved", kr._load_registry(_tmp_reg)["novapay_registry:20222222222222"]["status"] == "resolved")

# ── 4: ТТН НЕ в книзі — лишається open ──
_tmp_reg2 = Path(tempfile.mktemp())
kr.REGISTRY_PATH = _tmp_reg2
kr._save_registry({
    "novapay_registry:20333333333333": {"source": "novapay_registry", "key": "20333333333333",
                                          "summary": "x", "sum": 50.0, "date": "x", "status": "open",
                                          "first_seen": "2026-09-01"},
}, path=_tmp_reg2)
_BOOK_TTNS.clear()
resolved4 = nr.resolve_against_book()
_chk("resolve_against_book: НЕ закрито, якщо ТТН відсутній у книзі", resolved4["resolved"] == [])
_chk("реєстр: усе ще open", kr._load_registry(_tmp_reg2)["novapay_registry:20333333333333"]["status"] == "open")


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Реєстр NovaPay-кандидатів незалежний від курсора seen_ttn — "
      "факт не зникає, поки ТТН не знайдено в книзі (аудит 2026-09-28)")
sys.exit(0)
