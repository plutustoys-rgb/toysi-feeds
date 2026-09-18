#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_kandydaty_registry.py — регрес-тест реєстру відкритих кандидатів КОДВ
(kandydaty_registry.py, 2026-09-18, знахідка незалежного аудитора КОДВ_журнал.md
«ДОПОВНЕННЯ 5», Д1+Д4 — курсор джерела «раз показав і забув» губив кандидатів назавжди).

Файлова система — ТИМЧАСОВА тека (tempfile), не документи_КОДВ. Мережа не потрібна.
`python test_kandydaty_registry.py` → exit 0/1.
"""
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import kandydaty_registry as kr

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


_TMP = Path(tempfile.mkdtemp()) / "_vidkryti_kandydaty.json"
_TMP_MD = Path(tempfile.mkdtemp()) / "_vidkryti_kandydaty.md"


def _c(key, summary="test", sum_=100.0, date_="2026-09-10"):
    return {"key": key, "summary": summary, "sum": sum_, "date": date_}


# 1: перший прогін — усі нові кандидати відкриваються
r1 = kr.sync_open_candidates("checkbox", [_c("58"), _c("59"), _c("60")], path=_TMP)
_chk("1-й прогін: 3 нових відкрито", set(r1["newly_opened"]) == {"checkbox:58", "checkbox:59", "checkbox:60"})
_chk("1-й прогін: нічого не закрито", r1["resolved"] == [])
reg = kr._load_registry(_TMP)
_chk("реєстр: 3 записи", len(reg) == 3)
_chk("реєстр: checkbox:58 status=open", reg["checkbox:58"]["status"] == "open")
_chk("реєстр: checkbox:58 first_seen=сьогодні", reg["checkbox:58"]["first_seen"] == date.today().isoformat())

# 2: ДРУГИЙ прогін, ТІ САМІ кандидати досі не в книзі (курсор джерела пішов далі — Д1!) — має
#    ЛИШИТИСЬ "open" з тим самим first_seen (не оновлюється як "новий")
r2 = kr.sync_open_candidates("checkbox", [_c("58"), _c("59"), _c("60")], path=_TMP)
_chk("2-й прогін: 0 нових (вже бачили)", r2["newly_opened"] == [])
_chk("2-й прогін: 3 лишились open", set(r2["still_open"]) == {"checkbox:58", "checkbox:59", "checkbox:60"})
reg = kr._load_registry(_TMP)
_chk("реєстр: first_seen НЕ змінився при повторному прогоні", reg["checkbox:58"]["first_seen"] == date.today().isoformat())

# 3: ТРЕТІЙ прогін — 58 і 59 внесено (джерело їх більше НЕ пропонує), 60 усе ще висить, +новий 61
r3 = kr.sync_open_candidates("checkbox", [_c("60"), _c("61")], path=_TMP)
_chk("3-й прогін: 61 новий", r3["newly_opened"] == ["checkbox:61"])
_chk("3-й прогін: 60 лишився open", r3["still_open"] == ["checkbox:60"])
_chk("3-й прогін: 58 і 59 закрито (resolved)", set(r3["resolved"]) == {"checkbox:58", "checkbox:59"})
reg = kr._load_registry(_TMP)
_chk("реєстр: 58 status=resolved", reg["checkbox:58"]["status"] == "resolved")
_chk("реєстр: 58 має resolved_at", "resolved_at" in reg["checkbox:58"])
_chk("реєстр: resolved-запис НЕ видалено (аудиторський слід)", "checkbox:58" in reg)
_chk("реєстр: усього 4 записи (58,59,60,61)", len(reg) == 4)

# 4: ІНШЕ джерело не заважає — sync для "novapay" не чіпає checkbox-записи
r4 = kr.sync_open_candidates("novapay", [_c("A1", sum_=50.0)], path=_TMP)
reg = kr._load_registry(_TMP)
_chk("інше джерело: checkbox:60 усе ще open (не зачеплено)", reg["checkbox:60"]["status"] == "open")
_chk("інше джерело: novapay:A1 додано", "novapay:A1" in reg and reg["novapay:A1"]["status"] == "open")

# 5: кандидат, що був "resolved", але ЗНОВУ спливає непокритим (свідомо відхилений? передумали?
#    хибний запис у книзі виправили назад?) — відкривається ЗНОВУ, не ігнорується
r5 = kr.sync_open_candidates("checkbox", [_c("58"), _c("60")], path=_TMP)
_chk("resolved → знову unresolved: перевідкрито", "checkbox:58" in r5["newly_opened"] or "checkbox:58" in r5["still_open"])
reg = kr._load_registry(_TMP)
_chk("реєстр: 58 знову status=open", reg["checkbox:58"]["status"] == "open")

# 6: write_open_report() — старіші (більший вік) угорі, resolved НЕ потрапляють у звіт
kr._save_registry({
    "checkbox:old": {"source": "checkbox", "key": "old", "summary": "старий", "sum": 1.0,
                      "date": "x", "status": "open",
                      "first_seen": (date.today() - timedelta(days=10)).isoformat()},
    "checkbox:new": {"source": "checkbox", "key": "new", "summary": "новий", "sum": 2.0,
                      "date": "x", "status": "open", "first_seen": date.today().isoformat()},
    "checkbox:done": {"source": "checkbox", "key": "done", "summary": "закритий", "sum": 3.0,
                       "date": "x", "status": "resolved",
                       "first_seen": date.today().isoformat(), "resolved_at": date.today().isoformat()},
}, path=_TMP)
report_path = kr.write_open_report(path=_TMP, out_path=_TMP_MD)
content = report_path.read_text(encoding="utf-8")
_chk("звіт: старий кандидат ВИЩЕ нового", content.index("старий") < content.index("новий"))
_chk("звіт: resolved НЕ у звіті", "закритий" not in content)
_chk("звіт: 10 днів показано", "| 10 |" in content)

# 7: resolve=False (аудит 2026-09-18, рецидив Д1/Д4: обрізана сторінка API) — не закриває
#    "open", навіть якщо current не містить ключ; still_open лишається пустим (не заявлений
#    у current цього разу), але статус реєстру не змінюється на "resolved"
_TMP2 = Path(tempfile.mkdtemp()) / "_vidkryti_kandydaty.json"
kr.sync_open_candidates("src", [_c("1")], path=_TMP2)
r7 = kr.sync_open_candidates("src", [], path=_TMP2, resolve=False)
_chk("resolve=False: resolved порожній, хоч current порожній", r7["resolved"] == [])
reg7 = kr._load_registry(_TMP2)
_chk("resolve=False: запис лишився status=open", reg7["src:1"]["status"] == "open")
r7b = kr.sync_open_candidates("src", [], path=_TMP2, resolve=True)
_chk("resolve=True (за замовчуванням) наступного разу: закрито", r7b["resolved"] == ["src:1"])

# 8: порожній реєстр → звіт не падає
empty_path = Path(tempfile.mktemp())
kr._save_registry({}, path=empty_path)
empty_report = kr.write_open_report(path=empty_path, out_path=Path(tempfile.mktemp()))
_chk("порожній реєстр: звіт сформовано без винятку", empty_report.exists())


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Реєстр відкритих кандидатів: sync/still-open/resolved/re-open/звіт — усе коректно")
sys.exit(0)
