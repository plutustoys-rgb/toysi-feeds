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
_chk("звіт: шапка динамічна за реєстром ('checkbox'), не хардкод старого джерела",
     "checkbox" in content and "поки що: Checkbox" not in content)

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
_chk("порожній реєстр: шапка каже 'ще жодного', не падає на порожній множині джерел",
     "ще жодного" in empty_report.read_text(encoding="utf-8"))


# 9: check_stale_candidates / acknowledge / send_stale_alert (аудит 2026-09-28, п.3 — "немає
# сигналу книга стоїть": source_freshness дивиться лише свіжість ДЖЕРЕЛА, не вік невнесеного
# кандидата — чеки 1,3,5,8 не мають бути шумом після acknowledge()).
_TMP9 = Path(tempfile.mktemp())
kr._save_registry({
    "checkbox:1": {"source": "checkbox", "key": "1", "summary": "чек 1", "sum": 10.0,
                   "date": "x", "status": "open",
                   "first_seen": (date.today() - timedelta(days=5)).isoformat()},
    "checkbox:2": {"source": "checkbox", "key": "2", "summary": "щойно", "sum": 20.0,
                   "date": "x", "status": "open", "first_seen": date.today().isoformat()},
    "checkbox:3": {"source": "checkbox", "key": "3", "summary": "закритий давно", "sum": 30.0,
                   "date": "x", "status": "resolved",
                   "first_seen": (date.today() - timedelta(days=20)).isoformat(),
                   "resolved_at": date.today().isoformat()},
}, path=_TMP9)
_ACK9 = Path(tempfile.mktemp())

stale = kr.check_stale_candidates(max_age_days=2, path=_TMP9, ack_path=_ACK9)
_chk("stale: лише checkbox:1 (5 днів, поріг 2)", [k for _, k, _ in stale] == ["checkbox:1"])
_chk("stale: checkbox:2 (сьогодні, поріг 2) НЕ потрапив", "checkbox:2" not in [k for _, k, _ in stale])
_chk("stale: resolved НЕ потрапляє незалежно від віку", "checkbox:3" not in [k for _, k, _ in stale])

kr.acknowledge("checkbox:1", "власник уже пояснив затримку 25.09", path=_ACK9)
stale_after_ack = kr.check_stale_candidates(max_age_days=2, path=_TMP9, ack_path=_ACK9)
_chk("acknowledge(): визнаний виняток зникає зі stale-переліку", stale_after_ack == [])

report9 = kr.write_open_report(path=_TMP9, out_path=Path(tempfile.mktemp()), ack_path=_ACK9)
content9 = report9.read_text(encoding="utf-8")
_chk("звіт: визнаний виняток ВСЕ ОДНО показаний (прозорість, не приховування)", "чек 1" in content9)
_chk("звіт: причина визнання видима в звіті", "власник уже пояснив" in content9)

# send_stale_alert() — мокаємо саму мережеву відправку (send_throttled_alert), перевіряємо
# лише що модуль ВИКЛИКАЄ її з правильним throttle-ключем/переліком, не сам HTTP.
_sent_calls = []
kr.send_throttled_alert = lambda dedup_key, text, cooldown_sec=0: (
    _sent_calls.append((dedup_key, text, cooldown_sec)) or True
)
sent = kr.send_stale_alert(max_age_days=2, path=_TMP9, ack_path=_ACK9)
_chk("send_stale_alert: після acknowledge() нема кого сигналити → не шле", sent is False and _sent_calls == [])

_ACK9_EMPTY = Path(tempfile.mktemp())
sent2 = kr.send_stale_alert(max_age_days=2, path=_TMP9, ack_path=_ACK9_EMPTY)
_chk("send_stale_alert: без acknowledge — шле throttled-алерт", sent2 is True and len(_sent_calls) == 1)
_chk("send_stale_alert: dedup_key стабільний", _sent_calls[0][0] == "kodv_stale_candidates")
_chk("send_stale_alert: cooldown = 24 год", _sent_calls[0][2] == 24 * 3600)
_chk("send_stale_alert: перелік містить ключ стривоженого", "checkbox:1" in _sent_calls[0][1])

# _NO_TELEGRAM-гейт (аудит 2026-09-29): ручний діагностичний запуск НЕ має слати реальний алерт —
# той самий клас бага, що вже фіксили в test_site_cod_guardrails.py, тепер запобігли заздалегідь.
_ACK9_EMPTY2 = Path(tempfile.mktemp())
kr._NO_TELEGRAM = True
try:
    _sent_calls.clear()
    sent3 = kr.send_stale_alert(max_age_days=2, path=_TMP9, ack_path=_ACK9_EMPTY2)
    _chk("_NO_TELEGRAM=True: send_stale_alert НЕ шле, навіть якщо є що сигналити",
         sent3 is False and _sent_calls == [])
finally:
    kr._NO_TELEGRAM = False


# 10: amount_applied_in_text / resolve_open_candidates_by_text (спільна реалізація —
# rozetka_commission_ledger.py й eva_commission_ledger.py делегують сюди, аудит 2026-09-28, п.1)
_chk("amount_applied_in_text: кома у тексті, крапка в amount", kr.amount_applied_in_text("сума 47,18 внесена", 47.18))
_chk("amount_applied_in_text: крапка в обох", kr.amount_applied_in_text("сума 47.18 внесена", 47.18))
_chk("amount_applied_in_text: сума відсутня", not kr.amount_applied_in_text("щось інше", 47.18))
_chk("amount_applied_in_text: amount=None", not kr.amount_applied_in_text("47,18", None))
_chk("amount_applied_in_text: text порожній", not kr.amount_applied_in_text("", 47.18))

