#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_rozetka_commission_ledger.py — регрес-тест reserve_warning (rozetka_commission_ledger.py,
аудит КОДВ-автоматики 2026-09-22, черга 2, баг (6): кабінет пише спершу "Резервування суми",
потім, окремим прогоном, фіналізовану "Комісія за продаж" на ТУ САМУ суму. Бухгалтер інколи
вже вносить резерв у поточне I9 вручну з приміткою "(роялті ще «Резервування»..." у графі 5
(звірено живо, рядки 91/92/95/98 книги, 2026-09-23) — якщо скрипт пізніше пропонує додати
Δ (фіналізовану комісію) поверх, виходить подвійний облік. Фікс — НЕ автоматичне вирахування
(парсинг суми резерву з вільного людського тексту крихкий), а явне попередження, коли графа 5
згадує "резервування" — рішення лишається за роллю «агент-бухгалтер».

Мережа/Playwright НЕ потрібні: fetch_royalty_rows/fetch_logistic_rows/_load_cursor/
_lookup_book_row замокано. `python test_rozetka_commission_ledger.py` → exit 0/1.
"""
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import rozetka_commission_ledger as rc
import kandydaty_registry as kr

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


def _royalty_row(log_id, order_id, sum_uah, type_title=rc.SALE_COMMISS_TITLE):
    return {"log_id": log_id, "date": "2026-09-16", "type_title": type_title,
            "order_id": order_id, "debit": sum_uah}


_BOOK = {}  # order_id → {"row": N, "current_i9": X, "e_text": "..."}


def _mock_lookup(order_id):
    return _BOOK.get(order_id, {})


rc._load_cursor = lambda: {"last_royalty_log_id": 100, "last_logistics_operation_id": 100}
rc.fetch_logistic_rows = lambda page: []
rc._lookup_book_row = _mock_lookup

# ── 1: живий кейс — графа 5 явно згадує "резервування" (рядок 91 книги, 2026-09-23) ──
_BOOK.clear()
_BOOK["905803580"] = {
    "row": 91, "current_i9": 21.92,
    "e_text": "Rozetka №905803580 (Ярьоменко), картка, чек Checkbox серіал 67 від 11.09.2026. "
              "i9=19.80 (роялті ще «Резервування», НЕ фінал, seller.rozetka.com.ua, 16.09).",
}
rc.fetch_royalty_rows = lambda page: [_royalty_row(101, "905803580", 19.80)]
candidates, _, _ = rc.collect(page=None)
_chk("живий кейс 91: candidate знайдено", len(candidates) == 1)
c = candidates[0]
_chk("живий кейс 91: reserve_warning=True (графа 5 згадує 'резервування')", c["reserve_warning"] is True)
_chk("живий кейс 91: delta_i9 і далі рахується (скрипт лише попереджає, не приховує)", c["delta_i9"] == 19.80)
_chk("живий кейс 91: book_proposed_i9 і далі показується (рішення — за бухгалтером)",
     c["book_proposed_i9"] == round(21.92 + 19.80, 2))

# ── 2: звичайне замовлення БЕЗ згадки резерву в графі 5 — reserve_warning=False ──
_BOOK.clear()
_BOOK["906058699"] = {
    "row": 200, "current_i9": 10.0,
    "e_text": "Rozetka №906058699, картка, чек Checkbox серіал 99 від 20.09.2026.",
}
rc.fetch_royalty_rows = lambda page: [_royalty_row(102, "906058699", 25.0)]
candidates2, _, _ = rc.collect(page=None)
_chk("звичайне замовлення: reserve_warning=False", candidates2[0]["reserve_warning"] is False)

# ── 3: заголовна буква/відмінок — регістронезалежність підрядкового пошуку ──
_BOOK.clear()
_BOOK["906100000"] = {
    "row": 201, "current_i9": 5.0,
    "e_text": "Rozetka №906100000. РЕЗЕРВУВАННЯ суми ще не фіналізоване.",
}
rc.fetch_royalty_rows = lambda page: [_royalty_row(103, "906100000", 12.0)]
candidates3, _, _ = rc.collect(page=None)
_chk("регістронезалежність: 'РЕЗЕРВУВАННЯ' (капс) теж ловиться", candidates3[0]["reserve_warning"] is True)

# ── 4: замовлення відсутнє в книзі взагалі — reserve_warning=False (нема графи 5, щоб перевіряти) ──
_BOOK.clear()
rc.fetch_royalty_rows = lambda page: [_royalty_row(104, "906200000", 8.0)]
candidates4, _, _ = rc.collect(page=None)
_chk("немає в книзі: reserve_warning=False (нема e_text)", candidates4[0]["reserve_warning"] is False)
_chk("немає в книзі: book_current_i9=None, book_proposed_i9=None", candidates4[0]["book_current_i9"] is None and candidates4[0]["book_proposed_i9"] is None)


# ── 5-8: реєстр незалежний від курсора (Аудитор, КОДВ_CHANNEL.md, 2026-09-28, п.1) —
# живий інцидент: роялті №906224962 (47,18) було в звіті 19.09, курсор пішов далі, факт зник
# із кожного НАСТУПНОГО звіту назавжди. Фікс: sync_registry() тримає кандидата persistent,
# незалежно від того, чи collect() бачить його як "новий" цього прогону; resolve_against_book()
# закриває ЛИШЕ коли сума з'явилась у Графі 5 книги (критерій Аудитора).
_tmp_reg = Path(tempfile.mktemp())
kr.REGISTRY_PATH = _tmp_reg

_BOOK.clear()
_BOOK["906224962"] = {"row": 110, "current_i9": 1.04, "e_text": "Rozetka №906224962, ще не внесено."}
rc.fetch_royalty_rows = lambda page: [_royalty_row(200, "906224962", 47.18)]
candidates5, _, _ = rc.collect(page=None)
sync_result = rc.sync_registry(candidates5)
_chk("sync_registry: новий кандидат відкрито (ключ order_id:royalty)",
     sync_result["newly_opened"] == ["rozetka_commission:906224962:royalty"])
reg = kr._load_registry(_tmp_reg)
_chk("реєстр: запис status=open", reg["rozetka_commission:906224962:royalty"]["status"] == "open")
_chk("реєстр: sum = royalty_new (47.18)", reg["rozetka_commission:906224962:royalty"]["sum"] == 47.18)

# ПРЕ-ФІКС ВІДТВОРЕННЯ живого інциденту: наступний прогін кабінет уже НЕ показує цей рядок
# (курсор пройшов повз / рядок випав за межу вікна ~20) — collect() мовчить про нього.
# Саме тут факт раніше зникав НАЗАВЖДИ, бо нічого, крім курсора, його не пам'ятало.
rc.fetch_royalty_rows = lambda page: []
candidates6, _, _ = rc.collect(page=None)
_chk("ПРЕ-ФІКС інваріант: наступний прогін дійсно НЕ бачить кандидата як 'новий' "
     "(це й був корінь бага — без реєстру факт зник би тут безслідно)", candidates6 == [])
reg_after = kr._load_registry(_tmp_reg)
_chk("ФІКС: кандидат УСЕ ОДНО 'open' у реєстрі — мовчання collect() його не стирає",
     reg_after["rozetka_commission:906224962:royalty"]["status"] == "open")

# ── 6: resolve_against_book() — сума з'явилась у Графі 5 → закрито ──
_BOOK["906224962"]["e_text"] = "Rozetka №906224962, роялті 47,18 внесено 28.09.2026."
resolved = rc.resolve_against_book()
_chk("resolve_against_book: закрито (сума 47,18 знайдена в Графі 5)",
     resolved["resolved"] == ["rozetka_commission:906224962:royalty"])
_chk("реєстр: status=resolved", kr._load_registry(_tmp_reg)["rozetka_commission:906224962:royalty"]["status"] == "resolved")

# ── 7: сума в графі 5 ще НЕ з'явилась (звичайний, найчастіший випадок) — лишається open ──
_BOOK.clear()
_BOOK["906267890"] = {"row": 111, "current_i9": 3.13, "e_text": "Rozetka №906267890, ще не внесено."}
rc.fetch_royalty_rows = lambda page: [_royalty_row(201, "906267890", 135.21)]
candidates7, _, _ = rc.collect(page=None)
rc.sync_registry(candidates7)
resolved7 = rc.resolve_against_book()
_chk("resolve_against_book: НЕ закрито, якщо сума ще не в Графі 5",
     "rozetka_commission:906267890:royalty" not in resolved7["resolved"])
_chk("реєстр: 906267890:royalty усе ще open",
     kr._load_registry(_tmp_reg)["rozetka_commission:906267890:royalty"]["status"] == "open")

# ── 7б: РОЯЛТІ + ЛОГІСТИКА разом — ОКРЕМІ записи, не комбінована сума (аудит 2026-09-29, п.1б:
# бухгалтер пише компоненти окремо, комбінована Δ могла б ніколи не з'явитись текстом) ──
_BOOK.clear()
_BOOK["906999999"] = {"row": 200, "current_i9": 0.0, "e_text": "Rozetka №906999999, нічого не внесено."}
rc.fetch_royalty_rows = lambda page: [_royalty_row(300, "906999999", 94.61)]
rc.fetch_logistic_rows = lambda page: [{"operation_id": 300, "date": "2026-09-29",
                                        "type_title": rc.LOGISTIC_SPECIAL_TITLE,
                                        "order_id": "906999999", "ttn": "20111111111111", "debit": 10.20}]
candidates7b, _, _ = rc.collect(page=None)
_chk("комбінований кандидат: роялті+логістика в ОДНОМУ candidate (delta_i9)",
     candidates7b[0]["royalty_new"] == 94.61 and candidates7b[0]["logistics_new"] == 10.20
     and candidates7b[0]["delta_i9"] == 104.81)
sync7b = rc.sync_registry(candidates7b)
_chk("sync_registry: ДВА окремих записи, не один комбінований",
     set(sync7b["newly_opened"]) == {"rozetka_commission:906999999:royalty", "rozetka_commission:906999999:logistics"})
reg7b = kr._load_registry(_tmp_reg)
_chk("реєстр: sum роялті-запису = 94.61 (компонент, НЕ 104.81)",
     reg7b["rozetka_commission:906999999:royalty"]["sum"] == 94.61)
_chk("реєстр: sum логістика-запису = 10.20 (компонент, НЕ 104.81)",
     reg7b["rozetka_commission:906999999:logistics"]["sum"] == 10.20)

# Бухгалтер вносить лише логістику (типовий випадок з живих даних Аудитора — рядок 118
# "стало 10.2 (+10.2)") — роялті-компонент лишається open, логістика закривається окремо.
_BOOK["906999999"]["e_text"] = "Rozetka №906999999, стало 10.2 (+10.2)."
resolved7b = rc.resolve_against_book()
_chk("resolve_against_book: логістика закрита (числове порівняння: '10.2' == 10.20)",
     "rozetka_commission:906999999:logistics" in resolved7b["resolved"])
_chk("resolve_against_book: роялті (94.61) ЛИШАЄТЬСЯ open — комбінована сума ще НЕ шукається",
     "rozetka_commission:906999999:royalty" not in resolved7b["resolved"])
_chk("реєстр: роялті-компонент усе ще open", kr._load_registry(_tmp_reg)["rozetka_commission:906999999:royalty"]["status"] == "open")
_chk("реєстр: логістика-компонент resolved", kr._load_registry(_tmp_reg)["rozetka_commission:906999999:logistics"]["status"] == "resolved")
rc.fetch_logistic_rows = lambda page: []  # не протікає в наступні секції файлу

# ── 8: _delta_applied_in_book — формати кома/крапка, межові випадки ──
_chk("_delta_applied_in_book: кома у книзі, крапка в delta", rc._delta_applied_in_book("сума 47,18 внесена", 47.18))
_chk("_delta_applied_in_book: крапка в обох", rc._delta_applied_in_book("сума 47.18 внесена", 47.18))
_chk("_delta_applied_in_book: сума відсутня в тексті", not rc._delta_applied_in_book("щось інше", 47.18))
_chk("_delta_applied_in_book: delta=None", not rc._delta_applied_in_book("47,18", None))
_chk("_delta_applied_in_book: e_text порожній", not rc._delta_applied_in_book("", 47.18))

# ── 9: collect(ignore_cursor=True) — бекфіл (аудит 2026-09-29, п.1а): факти СТАРШІ за курсор
# (log_id/operation_id ≤ те, що вже "бачив" курсор) досі повертаються, коли ignore_cursor=True —
# нормальний collect() їх би мовчки пропустив (та сама причина, чому факти губились назавжди).
rc._load_cursor = lambda: {"last_royalty_log_id": 500, "last_logistics_operation_id": 500}
rc.fetch_royalty_rows = lambda page: [_royalty_row(50, "906OLD", 22.50)]  # 50 << курсор 500
rc.fetch_logistic_rows = lambda page: []

normal_candidates, _, _ = rc.collect(page=None)
_chk("нормальний collect(): СТАРИЙ log_id (50 ≤ курсор 500) НЕ повертається (як і мало бути)",
     normal_candidates == [])

backfill_candidates, backfill_cursor, _ = rc.collect(page=None, ignore_cursor=True)
_chk("collect(ignore_cursor=True): той самий СТАРИЙ рядок ПОВЕРТАЄТЬСЯ (бекфіл бачить те, що вже не нове)",
     len(backfill_candidates) == 1 and backfill_candidates[0]["order_id"] == "906OLD")
_chk("collect(ignore_cursor=True): new_cursor і далі рахується коректно (для інформації, не для запису)",
     backfill_cursor["last_royalty_log_id"] == 50)
rc.fetch_royalty_rows = lambda page: []  # не протікає далі

# ── 10: RESERVE_RELEASE_TITLE — «зняття резерву за невиконане замовлення» (Аудитор,
# КОДВ_журнал.md, 2026-09-29, розділ 1: живий доказ №905260801 -23,52, №905484851 -103,90) —
# ОКРЕМИЙ канал reserve_releases, не змішується зі звичайними candidates (комісія) ──
rc._load_cursor = lambda: {"last_royalty_log_id": 100, "last_logistics_operation_id": 100}
rc.fetch_royalty_rows = lambda page: [
    _royalty_row(150, "905484851", -103.90, type_title=rc.RESERVE_RELEASE_TITLE),
    _royalty_row(151, "906300000", 40.0),  # звичайна комісія в тому самому прогоні — не плутається
]
rc.fetch_logistic_rows = lambda page: []
_BOOK.clear()
candidates10, _, reserve_releases10 = rc.collect(page=None)
_chk("reserve_release рядок НЕ потрапляє у звичайні candidates",
     len(candidates10) == 1 and candidates10[0]["order_id"] == "906300000")
_chk("reserve_release потрапляє в ОКРЕМИЙ список reserve_releases",
     len(reserve_releases10) == 1 and reserve_releases10[0]["order_id"] == "905484851")
_chk("reserve_release: сума береться за модулем (amount=103.90, не -103.90)",
     reserve_releases10[0]["amount"] == 103.90)

sync_rr = rc.sync_reserve_releases(reserve_releases10)
_chk("sync_reserve_releases: новий запис відкрито (ключ order_id:reserve_release)",
     sync_rr["newly_opened"] == ["rozetka_reserve_release:905484851:reserve_release"])
reg_rr = kr._load_registry(_tmp_reg)
_chk("реєстр: sum = amount (103.90)",
     reg_rr["rozetka_reserve_release:905484851:reserve_release"]["sum"] == 103.90)

resolved_rr_before = rc.resolve_reserve_releases()
_chk("resolve_reserve_releases: без сторно в книзі — не закрито",
     "rozetka_reserve_release:905484851:reserve_release" not in resolved_rr_before["resolved"])

_BOOK["905484851"] = {"row": 130, "current_i9": 103.90,
                       "e_text": "Rozetka №905484851, повернення покупцю, сторно 103,90 (RETURN)."}
resolved_rr_after = rc.resolve_reserve_releases()
_chk("resolve_reserve_releases: сторно 103,90 з'явилось у Графі 5 — закрито",
     "rozetka_reserve_release:905484851:reserve_release" in resolved_rr_after["resolved"])
_chk("реєстр: status=resolved",
     kr._load_registry(_tmp_reg)["rozetka_reserve_release:905484851:reserve_release"]["status"] == "resolved")

# Повторний sync тим самим reserve_release — вже НЕ "newly_opened" (був resolved, тепер знов open,
# той самий source-незалежний реєстр, що й rozetka_commission)
rc.fetch_royalty_rows = lambda page: []
rc.fetch_logistic_rows = lambda page: []

# ── 11: _reserve_release_alert_text — регрес-тест на баг незалежного аудиту PR #603:
# rr_sync["newly_opened"] містить ПОВНІ ключі "rozetka_reserve_release:{order_id}:reserve_release"
# (sync_open_candidates префіксує джерелом), а старий фільтр у run()/backfill() звіряв ГОЛИЙ
# "{order_id}:reserve_release" — membership-тест НІКОЛИ не збігався, алерт ішов із заголовком,
# але БЕЗ жодного номера замовлення (рівно та інформація, заради якої алерт існує) ──
_rr_fresh = [{"order_id": "905484851", "amount": 103.90, "date": "2026-09-23"}]
_rr_sync_result = {"newly_opened": ["rozetka_reserve_release:905484851:reserve_release"],
                    "still_open": [], "resolved": []}
alert_text = rc._reserve_release_alert_text(_rr_fresh, _rr_sync_result)
_chk("_reserve_release_alert_text: алерт НЕ порожній", alert_text != "")
_chk("_reserve_release_alert_text: номер замовлення дійсно присутній у тексті (сам баг ховав саме це)",
     "№905484851" in alert_text)
_chk("_reserve_release_alert_text: сума присутня", "103.9" in alert_text)

_chk("_reserve_release_alert_text: newly_opened порожній → \"\" (нема кого називати)",
     rc._reserve_release_alert_text(_rr_fresh, {"newly_opened": [], "still_open": [], "resolved": []}) == "")

_chk("_reserve_release_alert_text: newly_opened про ІНШЕ замовлення → \"\" (жоден рядок не зматчився)",
     rc._reserve_release_alert_text(
         _rr_fresh, {"newly_opened": ["rozetka_reserve_release:999999999:reserve_release"],
                     "still_open": [], "resolved": []}) == "")

_chk("_reserve_release_alert_text: prefix застосовується (--backfill варіант)",
     rc._reserve_release_alert_text(_rr_fresh, _rr_sync_result, prefix="🚨 X").startswith("🚨 X:"))


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ reserve_warning + реєстр незалежний від курсора (sync_registry/resolve_against_book) — "
      "усі перевірки коректні.")
sys.exit(0)
