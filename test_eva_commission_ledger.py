#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_eva_commission_ledger.py — регрес-тест статусного фільтра eva_commission_ledger.py
(аудит КОДВ-автоматики 2026-09-22, черга 2, баг (7): картка EVA показує «Сума комісії»
НЕЗАЛЕЖНО від фактичного статусу замовлення — скасоване/невдала спроба оплати все одно
має ненульову «Всього», тож старий `if total<=0: continue` цей клас не ловить.

Живо звірено (2026-09-23, реальна картка seller.eva.ua): пара 8-081381904 (Скасовано
покупцем / Помилка оплати) проти 8-081381964 (Отримано / Оплачено) — той самий товар,
той самий покупець, та сама сума 72.68₴; 904 — невдала перша спроба, 964 — успішний
повторний платіж. Текст innerText нижче — дослівний фрагмент, знятий з живої картки.

Мережа/Playwright НЕ потрібні: тестуються лише regex-парсери статусу на застиглому тексті.
`python test_eva_commission_ledger.py` → exit 0/1.
"""
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import eva_commission_ledger as ec
import kandydaty_registry as kr

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


# Живий фрагмент картки 8-081381904 (скасовано покупцем, невдала оплата) — 2026-09-23
_TXT_CANCELLED = (
    "Замовлення № 8-081381904 від 16.09.2026\nЗмінити статус\nІсторія замовлення\n"
    "Статус замовлення\nСкасовано покупцем\nНе оплачено\nСума замовлення\n484.55 ₴\n"
    "Сума комісії \nВсього\n72.68 ₴\nЗ рахунку ТМ\n43.61 ₴\nЗ рахунку платформи\n29.07 ₴\n"
    "Покупець\nКлієнт\nСпажева Ірина Олександрівна\n"
    "Оплата\nВсього\n484.55 ₴\nСпосіб оплати\nLIQPAY\nСтатус оплати\nПомилка оплати\nТовари"
)

# Живий фрагмент картки 8-081381964 (успішний повторний платіж того самого покупця)
_TXT_OK = (
    "Замовлення № 8-081381964 від 16.09.2026\nЗмінити статус\nІсторія замовлення\n"
    "Статус замовлення\nОтримано\nСума замовлення\n484.55 ₴\n"
    "Сума комісії \nВсього\n72.68 ₴\nЗ рахунку ТМ\n43.61 ₴\nЗ рахунку платформи\n29.07 ₴\n"
    "Покупець\nКлієнт\nСпажева Ірина Олександрівна\n"
    "Оплата\nВсього\n484.55 ₴\nСпосіб оплати\nLIQPAY\nСтатус оплати\nОплачено\nТовари"
)

# ── 1: живий кейс — розпізнавання статусів ──
os_c, ps_c = ec._order_statuses(_TXT_CANCELLED)
_chk("скасоване: статус замовлення розпізнано", os_c == "Скасовано покупцем")
_chk("скасоване: статус оплати розпізнано", ps_c == "Помилка оплати")

os_ok, ps_ok = ec._order_statuses(_TXT_OK)
_chk("успішне: статус замовлення розпізнано", os_ok == "Отримано")
_chk("успішне: статус оплати розпізнано", ps_ok == "Оплачено")

# ── 2: живий кейс — рішення про пропуск ──
_chk("живий рецидив 904: _is_cancelled_or_failed → True (пропустити)",
     ec._is_cancelled_or_failed(os_c, ps_c) is True)
_chk("живий контроль 964 (успішний дубль тієї самої суми): _is_cancelled_or_failed → False",
     ec._is_cancelled_or_failed(os_ok, ps_ok) is False)

# ── 3: варіації — "Скасовано*" ловить будь-який суфікс (продавцем, системою тощо) ──
_chk("'Скасовано продавцем' теж ловиться (wildcard)",
     ec._is_cancelled_or_failed("Скасовано продавцем", "Оплачено") is True)

# ── 4: тільки статус оплати «Помилка оплати» без скасування замовлення — теж пропуск ──
_chk("замовлення НЕ скасоване, але оплата не пройшла — усе одно пропуск",
     ec._is_cancelled_or_failed("Обробляється", "Помилка оплати") is True)

# ── 5: немає жодного зі стоп-маркерів — не пропускаємо ──
_chk("звичайне замовлення в обробці, оплата пройшла — НЕ пропускаємо",
     ec._is_cancelled_or_failed("Обробляється", "Оплачено") is False)

# ── 6: текст без розпізнаваних статусів (зміна розмітки) — порожні рядки, НЕ пропускаємо
#      наосліп (fail-safe: невідомий статус ≠ скасований, краще показати кандидата на
#      ручну перевірку, ніж мовчки проковтнути) ──
_chk("немає match (порожні рядки): _is_cancelled_or_failed → False (fail-safe, не ховаємо)",
     ec._is_cancelled_or_failed("", "") is False)
os_none, ps_none = ec._order_statuses("зовсім інша розмітка без відомих міток")
_chk("_order_statuses на невідомому тексті: обидва порожні, не падає", os_none == "" and ps_none == "")


# ── 7-8: реєстр незалежний від processed_ids (Аудитор, КОДВ_CHANNEL.md, 2026-09-28, п.1 —
# "перевірити той самий клас курсора в eva_commission_ledger.py") — той самий фікс, що
# rozetka_commission_ledger.py: processed_ids росте монотонно, кандидат, не внесений до
# наступного прогону, раніше зникав НАЗАВЖДИ (жоден інший механізм його не пам'ятав).
_tmp_reg = Path(tempfile.mktemp())
kr.REGISTRY_PATH = _tmp_reg

_BOOK = {}


def _mock_book_lookup(order_id):
    return _BOOK.get(order_id, {"book_row": None, "book_e_text": None})


ec._load_cursor = lambda: {"processed_ids": []}
ec._book_lookup = _mock_book_lookup
ec.fetch_commissions = lambda: [
    {"order_id": "8-900001", "commission_total": 30.99, "commission_tm": 18.59, "commission_platform": 12.40},
]
candidates7, processed7 = ec.collect()
_chk("collect: новий кандидат знайдено", len(candidates7) == 1)
sync_result = ec.sync_registry(candidates7)
_chk("sync_registry: відкрито", sync_result["newly_opened"] == ["eva_commission:8-900001"])
_chk("реєстр: sum = commission_total", kr._load_registry(_tmp_reg)["eva_commission:8-900001"]["sum"] == 30.99)

# ПРЕ-ФІКС ВІДТВОРЕННЯ: processed_ids тепер містить 8-900001 (курсор "бачив") — collect() його
# більше НЕ поверне, навіть якщо бухгалтер так і не вніс суму в книгу. Раніше факт зникав тут.
ec._load_cursor = lambda: {"processed_ids": processed7}
candidates8, _ = ec.collect()
_chk("ПРЕ-ФІКС інваріант: наступний прогін дійсно НЕ бачить кандидата (processed_ids уже містить) "
     "— це й був корінь бага", candidates8 == [])
_chk("ФІКС: кандидат УСЕ ОДНО 'open' у реєстрі — курсор джерела його не стирає",
     kr._load_registry(_tmp_reg)["eva_commission:8-900001"]["status"] == "open")

# resolve_against_book(): сума з'явилась у Графі 5 → закрито
_BOOK["8-900001"] = {"book_row": 50, "book_e_text": "EVA №8-900001, комісія 30,99 внесено."}
resolved = ec.resolve_against_book()
_chk("resolve_against_book: закрито (сума 30,99 знайдена в Графі 5)",
     resolved["resolved"] == ["eva_commission:8-900001"])
_chk("реєстр: status=resolved", kr._load_registry(_tmp_reg)["eva_commission:8-900001"]["status"] == "resolved")

# collect(ignore_cursor=True) — бекфіл (аудит 2026-09-29, п.1а): та сама умова, "processed_ids
# уже містить 8-900001", але ignore_cursor=True все одно повертає його.
backfill_candidates, _ = ec.collect(ignore_cursor=True)
_chk("collect(ignore_cursor=True): замовлення, вже в processed_ids, ВСЕ ОДНО повертається",
     any(c["order_id"] == "8-900001" for c in backfill_candidates))


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Статусний фільтр EVA (скасовано/помилка оплати): живий рецидив 904 vs 964, "
      "варіації, fail-safe на невідомій розмітці — усі коректно.")
sys.exit(0)