# МЕЖОВА ПЕРЕВІРКА (аудит PR перед мержем 2026-09-28): "10,20" — сума, що за регресійними
# даними Аудитора повторюється мінімум 11 разів у книзі — НЕ має хибно збігатись як підрядок
# більшого числа ("110,20", "10,205"). Без межі це тихо приховало б НЕ внесений факт.
_chk("amount_applied_in_text: '10,20' НЕ збігається всередині '110,20' (більше число попереду)",
     not kr.amount_applied_in_text("рядок 110,20 щось інше", 10.20))
_chk("amount_applied_in_text: '10,20' НЕ збігається як префікс '10,205'",
     not kr.amount_applied_in_text("сума 10,205 внесена", 10.20))
_chk("amount_applied_in_text: '10,20' ЗБІГАЄТЬСЯ, коли стоїть окремо (реальний позитивний кейс)",
     kr.amount_applied_in_text("логістика 10,20 внесено 28.09", 10.20))
_chk("amount_applied_in_text: збіг у кінці рядка (немає символу після) — теж валідний",
     kr.amount_applied_in_text("сума 10,20", 10.20))
_chk("amount_applied_in_text: крапка-варіант теж має межову перевірку ('110.20' не збігається з 10.20)",
     not kr.amount_applied_in_text("110.20 інше", 10.20))

# ЧИСЛОВЕ порівняння, НЕ текстове (аудит 2026-09-29, живий приклад рядка 118 книги:
# «стало 10.2 (+10.2)» — книга пише ОДНУ цифру після коми, а не завжди дві).
_chk("amount_applied_in_text: '10.2' (одна цифра) ЗБІГАЄТЬСЯ з amount=10.20 (числове порівняння)",
     kr.amount_applied_in_text("стало 10.2 (+10.2)", 10.20))
_chk("amount_applied_in_text: '10,2' (кома, одна цифра) теж збігається",
     kr.amount_applied_in_text("логістика: 10,2 внесено", 10.20))
_chk("amount_applied_in_text: ціле число без копійок ('10') НЕ збігається з amount=10.20",
     not kr.amount_applied_in_text("сума 10 грн", 10.20))

# НЕГАТИВНИЙ КОНТЕКСТ (аудит 2026-09-29, живий приклад: примітка «бракує 10,20» містить те
# саме число кандидата, але означає ПРОТИЛЕЖНЕ — досі НЕ внесено).
_chk("amount_applied_in_text: 'бракує 10,20' НЕ рахується як внесено (негативний контекст)",
     not kr.amount_applied_in_text("Рядок 71: бракує 10,20 у графі 9.", 10.20))
_chk("amount_applied_in_text: 'не вистачає 47,18' НЕ рахується",
     not kr.amount_applied_in_text("не вистачає 47,18 роялті", 47.18))
_chk("amount_applied_in_text: 'внесено 10,20' (позитивний контекст, той самий число) РАХУЄТЬСЯ",
     kr.amount_applied_in_text("логістика внесено 10,20 сьогодні", 10.20))
_far_text = "бракує" + " x" * 20 + " 10,20 внесено тут"  # "бракує" явно за межами 25-символьного вікна
_chk("amount_applied_in_text: негативний контекст ЗА МЕЖАМИ вікна (>25 символів) не заважає",
     kr.amount_applied_in_text(_far_text, 10.20))

_TMP10 = Path(tempfile.mktemp())
kr._save_registry({
    "eva_commission:8-1": {"source": "eva_commission", "key": "8-1", "summary": "комісія 30.99",
                            "sum": 30.99, "date": "x", "status": "open",
                            "first_seen": date.today().isoformat()},
    "eva_commission:8-2": {"source": "eva_commission", "key": "8-2", "summary": "комісія 12.40",
                            "sum": 12.40, "date": "x", "status": "open",
                            "first_seen": date.today().isoformat()},
    "rozetka_commission:900": {"source": "rozetka_commission", "key": "900", "summary": "інше джерело",
                                "sum": 30.99, "date": "x", "status": "open",
                                "first_seen": date.today().isoformat()},
}, path=_TMP10)
_book_texts = {"8-1": "EVA №8-1, комісія 30,99 внесено.", "8-2": "EVA №8-2, ще не внесено."}
result10 = kr.resolve_open_candidates_by_text("eva_commission", lambda k: _book_texts.get(k, ""), path=_TMP10)
_chk("resolve_open_candidates_by_text: закрито лише 8-1 (сума в тексті)", result10["resolved"] == ["eva_commission:8-1"])
reg10 = kr._load_registry(_TMP10)
_chk("реєстр: eva_commission:8-1 status=resolved", reg10["eva_commission:8-1"]["status"] == "resolved")
_chk("реєстр: eva_commission:8-2 усе ще open (сума не знайдена)", reg10["eva_commission:8-2"]["status"] == "open")
_chk("реєстр: rozetka_commission:900 НЕ зачеплено (інший source, та сама сума 30.99)",
     reg10["rozetka_commission:900"]["status"] == "open")


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Реєстр відкритих кандидатів: sync/still-open/resolved/re-open/звіт — усе коректно")
sys.exit(0)
