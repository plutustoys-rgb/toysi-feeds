#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_akty_zvirka_ochikuvannya.py — перевірка очікувань головного бухгалтера в akty_zvirka (КОДВ_CHANNEL.md 2026-10-08).
Реальні акти не містять номерів замовлень, тому доказ трьох ступенів: номер у тексті акта → «акт = Σ кандидатів потоку, замовлення в кандидатах» →
інакше ❗ «не підтверджено» (ручне ✅ бухгалтера не перетирається). Закриття пишуться лише в поле `закрито_актом`, файл атомарно перечитується.
Мережа/PDF/книга не потрібні. `python test_akty_zvirka_ochikuvannya.py` → exit 0/1."""
import json
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import akty_zvirka as az

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def act(vendor="rozetka", doc="TA1", period="2026-09", text=""):
    return {"vendor": vendor, "doc_id": doc, "period": period, "text": text}


def row(stream, period, status, act_total=100.0, cand_sum=100.0, complete=False):
    return {"stream": stream, "period": period, "status": status, "act_total": act_total, "cand_sum": cand_sum, "complete": complete}


def exp(**kw):
    base = {"книга_рядок": 51, "контрагент": "ТОВ «Термінал Розетка»", "акт_за": "2026-09", "замовлення": "904295184", "склад": "комісія за продаж", "сума": 30.52}
    base.update(kw)
    return base


# ── ступінь 1: номер у тексті акта
r = az.check_expectation(exp(), [act(text="... замовлення 904295184 комісія ...")], [], {})
chk("номер замовлення в тексті акта → closed, доказ названо", r["verdict"] == "closed" and "тексті акта" in r["evidence"])
r = az.check_expectation(exp(замовлення="906402279, 906412447, 905840122"), [act(text="906402279 906412447")], [], {})
chk("з трьох номерів у тексті лише два → НЕ closed", r["verdict"] == "unproven")

# ── ступінь 2: композиція
cands = {"rozetka_logistics": {"2026-09": {"orders": [("906402279:logistics", 10.2), ("906412447:logistics", 10.2), ("905840122:logistics", 10.2)]}}}
e = exp(замовлення="906402279, 906412447, 905840122", склад="логістика неуспішних замовлень, по 10,20", сума=30.6)
r = az.check_expectation(e, [act()], [row("rozetka_logistics", "2026-09", "✅ збіг")], cands)
chk("потік ✅ збіг + усі 3 замовлення в кандидатах, сума 30.6 → closed (логістика мапиться окремо від комісії)", r["verdict"] == "closed" and "кандидат" in r["evidence"])
r = az.check_expectation(e, [act()], [row("rozetka_logistics", "2026-09", "⚠️ різниця", 285.6, 153.0)], cands)
chk("потік ⚠️ різниця → НЕ closed, причина називає потік і суми", r["verdict"] == "unproven" and "різниця" in r["detail"] and "285.60" in r["detail"])
cands2 = {"rozetka_logistics": {"2026-09": {"orders": [("906402279:logistics", 10.2), ("906412447:logistics", 10.2)]}}}
r = az.check_expectation(e, [act()], [row("rozetka_logistics", "2026-09", "✅ збіг")], cands2)
chk("у кандидатах лише 2 з 3 → сума 20.4 ≠ 30.6 → НЕ closed", r["verdict"] == "unproven" and "20.4" in r["detail"])
r = az.check_expectation(exp(), [act()], [row("rozetka_royalty", "2026-09", "✅ збіг")], {"rozetka_royalty": {"2026-09": {"orders": [("904295184", 99.0)]}}})
chk("замовлення в кандидатах, але з іншою сумою → НЕ closed", r["verdict"] == "unproven")
r = az.check_expectation(exp(), [act()], [row("rozetka_royalty", "2026-09", "✅ збіг")], {"rozetka_royalty": {"2026-09": {"orders": []}}})
chk("замовлення нема в кандидатах → НЕ closed, це сказано", r["verdict"] == "unproven" and "нема серед кандидатів" in r["detail"])
r = az.check_expectation(exp(), [act()], [row("rozetka_royalty", "2026-09", "✅ збіг")], {"rozetka_royalty": {"2026-09": {"orders": [("904295184", 30.52)]}}})
chk("комісія за продаж мапиться на rozetka_royalty й закривається", r["verdict"] == "closed")
r = az.check_expectation(exp(замовлення="904621035 / RMP-283162840", сума=10.2, склад="логістика (організація видачі)"),
                         [act()], [row("rozetka_logistics", "2026-09", "✅ збіг")], {"rozetka_logistics": {"2026-09": {"orders": [("904621035:logistics", 10.2)]}}})
chk("два номери одного платежу (замовлення / RMP) — достатньо знайденого й суми", r["verdict"] == "closed")

# ── ручне ✅, відкладені, незрозумілі
r = az.check_expectation(exp(статус="✅ є в акті TA00558523 (журнал (170))"), [act()], [row("rozetka_royalty", "2026-09", "⚠️ різниця")], {})
chk("ручне ✅ бухгалтера без авто-доказу → manual (не ❗)", r["verdict"] == "manual")
r = az.check_expectation(exp(), [], [], {})
chk("акта за місяць нема → pending (не ❗)", r["verdict"] == "pending" and "ще немає" in r["detail"])
r = az.check_expectation(exp(контрагент="Невідомий ТОВ"), [act()], [], {})
chk("контрагента не розпізнано → unproven з поясненням, без винятку", r["verdict"] == "unproven" and "не вдалось визначити" in r["detail"])
r = az.check_expectation(exp(контрагент="ТОВ «РУШ» (EVA)", замовлення="8-080322205", склад="роялті ТМ 8,22 + платформа 5,48 (12%)", сума=13.7),
                         [act("eva", "RU1", "2026-09")], [row("eva", "2026-09", "✅ збіг")], {"eva": {"2026-09": {"orders": [("8-080322205", 13.7)]}}})
chk("EVA: токен 8-080322205 розпізнається, потік eva", r["verdict"] == "closed")

# ── файл: evaluate + apply_closures
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "_ochikuvani_v_aktakh.json"
    data = {"створено": "x", "що_це": "y", "очікування": [
        exp(), exp(книга_рядок=58, замовлення="904575341", сума=31.75, статус="✅ вручну"), exp(книга_рядок=60, замовлення="999999999")]}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    cs = {"rozetka_royalty": {"2026-09": {"orders": [("904295184", 30.52)]}}}
    res, path = az.evaluate_expectations([act()], [row("rozetka_royalty", "2026-09", "✅ збіг")], cs, path=p)
    chk("evaluate: 3 результати, перший closed, другий manual, третій unproven",
        [r["verdict"] for r in res] == ["closed", "manual", "unproven"])
    n = az.apply_closures(res, path, "2026-10-12")
    after = json.loads(p.read_text(encoding="utf-8"))
    chk("apply: записано рівно 1 закриття", n == 1)
    chk("apply: поле закрито_актом з доказом, актами й датою", after["очікування"][0]["закрито_актом"] == {"доказ": res[0]["evidence"], "акти": ["TA1"], "перевірено": "2026-10-12"})
    chk("apply: решта файлу без змін (інші очікування й верхні ключі)",
        after["очікування"][1] == data["очікування"][1] and after["очікування"][2] == data["очікування"][2] and after["створено"] == "x")
    res2, _ = az.evaluate_expectations([act()], [row("rozetka_royalty", "2026-09", "✅ збіг")], cs, path=p)
    chk("повторний прогін: вже_закрито, нових закриттів 0 (ідемпотентно)", res2[0]["вже_закрито"] and az.apply_closures(res2, p, "2026-10-13") == 0)
    data2 = json.loads(p.read_text(encoding="utf-8"))
    del data2["очікування"][0]["закрито_актом"]
    data2["очікування"][0]["сума"] = 31.0
    p.write_text(json.dumps(data2, ensure_ascii=False), encoding="utf-8")
    chk("файл змінено після evaluate (інша сума) → не закриваємо чужий запис", az.apply_closures(res, p, "2026-10-14") == 0)
    chk("відсутній файл очікувань → ([], None), не помилка", az.evaluate_expectations([], [], {}, path=Path(d) / "нема.json") == ([], None))
    (Path(d) / "битий.json").write_text("{не json", encoding="utf-8")
    chk("битий файл → ([], None)", az.evaluate_expectations([], [], {}, path=Path(d) / "битий.json") == ([], None))

# ── аудит #687: межі числа, інший місяць акта, нечислова сума, нестандартна структура, збій запису, ізоляція run()
r = az.check_expectation(exp(), [act(text="IBAN UA2330528400000260090123904295184777 ЄДРПОУ 1")], [], {})
chk("номер усередині довшого числа (IBAN) НЕ вважається згадкою замовлення", r["verdict"] == "unproven")
r = az.check_expectation(exp(), [act(text="замовлення №904295184, комісія")], [], {})
chk("номер з розділювачем (№…,) — згадка є", r["verdict"] == "closed")
r = az.check_expectation(exp(), [act(period="2026-08", text="904295184")], [], {})
chk("акт ІНШОГО місяця не береться до уваги (pending, а не closed)", r["verdict"] == "pending")
r = az.check_expectation(exp(), [act(vendor="prom", text="904295184")], [], {})
chk("акт ІНШОГО контрагента не береться до уваги", r["verdict"] == "pending")
r = az.check_expectation(exp(сума="30,52"), [act()], [row("rozetka_royalty", "2026-09", "✅ збіг")], {"rozetka_royalty": {"2026-09": {"orders": [("904295184", 30.52)]}}})
chk("сума рядком з комою читається", r["verdict"] == "closed")
r = az.check_expectation(exp(сума="багато"), [act()], [], {})
chk("сума не число → unproven з поясненням, без винятку", r["verdict"] == "unproven" and "не число" in r["detail"])

with tempfile.TemporaryDirectory() as d:
    bad = Path(d) / "list.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    chk("верхній рівень файлу — список → ([], без винятку)", az.evaluate_expectations([act()], [], {}, path=bad)[0] == [])
    mixed = Path(d) / "mixed.json"
    mixed.write_text(json.dumps({"очікування": ["рядок", exp()]}, ensure_ascii=False), encoding="utf-8")
    chk("елемент-рядок серед очікувань пропускається, решта обробляється", len(az.evaluate_expectations([act()], [], {}, path=mixed)[0]) == 1)
    # збій запису (OSError) не валить: закриття не записано, tmp прибрано
    p2 = Path(d) / "_ochikuvani_v_aktakh.json"
    p2.write_text(json.dumps({"очікування": [exp()]}, ensure_ascii=False), encoding="utf-8")
    res3, _ = az.evaluate_expectations([act()], [row("rozetka_royalty", "2026-09", "✅ збіг")], {"rozetka_royalty": {"2026-09": {"orders": [("904295184", 30.52)]}}}, path=p2)
    real_replace = az.os.replace

    def boom(*a, **k):
        raise PermissionError("Access is denied")
    az.os.replace = boom
    try:
        n_fail = az.apply_closures(res3, p2, "2026-10-12")
    finally:
        az.os.replace = real_replace
    chk("PermissionError при записі → 0 закриттів, без винятку, файл не змінено, tmp не лишився",
        n_fail == 0 and "закрито_актом" not in json.loads(p2.read_text(encoding="utf-8"))["очікування"][0] and not Path(str(p2) + ".tmp").exists())

# run(): збій блоку очікувань не вбиває основний звіт
with tempfile.TemporaryDirectory() as d:
    saved = (az.collect_acts, az.candidates_novapay, az.candidates_rozetkapay, az.candidates_prom, az.candidates_from_registry,
             az.kandydaty_registry._load_registry, az.book_rows_for, az.vch.DOCS_DIR, az.evaluate_expectations)
    try:
        az.collect_acts = lambda: []
        az.candidates_novapay = az.candidates_rozetkapay = az.candidates_prom = lambda: {}
        az.candidates_from_registry = lambda reg: {}
        az.kandydaty_registry._load_registry = lambda: {}
        az.book_rows_for = lambda ids: {}
        az.vch.DOCS_DIR = Path(d)

        def explode(*a, **k):
            raise ValueError("зламаний файл очікувань")
        az.evaluate_expectations = explode
        az.run()
        chk("run(): evaluate кинув виняток → основний звіт _zvirka_aktiv.md усе одно записано", (Path(d) / az.REPORT_NAME).exists())
        az.evaluate_expectations = saved[8]
        az.run(only_month="2026-09")
        chk("run(--month): без файлу очікувань теж працює", (Path(d) / az.REPORT_NAME).exists())
    finally:
        (az.collect_acts, az.candidates_novapay, az.candidates_rozetkapay, az.candidates_prom, az.candidates_from_registry,
         az.kandydaty_registry._load_registry, az.book_rows_for, az.vch.DOCS_DIR, az.evaluate_expectations) = saved

# ── звіт
txt = az.render_expectations(res)
chk("звіт: розділ, ❗ у третьому, ✅ у першому, ℹ️ у другому", "## Очікування бухгалтера" in txt and "❗ очікувана сума не підтверджена" in txt and "✅ закрито актом" in txt and "ℹ️ закрито вручну" in txt)
chk("звіт: порожні результати → порожній рядок", az.render_expectations([]) == "")

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
