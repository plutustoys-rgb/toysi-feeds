"""
akty_zvirka.py — звіт-звірка «місячні акти контрагентів (Вчасно) ↔ сума кандидатів комісій».

НАВІЩО (запит головного бухгалтера, КОДВ_CHANNEL.md 2026-10-06; «одне правило» — довідник §3, журнал (142)):
комісії NovaPay, RozetkaPay, Rozetka (роялті/логістика), EVA і Prom вносяться в книгу ОДНИМ рядком на місячний
акт, а не в рядки продажів. Реєстри/кандидати лишаються ІНСТРУМЕНТОМ ЗВІРКИ акта із замовленнями. Раніше цю
звірку 05.10 робив вручну агент; тут вона автоматична: для кожного контрагента й місяця —
«акт є/нема», сума акта (із PDF), сума кандидатів (замовлення/реєстри), різниця, «акт уже в книзі рядком №…».

ЩО ВОНО РОБИТЬ (READ-ONLY щодо книги й реєстрів; пише `документи_КОДВ/_zvirka_aktiv.md/.json`, `_zvirka_ochikuvannya.json` і єдине поле `закрито_актом` у `_ochikuvani_v_aktakh.json`):
 1. Бере акти `документи_КОДВ/*/*/*_akt_*.pdf` (контрагенти: Prom, НоваПей, Розетка Пей, Термінал Розетка, РУШ/EVA, АЛЛО).
 2. З тексту PDF (pdfplumber) дістає ПОРІВНЮВАНУ суму потоку за правилами, звіреними з довідником/журналом (див. parse_*):
    NovaPay — «Сума винагороди» (без ПДВ); RozetkaPay — «Разом» винагороди (без ПДВ);
    EVA — роялті (акт «використання ТМ») + доступ до платформи (акт «надання послуги доступу»);
    Rozetka — «Комісія за продаж» = роялті-акт + «доступ у зв'язку з обсягом продажу»×1,2, і логістика «Організація
    видачі»×1,2 (журнал: 322,13 = 178,97 + 143,16; 61,20 = 51,00×1,2); Prom — усі позиції акта КРІМ пакета
    «програмної продукції» ×1,2 (ProSale + компенсація доставки; журнал: 332,06 = 276,72×1,2; 35,00 = 29,17×1,2).
 3. Суму кандидатів бере з реєстрів: NovaPay і RozetkaPay — з УСІХ архівних реєстрів (ПОВНО, дедуп за ТТН/фін-номером);
    Rozetka/EVA/Prom — з `_vidkryti_kandydaty.json` / щоденних `*_prom_komisiya_kandydaty.json` (⚠️ НЕПОВНО: лише те,
    що бачив кабінет/API за вікно; дата EVA = перше побачення; звіт це явно позначає).
 4. Знаходить, у якому рядку книги акт уже згаданий (номер у перших 100 символах графи 5/12).
 5. Очікування бухгалтера (`_ochikuvani_v_aktakh.json`, запит 2026-10-08): для кожної суми, прибраної з продажів, перевіряє, що вона є в акті
    відповідного контрагента й місяця (докази й обмеження — у блоці «очікування бухгалтера» нижче); у звіт іде розділ «Очікування бухгалтера».

ЧОГО ВОНО НЕ РОБИТЬ: не знає складу замовлень в акті (акти — агрегати за категоріями/способами оплати, переліку
замовлень не містять), тому «замовлень, яких нема в акті» напряму не обчислює; при розбіжності друкує перелік замовлень
кандидатів потоку, щоб знайти зайве/пропущене (як EVA 8-080322205). Не вгадує: якщо суму акта не вдалось прочитати —
пише «не прочитано» (можна задати вручну в `документи_КОДВ/_akty_sumy.json`).

ЗАПУСК: python akty_zvirka.py [--month 2026-08]
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import kandydaty_registry
import vchasno_akty_kandydaty as vch

VAT = 1.2
TOLERANCE = 0.05          # ₴: до цієї різниці вважаємо збігом (округлення копійок)
OVERRIDES_NAME = "_akty_sumy.json"
REPORT_NAME = "_zvirka_aktiv.md"
JSON_NAME = "_zvirka_aktiv.json"

COUNTERPARTY_UA = {
    "prom": "Prom (УАПРОМ)", "novapay": "НоваПей", "rozetkapay": "Розетка Пей",
    "rozetka": "Термінал Розетка", "eva": "РУШ (EVA)", "allo": "АЛЛО",
}
STREAM_UA = {
    "novapay": "винагорода НП", "rozetkapay": "еквайринг RozetkaPay", "eva": "роялті ТМ + доступ до платформи",
    "rozetka_royalty": "комісія за продаж (роялті + обсяг)", "rozetka_logistics": "логістика (організація видачі)",
    "prom": "комісії Prom (ProSale, доставка; без пакета)", "allo": "комісія (звірки немає)",
}
# Потоки з ПОВНИМИ даними кандидатів (усі архівні реєстри). Решта — неповні.
COMPLETE_STREAMS = {"novapay", "rozetkapay"}


def _num(s) -> float:
    """'1 175,00' / '1 175,00' / '5,89' → float."""
    return float(re.sub(r"[\s ]", "", str(s)).replace(",", "."))


_AMT = r"(\d[\d  ]*,\d{2})"   # без \s: сума не має склеювати сусідні рядки


# ── парсери актів (чисті функції над текстом PDF) ─────────────────────────────────────────────────────────
def _bad(note: str, **kw) -> dict:
    return {"total": None, "note": note, **kw}


def parse_novapay(text: str) -> dict:
    m = re.search(r"Сума винагороди Платіжної установи за надані\s+послуги\s+" + _AMT + r"\s*грн", text)
    if not m:
        return _bad("не знайдено «Сума винагороди … за надані послуги»", stream="novapay", basis="без ПДВ")
    return {"stream": "novapay", "total": _num(m.group(1)), "basis": "без ПДВ"}


def parse_rozetkapay(text: str) -> dict:
    """«Разом» винагороди таблиці способів оплати. КОНТРОЛЬ: сума рядків таблиці (обох колонок) має дорівнювати «Разом» —
    інакше (3 колонки, зсув, зайвий рядок) повертаємо None, а не хибне число (аудит #625 M-3)."""
    m = re.search(r"Разом\s+" + _AMT + r"\s+" + _AMT, text)
    if not m:
        return _bad("не знайдено рядок «Разом»", stream="rozetkapay", basis="без ПДВ")
    rows = re.findall(r"^\d+\s+[^\n]*?\s" + _AMT + r"\s+" + _AMT + r"\s*$", text, re.M)
    if not rows:
        return _bad("таблицю способів оплати не розпізнано", stream="rozetkapay", basis="без ПДВ")
    s1 = round(sum(_num(a) for a, _ in rows), 2)
    s2 = round(sum(_num(b) for _, b in rows), 2)
    if abs(s1 - _num(m.group(1))) > 0.02 or abs(s2 - _num(m.group(2))) > 0.02:
        return _bad(f"контрольна сума таблиці ({s1} / {s2}) ≠ «Разом» ({m.group(1)} / {m.group(2)})", stream="rozetkapay", basis="без ПДВ")
    return {"stream": "rozetkapay", "total": _num(m.group(2)), "basis": "без ПДВ"}


def parse_eva(text: str) -> dict:
    """Два різні акти EVA: «використання ТОРГОВЕЛЬНИХ МАРОК» (роялті, не об'єкт ПДВ) і «надання ПОСЛУГИ ДОСТУПУ»
    (сума вже з ПДВ). Повертає частину потоку `eva`: part=royalty|access. КОНТРОЛЬ: підсумок у тексті = рядок таблиці «Всього»."""
    if "ТОРГОВЕЛЬНИХ МАРОК" in text.upper() and "роялті" in text:
        part, basis = "royalty", "без ПДВ (не об'єкт ПДВ)"
        m = re.search(r"Загальна сума роялті[^\n]*?склала\s+" + _AMT + r"\s*грн", text, re.S)
    else:
        part, basis = "access", "з ПДВ"
        m = re.search(r"Загальна сума за послуги складає\s+" + _AMT + r"\s*грн", text)
    base = {"stream": "eva", "part": part, "basis": basis}
    if not m:
        return {**_bad("не знайдено загальну суму акта", **base)}
    tot = _num(m.group(1))
    t = re.search(r"Всього\s+[\d,\s]+?-\s*" + _AMT, text)
    if not t or abs(_num(t.group(1)) - tot) > 0.02:
        return _bad("контрольна сума: «Всього» таблиці ≠ загальна сума в тексті", **base)
    return {**base, "total": tot}


def parse_rozetka(text: str) -> dict:
    """Два акти Термінал Розетка. Роялті-акт: «Роялті за користування торгової марки» (сума без ПДВ).
    Акт «доступ»: логістика «Організація видачі», щомісячна плата, «доступ ... в зв'язку з обсягом продажу» — усе
    без ПДВ; у книзі комісії з ПДВ → ×1,2 (журнал 322,13 = 178,97 + 143,16; 61,20). КОНТРОЛЬ: сума розпізнаних позицій = «Всього»
    акта; інакше всі позиції None (не показуємо часткове)."""
    if "Роялті за користування" in text:
        m = re.search(r"Роялті за користування[^\n]*?" + _AMT + r"\s+" + _AMT + r"\s*\n", text)
        t = re.search(r"Всього без ПДВ:\s*" + _AMT, text)
        if not m or not t or abs(_num(m.group(2)) - _num(t.group(1))) > 0.02:
            return {"doc": "royalty", "royalty_novat": None, "note": "роялті-акт: суму не підтверджено рядком «Всього без ПДВ»"}
        return {"doc": "royalty", "royalty_novat": _num(m.group(2))}
    out = {"doc": "access"}
    m = re.search(r"Організація видачі відправлень[^\n]*?шт\s+[\d,\s]+?\s" + _AMT + r"\s*\n", text)
    out["logistics_novat"] = _num(m.group(1)) if m else None
    m = re.search(r"Безумовна щомісячна плата[^\n]*\n\s*\d+\s+\d+\s+послуга\s+[\d,\s]+?\s" + _AMT + r"\s*\n", text)
    out["monthly_novat"] = _num(m.group(1)) if m else None
    m = re.search(r"в\s+зв.язку з обсягом продажу", text)
    m2 = re.search(r"\n\s*\d+\s+\d+\s+послуга\s+[\d,\s]+?\s" + _AMT + r"\s*\n[^\n]*обсягом продажу", text)
    out["volume_novat"] = _num(m2.group(1)) if (m and m2) else None
    t = re.search(r"\nВсього:\s*" + _AMT, text)
    items = [out["logistics_novat"], out["monthly_novat"], out["volume_novat"]]
    if not t or any(v is None for v in items) or abs(sum(items) - _num(t.group(1))) > 0.02:
        return {"doc": "access", "logistics_novat": None, "monthly_novat": None, "volume_novat": None,
                "note": "акт «доступ»: розпізнані позиції не дають «Всього» (є нерозпізнана/зайва позиція)"}
    return out


def parse_prom(text: str) -> dict:
    """Позиції верхнього рівня «N <назва> 1 грн ціна сума» (без крапки в номері). Пакет «програмної продукції» —
    передоплата послуги (виняток §3), НЕ комісія замовлень → виключається. Решта (ProSale, компенсація доставки,
    «Оплатити частинами» тощо) ×1,2 = порівнюване з кандидатами (cpa Prom — з ПДВ). КОНТРОЛЬ: сума ВСІХ розпізнаних
    позицій верхнього рівня (разом із пакетом) = «Всього:» акта (без ПДВ); інакше None."""
    items = []
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        m = re.match(r"^(\d+)\s+(.*?)\s+1\s+грн\s+" + _AMT + r"\s+" + _AMT + r"\s*$", ln.strip())
        if not m:
            continue
        # мітка = назва + рядки-ПРОДОВЖЕННЯ (до наступної нумерованої позиції) — НЕ будь-який наступний рядок: інакше текст сусіднього
        # пакета потрапляв у мітку однорядкової комісійної позиції (аудит #625 re-M-1)
        cont = []
        for nxt in lines[i + 1:i + 4]:
            if re.match(r"^\d+(\.\d+)?\s", nxt.strip()) or not nxt.strip():
                break
            cont.append(nxt.strip())
        label = (m.group(2) + " " + " ".join(cont)).strip()
        items.append({"n": m.group(1), "label": label[:120], "novat": _num(m.group(4))})

    def _is_package(x):   # пакет = ключове слово АБО має підпозиції «N.1 …» (у реальних актах пакет завжди «складається з»)
        return "програмної продукції" in x["label"] or bool(re.search(r"^" + re.escape(x["n"]) + r"\.\d+\s", text, re.M))
    commission = [x for x in items if not _is_package(x)]
    package = [x for x in items if _is_package(x)]
    base = {"stream": "prom", "items": items, "basis": "(без ПДВ)×1,2",
            "package_novat": round(sum(x["novat"] for x in package), 2)}
    t = re.search(r"\nВсього:\s*" + _AMT, text)
    if not items or not t:
        return _bad("позиції акта або «Всього:» не розпізнано", **base, commission_novat=None)
    if abs(sum(x["novat"] for x in items) - _num(t.group(1))) > 0.02:
        return _bad(f"контрольна сума позицій ({sum(x['novat'] for x in items):.2f}) ≠ «Всього:» ({t.group(1)}): є нерозпізнана позиція",
                    **base, commission_novat=None)
    nov = round(sum(x["novat"] for x in commission), 2)
    return {**base, "commission_novat": nov, "total": round(nov * VAT, 2)}


# ── акти ────────────────────────────────────────────────────────────────────────────────────────────────────
def act_period(date_str: str) -> str:
    """Місяць, ЗА який акт: акти приходять 3–16 числа наступного місяця (датовані кінцем місяця або днем відправки);
    день ≤ 20 → попередній місяць, інакше — місяць дати. 2026-07-31→2026-07; 2026-09-16→2026-08."""
    y, m, d = (int(x) for x in date_str.split("-"))
    if d <= 20:
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return f"{y:04d}-{m:02d}"


_MONTH_NOM = {"січень": 1, "лютий": 2, "березень": 3, "квітень": 4, "травень": 5, "червень": 6, "липень": 7, "серпень": 8,
              "вересень": 9, "жовтень": 10, "листопад": 11, "грудень": 12}
_MONTH_GEN = {"січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6, "липня": 7, "серпня": 8,
              "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12}


def period_from_text(text: str) -> str | None:
    """Місяць ЗА який акт — із самого тексту (надійніше за дату в імені файлу, яка може бути днем відправки після 20-го):
    «за Липень 2026 р.» (NovaPay/EVA/Prom) → «від 31 серпня 2026» (Rozetka) → «по 31.08.2026» (RozetkaPay)."""
    m = re.search(r"за\s+([А-Яа-яІіЇїЄє]+)\s+(\d{4})\s*р", text)
    if m and m.group(1).lower() in _MONTH_NOM:
        return f"{m.group(2)}-{_MONTH_NOM[m.group(1).lower()]:02d}"
    m = re.search(r"від\s+\d{1,2}\s+([А-Яа-яІіЇїЄє]+)\s+(\d{4})", text)
    if m and m.group(1).lower() in _MONTH_GEN:
        return f"{m.group(2)}-{_MONTH_GEN[m.group(1).lower()]:02d}"
    m = re.search(r"по\s+\d{2}\.(\d{2})\.(\d{4})", text)
    if m:
        return f"{m.group(2)}-{m.group(1)}"
    return None


def pdf_text(path: str) -> str:
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def load_overrides() -> dict:
    try:
        return json.loads((vch.DOCS_DIR / OVERRIDES_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def collect_acts(text_fn=pdf_text) -> list:
    """Акти потрібних контрагентів: [{vendor, doc_id, date, period, path, parsed:{...}|None, error}]."""
    out, seen = [], set()
    overrides = load_overrides()
    for f in vch.find_akt_files():
        base = os.path.basename(f)
        if not base.lower().endswith(".pdf"):
            continue
        info = vch.parse_akt_filename(base)
        if not info or info["vendor"] not in COUNTERPARTY_UA:
            continue
        key = (info["vendor"], info["doc_id"])
        if key in seen:      # дубль того самого акта (напр. *_ne_nova_vytrata.pdf) — рахуємо раз
            continue
        seen.add(key)
        period = (overrides.get(info["doc_id"]) or {}).get("period") or act_period(info["date"])
        rec = {"vendor": info["vendor"], "doc_id": info["doc_id"], "date": info["date"], "period": period,
               "path": f, "parsed": None, "error": None}
        try:
            text = text_fn(f)
            rec["text"] = text      # для перевірки очікувань бухгалтера (номери замовлень у тексті акта)
            if not (overrides.get(info["doc_id"]) or {}).get("period"):
                rec["period"] = period_from_text(text) or period
            rec["parsed"] = {"novapay": parse_novapay, "rozetkapay": parse_rozetkapay, "eva": parse_eva,
                             "rozetka": parse_rozetka, "prom": parse_prom}.get(info["vendor"], lambda t: None)(text)
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {str(e)[:100]}"
        out.append(rec)
    return out


def act_streams(acts: list) -> dict:
    """{(stream, period): {"docs":[doc_id...], "total":float|None, "basis":str, "missing":[...] }}. Потоки з двох актів
    (EVA, Rozetka) збираються з обох; якщо одного нема — total=None і `missing` каже чого бракує."""
    overrides = load_overrides()
    res = {}

    def put(stream, period, doc, total, basis, missing=None):
        r = res.setdefault((stream, period), {"docs": [], "total": None, "basis": basis, "missing": [], "parts": {}})
        r["docs"].append(doc)
        if missing:
            r["missing"] += missing
        return r

    by = defaultdict(list)
    for a in acts:
        by[(a["vendor"], a["period"])].append(a)

    for (vendor, period), group in by.items():
        if vendor in ("novapay", "rozetkapay", "prom"):
            for a in group:
                p = a["parsed"] or {}
                total = (overrides.get(a["doc_id"]) or {}).get("comparable", p.get("total"))
                r = put(vendor, period, a["doc_id"], total, p.get("basis", "?"))
                if total is not None:
                    r["total"] = round((r["total"] or 0) + total, 2)
                else:
                    r["missing"].append(f"{a['doc_id']}: {p.get('note') or a.get('error') or 'суму не прочитано'}")
        elif vendor == "eva":
            parts = {}
            for a in group:
                p = a["parsed"] or {}
                v = (overrides.get(a["doc_id"]) or {}).get("comparable", p.get("total"))
                parts[p.get("part", "?")] = v
                put("eva", period, a["doc_id"], v, "роялті + доступ (з ПДВ)")
            r = res[("eva", period)]
            miss = [k for k in ("royalty", "access") if parts.get(k) is None]
            r["missing"] = [f"немає акта/суми «{'роялті' if k == 'royalty' else 'доступ до платформи'}»" for k in miss]
            r["missing"] += [f"{a['doc_id']}: {(a['parsed'] or {}).get('note')}" for a in group if (a["parsed"] or {}).get("note")]
            r["total"] = round(sum(v for v in parts.values() if v is not None), 2) if not miss else None
            r["parts"] = parts
        elif vendor == "rozetka":
            roy = acc = None
            for a in group:
                p = dict(a["parsed"] or {})
                ov = overrides.get(a["doc_id"]) or {}
                for k in ("royalty_novat", "volume_novat", "logistics_novat"):   # ручна поправка суми акта (без ПДВ), якщо PDF не прочитано
                    if k in ov:
                        p[k] = ov[k]
                if p.get("doc") == "royalty":
                    roy = p
                elif p.get("doc") == "access":
                    acc = p
                put("rozetka_royalty", period, a["doc_id"], None, "")
            docs = [a["doc_id"] for a in group]
            vol = acc.get("volume_novat") if acc else None
            if roy and roy.get("royalty_novat") is not None and vol is not None:
                total = round(roy["royalty_novat"] + vol * VAT, 2)
                res[("rozetka_royalty", period)] = {"docs": docs, "total": total, "missing": [],
                                                  "basis": "роялті-акт (без ПДВ) + «доступ за обсягом»×1,2", "parts": {}}
            else:
                notes = [f"{a['doc_id']}: {(a['parsed'] or {}).get('note')}" for a in group if (a["parsed"] or {}).get("note")]
                res[("rozetka_royalty", period)] = {"docs": docs, "total": None, "basis": "", "parts": {},
                                                  "missing": ["потрібні ОБИДВА акти Розетки (роялті + доступ) з підтвердженими сумами"] + notes}
            lg = acc.get("logistics_novat") if acc else None
            res[("rozetka_logistics", period)] = {
                "docs": [a["doc_id"] for a in group if (a["parsed"] or {}).get("doc") == "access"],
                "total": round(lg * VAT, 2) if lg is not None else None, "basis": "«Організація видачі» (без ПДВ)×1,2",
                "parts": {}, "missing": [] if lg is not None else ["немає акта «доступ» / не прочитано логістику"]}
        elif vendor == "allo":
            for a in group:
                put("allo", period, a["doc_id"], (overrides.get(a["doc_id"]) or {}).get("comparable"), "?",
                    ["ALLO: окремої звірки комісій немає"])
    return res


# ── кандидати ───────────────────────────────────────────────────────────────────────────────────────────────
def _ym(date_str) -> str | None:
    t = str(date_str or "")
    m = re.match(r"^\s*(\d{1,2})\.(\d{2})\.(\d{4})", t)
    if m:
        return f"{m.group(3)}-{m.group(2)}"
    m = re.match(r"^\s*(\d{4})-(\d{2})-\d{2}", t)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def candidates_novapay() -> dict:
    """ПОВНО: усі архівні реєстри NovaPay, дедуп за ТТН. {period: {"sum":..,"n":..,"orders":[(id,amount)]}}"""
    import novapay_registry_kandydaty as npk
    rows = npk._rows_from(npk._all_registries())
    seen, out = set(), defaultdict(lambda: {"sum": 0.0, "n": 0, "orders": []})
    for r in rows:
        ttn = str(r.get("ttn") or "").strip()
        if not ttn or ttn in seen or r.get("commission") is None:
            continue
        seen.add(ttn)
        ym = _ym(r.get("date"))
        if not ym:
            continue
        o = out[ym]
        o["sum"] = round(o["sum"] + float(r["commission"]), 2)
        o["n"] += 1
        o["orders"].append((str(r.get("internal_order_id") or ttn), float(r["commission"])))
    return dict(out)


def candidates_rozetkapay() -> dict:
    """ПОВНО: усі архівні реєстри RozetkaPay, дедуп за фін-номером. Еквайринг = Σ|комісія| платежів − комісія, повернена
    сторно (її знак у реєстрі зворотній). Місяць — за датою ОПЛАТИ покупцем (date_pay); альтернатива date_transfer —
    рішення бухгалтера (різниця ≈ кілька ₴ на межі місяців)."""
    import rozetkapay_registry_kandydaty as rpk
    seen, out = set(), defaultdict(lambda: {"sum": 0.0, "n": 0, "orders": []})
    files = [f for f in glob.glob(str(rpk.DOCS_DIR / "*" / "RozetkaPay" / "*.xlsx")) if not os.path.basename(f).startswith("~$")]
    for f in sorted(files, key=os.path.getmtime):
        try:
            rows = rpk._parse_registry(Path(f))
        except Exception as e:  # noqa: BLE001
            print(f"[AktyZvirka] {os.path.basename(f)}: не прочитано ({e})", file=sys.stderr)
            continue
        for r in rows:
            fin = r.get("finop") or f"{r.get('order_id')}|{r.get('date_pay')}|{r.get('sum')}"
            if fin in seen or not isinstance(r.get("commission"), (int, float)):
                continue
            seen.add(fin)
            ym = _ym(r.get("date_pay") or r.get("date_transfer"))
            if not ym:
                continue
            # платіж: commission<0 → еквайринг +|c|; сторно: commission>0 → еквайринг −c. В обох випадках = −commission.
            amt = -float(r["commission"])
            o = out[ym]
            o["sum"] = round(o["sum"] + amt, 2)
            o["n"] += 1
            o["orders"].append((str(r.get("order_id")), round(amt, 2)))
    return dict(out)


def candidates_from_registry(reg: dict) -> dict:
    """НЕПОВНО: з `_vidkryti_kandydaty.json` (open+resolved): rozetka_commission (роялті/логістика окремо за ключем
    «order:royalty|logistics»), eva_commission (дата = перше побачення). {stream: {period: {...}}}"""
    out = {s: defaultdict(lambda: {"sum": 0.0, "n": 0, "orders": []}) for s in ("rozetka_royalty", "rozetka_logistics", "eva")}
    for full_key, e in reg.items():
        src = e.get("source")
        amt = e.get("sum")
        if not isinstance(amt, (int, float)):
            continue
        if src == "rozetka_commission":
            stream = "rozetka_logistics" if str(e.get("key", "")).endswith(":logistics") else "rozetka_royalty"
            ym = _ym(e.get("date"))
        elif src == "eva_commission":
            stream, ym = "eva", _ym(e.get("first_seen"))
        else:
            continue
        if not ym:
            continue
        o = out[stream][ym]
        o["sum"] = round(o["sum"] + float(amt), 2)
        o["n"] += 1
        o["orders"].append((str(e.get("key")), float(amt)))
    return {s: dict(v) for s, v in out.items()}


def candidates_prom() -> dict:
    """НЕПОВНО: щоденні `*_prom_komisiya_kandydaty.json` (лише нові за курсором). Місяць — за датою файлу."""
    out = defaultdict(lambda: {"sum": 0.0, "n": 0, "orders": []})
    seen = set()
    for f in sorted(glob.glob(str(vch.DOCS_DIR / "*" / "Prom" / "*_prom_komisiya_kandydaty.json"))):
        ym = _ym(os.path.basename(f)[:10])
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for c in data if isinstance(data, list) else []:
            oid = str(c.get("order_id"))
            if oid in seen or not isinstance(c.get("commission_total"), (int, float)) or not ym:
                continue
            seen.add(oid)
            o = out[ym]
            o["sum"] = round(o["sum"] + float(c["commission_total"]), 2)
            o["n"] += 1
            o["orders"].append((oid, float(c["commission_total"])))
    return dict(out)


# ── книга ───────────────────────────────────────────────────────────────────────────────────────────────────
def _core(doc_id: str):
    m = re.search(r"\d+$", doc_id)
    core = m.group(0).lstrip("0") if m else None
    return core if core and len(core) >= 5 else None


def classify_cell(doc_id: str, cell: str) -> str | None:
    """'declared' — акт названо ВЛАСНИМ номером рядка: у перших 100 символах (логіка vchasno_akty_kandydaty, захист від
    хибного ✅ через побічну згадку, рядок 119) АБО у формі «… (Вчасно <номер>)» у перших 250 символах (формат бухгалтера для
    НоваПей, рядки 188/190). Слово «Акт» саме по собі НЕ рахується: у приміткових рядках продажів («…вже в Акті Prom №…») воно
    стоїть у перших 250 символах (аудит #625 M-1). 'mentioned' — номер лише десь далі в тексті; None — нема."""
    if vch._already_in_book(doc_id, [cell]):
        return "declared"
    head = cell[:250]
    core = _core(doc_id)
    pat = re.escape(doc_id) + (r"|" + re.escape(core) if core else "")
    first = re.search(r"\(Вчасно\s*№?\s*([A-Za-z0-9\-]+)", head)   # ПЕРШЕ «(Вчасно …)» у клітинці — власний номер рядка
    if first and re.fullmatch(pat, first.group(1)):
        return "declared"
    if doc_id in cell or (core and core in cell):
        return "mentioned"
    return None


def book_rows_for(doc_ids: list) -> dict:
    """READ-ONLY: {doc_id: {"declared": [рядки], "mentioned": [рядки]}} за графами 5 і 12. Книгу не пише; недоступну —
    порожньо + попередження (тоді «в книзі» невідомо, звіт це не вигадує)."""
    res = {d: {"declared": [], "mentioned": []} for d in doc_ids}
    if not vch.KODV_XLSX.exists():
        return None
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(vch.KODV_XLSX), data_only=True, read_only=True)
        ws = wb["КОДВ"]
        for rownum, row in enumerate(ws.iter_rows(min_row=7), start=7):   # номер рядка — з лічильника (EmptyCell у read_only .row не має)
            for col in (4, 11):          # графа 5 (E) і графа 12 (L)
                v = row[col].value if len(row) > col else None
                if not v:
                    continue
                cell = str(v)
                for d in doc_ids:
                    kind = classify_cell(d, cell)
                    if kind:
                        res[d][kind].append(rownum)
    except Exception as e:  # noqa: BLE001
        print(f"[AktyZvirka] книгу не прочитано ({e}) — «в книзі» невідомо.", file=sys.stderr)
        return None
    return {d: {k: sorted(set(v)) for k, v in kinds.items()} for d, kinds in res.items()}


# ── звіт ────────────────────────────────────────────────────────────────────────────────────────────────────
def build_rows(acts: list, cands: dict, book: dict, only_month: str = None) -> list:
    """cands: {stream: {period: {sum,n,orders}}}. Повертає рядки звіту (по потоку × місяцю), найновіші вгорі."""
    streams = act_streams(acts)
    keys = set(streams.keys())
    for s, per in cands.items():
        for ym in per:
            keys.add((s, ym))
    rows = []
    for (stream, ym) in sorted(keys, key=lambda k: (k[1], k[0]), reverse=True):
        if only_month and ym != only_month:
            continue
        a = streams.get((stream, ym))
        c = (cands.get(stream) or {}).get(ym)
        total = a["total"] if a else None
        csum = c["sum"] if c else None
        diff = round(csum - total, 2) if (total is not None and csum is not None) else None
        if a is None:
            status = "акта нема"
        elif total is None:
            status = "суму акта не прочитано"
        elif c is None:
            status = "кандидатів нема"
        elif abs(diff) <= TOLERANCE:
            status = "✅ збіг"
        else:
            status = "⚠️ різниця"
        docs = (a or {}).get("docs", [])
        book_known = book is not None
        per_doc = {d: ((book or {}).get(d) or {"declared": [], "mentioned": []}) for d in docs}
        not_in_book = [d for d in docs if book_known and not per_doc[d]["declared"]]
        if not_in_book:
            status += " · 📕 акт НЕ в книзі: " + ", ".join(not_in_book)
        rows.append({
            "stream": stream, "period": ym, "docs": docs, "act_total": total, "act_basis": (a or {}).get("basis", ""),
            "act_missing": (a or {}).get("missing", []), "cand_sum": csum, "cand_n": (c or {}).get("n", 0),
            "complete": stream in COMPLETE_STREAMS, "diff": diff, "status": status,
            "book_known": book_known, "per_doc_book": per_doc, "not_in_book": not_in_book,
            "book_rows": sorted({r for d in docs for r in per_doc[d]["declared"]}),
            "book_mentions": sorted({r for d in docs for r in per_doc[d]["mentioned"]} - {r for d in docs for r in per_doc[d]["declared"]}),
            "orders": (c or {}).get("orders", []),
        })
    return rows


def _book_cell(r: dict) -> str:
    """По КОЖНОМУ акту потоку окремо (аудит #625 H-1: об'єднання рядків ховало відсутність одного з двох актів)."""
    if not r["docs"]:
        return "—"
    if not r["book_known"]:
        return "невідомо (книгу не прочитано)"
    parts = []
    for d in r["docs"]:
        b = r["per_doc_book"][d]
        txt = ("р." + ", ".join(str(x) for x in b["declared"])) if b["declared"] else "НЕМАЄ"
        if b["mentioned"]:
            txt += f" (лише згадано: {', '.join(str(x) for x in b['mentioned'])})"
        parts.append(txt if len(r["docs"]) == 1 else f"{d}: {txt}")
    return "; ".join(parts)


def _f(x) -> str:
    return "—" if x is None else f"{x:.2f}"


def render(rows: list, today: str) -> str:
    L = [f"# Звірка місячних актів контрагентів ↔ кандидати комісій ({today})", "",
         "Звіт ЛИШЕ для звірки (правило «одне правило», довідник §3): комісії вносяться ОДНИМ рядком на акт, не в рядки продажів. "
         "Різниця = кандидати − акт. ✅ — збіг до 5 копійок. «Повнота»: **повні** — усі архівні реєстри (NovaPay, RozetkaPay); "
         "**неповні** — лише те, що бачили кабінет/API (Rozetka, EVA, Prom): різниця тут НЕ доводить помилку, лише підказує "
         "(місяць Prom = дата файлу кандидатів, місяць EVA = перше побачення в реєстрі, а не дата замовлення; перші прогони тягнули "
         "30-денний lookback — тому такі потоки дають хибні ⚠️ на межі місяців). "
         "Акти не містять переліку замовлень, тому «замовлень поза актом» напряму не видно — при ⚠️ нижче перелік замовлень потоку. "
         "Суму акта можна виправити/задати вручну в `документи_КОДВ/_akty_sumy.json`: "
         '`{"<номер акта>": {"comparable": 12.34, "period": "2026-08"}}`.', ""]
    if not rows:
        return "\n".join(L + ["_Даних немає._"]) + "\n"
    L += ["| Місяць | Контрагент / потік | Акт (№) | Сума акта | База | Кандидати (к-ть) | Сума кандидатів | Різниця | Повнота | У книзі (рядок №) | Статус |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        cp = COUNTERPARTY_UA.get(r["stream"].split("_")[0], r["stream"])
        L.append("| " + " | ".join([
            r["period"], f"{cp}: {STREAM_UA.get(r['stream'], r['stream'])}", ", ".join(r["docs"]) or "—",
            _f(r["act_total"]), r["act_basis"] or "—", str(r["cand_n"]), _f(r["cand_sum"]), _f(r["diff"]),
            "повні (за наявними архівами)" if r["complete"] else "неповні",
            _book_cell(r),
            r["status"] + (f" ({'; '.join(r['act_missing'])})" if r["act_missing"] else "")]) + " |")
    diffs = [r for r in rows if r["status"].startswith("⚠️ різниця") and r["orders"]]
    if diffs:
        L += ["", "## Замовлення потоків із різницею (для пошуку зайвого/пропущеного)", ""]
        for r in diffs:
            items = sorted(r["orders"], key=lambda x: -abs(x[1]))[:40]
            L.append(f"- **{r['period']} {STREAM_UA.get(r['stream'], r['stream'])}** (різниця {r['diff']:+.2f}; "
                     f"{'повні' if r['complete'] else 'неповні'} дані): " + "; ".join(f"{o} {a:.2f}" for o, a in items)
                     + ("; …" if len(r["orders"]) > 40 else ""))
    return "\n".join(L) + "\n"


# ── очікування бухгалтера (запит КОДВ_CHANNEL.md 2026-10-08) ───────────────────────────────────────────────────
# `документи_КОДВ/_ochikuvani_v_aktakh.json`: суми, ПРИБРАНІ з рядків продажів «одним правилом», що МАЮТЬ з'явитися в місячному акті.
# Мета бухгалтера: жодна сума не губиться між «прибрано з продажу» і «внесено актом», і це не тримається на її пам'яті.
# Реальні акти НЕ містять переліку замовлень (перевірено 12.10.2026 на 11 актах: жоден з номерів очікувань у тексті PDF не зустрівся),
# тому доказ — трьох ступенів, і звіт ЗАВЖДИ каже, який саме:
#   1. «номер замовлення в тексті акта» — усі номери очікування є в тексті акта (на випадок, коли контрагент колись додасть перелік);
#   2. «акт = сума кандидатів потоку» — потік (контрагент × місяць) має статус «✅ збіг» (акт == Σ кандидатів до 5 коп), а замовлення
#      очікування входить у кандидати цього потоку з тією самою сумою (композиція: якщо акт дорівнює Σ кандидатів, то вона містить цей рядок);
#      (⚠️ для НЕповних потоків — Rozetka/EVA/Prom — «✅ збіг» теоретично може бути випадковим; на практиці малоймовірно, але це лише непряма композиція)
#   3. інакше — НЕ закрито: «❗ очікувана сума не підтверджена актом» з причиною (акт без переліку, потік ⚠️ різниця, замовлення нема
#      в кандидатах…). Ручне «✅» бухгалтера (поле `статус`, напр. звірка з кабінетом продавця) НЕ перетирається й не дає ❗: це «ℹ️ закрито вручну».
# Закриті автоматично (1, 2) отримують поле `закрито_актом` у файлі очікувань (тільки це поле, решта файлу не чіпається; запис атомарний).
EXPECT_NAME = "_ochikuvani_v_aktakh.json"
EXPECT_REPORT_NAME = "_zvirka_ochikuvannya.json"
_ORDER_TOKEN_RE = re.compile(r"(?:[A-Za-z]{2,4}-)?\d[\d-]{5,}")


def _vendor_of(counterparty: str):
    c = (counterparty or "").lower()
    if "розетка пей" in c or "rozetkapay" in c:
        return "rozetkapay"
    if "розетк" in c:
        return "rozetka"
    if "руш" in c or "eva" in c:
        return "eva"
    if "prom" in c or "промо" in c or "уапром" in c:
        return "prom"
    if "новапей" in c or "novapay" in c or "нова пей" in c:
        return "novapay"
    if "алло" in c or "allo" in c:
        return "allo"
    return None


def _stream_of(vendor, composition: str):
    if vendor == "rozetka":
        return "rozetka_logistics" if "логіст" in (composition or "").lower() else "rozetka_royalty"
    return vendor


def _order_tokens(orders_field) -> list:
    """«904621035 / RMP-283162840», «906402279, 906412447, 905840122» → унікальні токени-номери у порядку появи."""
    seen, out = set(), []
    for t in _ORDER_TOKEN_RE.findall(str(orders_field or "")):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def check_expectation(exp: dict, acts: list, rows: list, cands: dict) -> dict:
    """Повертає {"verdict": closed|manual|pending|unproven, "evidence": str, "detail": str, "docs": [..]}. Чиста функція (без I/O)."""
    vendor = _vendor_of(exp.get("контрагент"))
    stream = _stream_of(vendor, exp.get("склад"))
    period = exp.get("акт_за")
    manual = str(exp.get("статус") or "").lstrip().startswith("✅")
    if vendor is None or not period:
        return {"verdict": "unproven", "evidence": "", "docs": [],
                "detail": f"не вдалось визначити контрагента/місяць акта («{exp.get('контрагент')}», акт_за={period})"}
    docs_acts = [a for a in acts if a["vendor"] == vendor and a["period"] == period]
    if not docs_acts:
        return {"verdict": "manual" if manual else "pending", "evidence": "", "docs": [],
                "detail": f"акта {COUNTERPARTY_UA.get(vendor, vendor)} за {period} ще немає в `документи_КОДВ` — очікування лишається відкритим"
                          + (" (бухгалтер уже позначила ✅ вручну)" if manual else "")}
    doc_ids = [a["doc_id"] for a in docs_acts]
    tokens = _order_tokens(exp.get("замовлення"))
    try:
        amount = _num(exp.get("сума") or 0)     # «30,52» рядком теж читається; сміття → не валимо звіт
    except (ValueError, TypeError):
        return {"verdict": "unproven", "evidence": "", "docs": doc_ids, "detail": f"сума очікування не число: {exp.get('сума')!r}"}
    # 1. номери замовлень у тексті акта
    texts = [a.get("text") or "" for a in docs_acts]
    # номер — ціле «слово»: не частина довшого числа (IBAN, ЄДРПОУ, суми) — аудит #687
    if tokens and all(any(re.search(r"(?<![\d-])" + re.escape(t) + r"(?![\d])", tx) for tx in texts) for t in tokens):
        return {"verdict": "closed", "evidence": "номер замовлення в тексті акта", "docs": doc_ids,
                "detail": f"усі номери ({', '.join(tokens)}) є в тексті {', '.join(doc_ids)}"}
    # 2. композиція: потік «✅ збіг» + замовлення входить у кандидати з тією самою сумою
    row = next((r for r in rows if r["stream"] == stream and r["period"] == period), None)
    c_orders = ((cands.get(stream) or {}).get(period) or {}).get("orders", [])
    by_id = {}
    for o, amt in c_orders:
        by_id.setdefault(str(o).split(":")[0], []).append(float(amt))
    matched = [t for t in tokens if t in by_id]
    cand_sum = round(sum(by_id[t][0] for t in matched), 2) if matched else 0.0
    row_ok = bool(row) and str(row["status"]).startswith("✅ збіг")
    amount_ok = bool(matched) and abs(cand_sum - amount) <= TOLERANCE
    # сума очікування має збігатися з кандидатами ТІЛЬКИ по тих токенах, що є в реєстрі; якщо токенів більше (напр. «замовлення / RMP-…» —
    # той самий платіж двома номерами) — достатньо, що збіглася сума по знайдених
    if row_ok and amount_ok:
        return {"verdict": "closed", "evidence": "акт = сума кандидатів потоку, замовлення входить у кандидати", "docs": doc_ids,
                "detail": f"потік {STREAM_UA.get(stream, stream)} за {period}: акт {row['act_total']} = кандидати {row['cand_sum']} (✅ збіг); "
                          f"замовлення {', '.join(matched)} у кандидатах = {cand_sum}"}
    reasons = ["акт не містить переліку замовлень (номерів немає в тексті PDF)"]
    if row is None:
        reasons.append("потоку немає у звіті")
    elif not row_ok:
        reasons.append(f"потік {STREAM_UA.get(stream, stream)} за {period}: {row['status'].split(' · ')[0]}"
                       + (f" (акт {_f(row['act_total'])} / кандидати {_f(row['cand_sum'])}, неповні дані)" if not row["complete"] else ""))
    if not matched:
        reasons.append(f"замовлення ({', '.join(tokens) or '—'}) нема серед кандидатів потоку")
    elif not amount_ok:
        reasons.append(f"сума кандидатів по замовленню {cand_sum} ≠ очікувана {amount}")
    return {"verdict": "manual" if manual else "unproven", "evidence": "", "docs": doc_ids, "detail": "; ".join(reasons)}


def evaluate_expectations(acts: list, rows: list, cands: dict, path=None) -> tuple:
    """Читає файл очікувань → (список результатів, шлях|None). Файл відсутній/битий → ([], None) (не помилка)."""
    p = Path(path) if path else vch.DOCS_DIR / EXPECT_NAME
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], None
    out = []
    items = data.get("очікування") if isinstance(data, dict) else None
    for i, e in enumerate(items if isinstance(items, list) else []):
        if not isinstance(e, dict):
            continue
        res = check_expectation(e, acts, rows, cands)
        res.update({"index": i, "книга_рядок": e.get("книга_рядок"), "контрагент": e.get("контрагент"), "акт_за": e.get("акт_за"),
                    "замовлення": e.get("замовлення"), "склад": e.get("склад"), "сума": e.get("сума"),
                    "вже_закрито": bool(e.get("закрито_актом"))})
        out.append(res)
    return out, p


def apply_closures(results: list, path, today: str) -> int:
    """Проставляє `закрито_актом` ЛИШЕ для автоматично доведених (closed) очікувань, яких ще не закрито. Файл перечитується безпосередньо
    перед записом (бухгалтер могла його змінити за час прогону), чіпається тільки це поле, запис атомарний (tmp + replace).
    Повертає кількість нових закриттів."""
    todo = {r["index"]: r for r in results if r["verdict"] == "closed" and not r["вже_закрито"]}
    if not todo or path is None:
        return 0
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    items = data.get("очікування") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return 0
    n = 0
    for i, r in todo.items():
        if i >= len(items) or not isinstance(items[i], dict):
            continue
        if i >= len(items) or items[i].get("закрито_актом"):
            continue
        key = lambda e: (str(e.get("замовлення")), e.get("сума"), e.get("акт_за"))   # не закриваємо «не той» запис, якщо файл змінили між читаннями
        if key(items[i]) != (str(r["замовлення"]), r["сума"], r["акт_за"]):
            continue
        items[i]["закрито_актом"] = {"доказ": r["evidence"], "акти": r["docs"], "перевірено": today}
        n += 1
    if n:
        tmp = Path(str(path) + ".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:     # файл відкритий у редакторі / нема прав: закриття не записано, наступний прогін спробує знову
            print(f"[AktyZvirka] ⚠️ не вдалось записати закрито_актом у {path}: {type(e).__name__} — залишено без змін", file=sys.stderr)
            try:
                tmp.unlink()
            except OSError:
                pass
            return 0
    return n


def render_expectations(results: list) -> str:
    if not results:
        return ""
    L = ["", "## Очікування бухгалтера (суми, прибрані з рядків продажів; мають бути в акті) — `_ochikuvani_v_aktakh.json`", "",
         "Реальні акти не містять номерів замовлень, тому доказ — або номер у тексті акта, або «акт = Σ кандидатів потоку, і замовлення в кандидатах». "
         "❗ означає «автоматично не підтверджено», а НЕ «суми в акті немає»; ручне ✅ бухгалтера (звірка з кабінетом) не перетирається.", "",
         "| Рядок книги | Контрагент | Акт за | Замовлення | Склад | Сума | Статус | Пояснення |", "|---|---|---|---|---|---|---|---|"]
    mark = {"closed": "✅ закрито актом", "manual": "ℹ️ закрито вручну (авто-доказу нема)", "pending": "⏳ акта ще нема", "unproven": "❗ очікувана сума не підтверджена актом"}
    for r in results:
        rn = r["книга_рядок"]
        rn = ", ".join(str(x) for x in rn) if isinstance(rn, list) else str(rn)
        st = mark[r["verdict"]] + (f" — {r['evidence']}" if r["evidence"] else "")
        if r["verdict"] == "closed" and r["вже_закрито"]:
            st += " (раніше закрито)"
        L.append(f"| {rn} | {r['контрагент']} | {r['акт_за']} | {r['замовлення']} | {r['склад']} | {r['сума']} | {st} | {r['detail']} |")
    open_ = [r for r in results if r["verdict"] == "unproven"]
    if open_:
        L += ["", f"**❗ Не підтверджено автоматично: {len(open_)}** — " + "; ".join(f"р.{r['книга_рядок']} {r['замовлення']} {r['сума']}" for r in open_)]
    return "\n".join(L) + "\n"


def run(only_month: str = None) -> list:
    acts = collect_acts()
    cands = {"novapay": candidates_novapay(), "rozetkapay": candidates_rozetkapay(), "prom": candidates_prom()}
    cands.update(candidates_from_registry(kandydaty_registry._load_registry()))
    book = book_rows_for(sorted({a["doc_id"] for a in acts}))
    rows = build_rows(acts, cands, book, only_month)
    out_md = vch.DOCS_DIR / REPORT_NAME
    today = datetime.now().strftime("%Y-%m-%d")
    main_md = render(rows, today)
    out_md.write_text(main_md, encoding="utf-8")        # основний звіт пишеться ЗАВЖДИ першим: збій блоку очікувань його не зачіпає
    exp_results, newly = [], 0
    try:
        all_rows = rows if not only_month else build_rows(acts, cands, book, None)    # очікування не залежать від фільтра --month
        exp_results, exp_path = evaluate_expectations(acts, all_rows, cands)
        newly = apply_closures(exp_results, exp_path, today)
        out_md.write_text(main_md + render_expectations(exp_results), encoding="utf-8")
        if exp_results:
            (vch.DOCS_DIR / EXPECT_REPORT_NAME).write_text(json.dumps(exp_results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"[AktyZvirka] ⚠️ блок «очікування бухгалтера» пропущено: {type(e).__name__}: {str(e)[:120]} (основний звіт записано)", file=sys.stderr)
        exp_results = []
    (vch.DOCS_DIR / JSON_NAME).write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    unread = [a for a in acts if a["error"] or not a["parsed"]]
    run.unread_pdf = [a for a in acts if a["error"]]    # саме ПОМИЛКА читання PDF (а не «акт прочитано, але формат невідомий»)
    print(f"[AktyZvirka] актів: {len(acts)} (не прочитано: {len(unread)}); рядків звіту: {len(rows)}; "
          f"⚠️ різниця: {sum(1 for r in rows if r['status'].startswith('⚠️ різниця'))}; звіт: {out_md}")
    if exp_results:
        cnt = {v: sum(1 for r in exp_results if r["verdict"] == v) for v in ("closed", "manual", "pending", "unproven")}
        print(f"[AktyZvirka] очікування бухгалтера: {len(exp_results)} (закрито актом: {cnt['closed']}, нових закриттів записано: {newly}; "
              f"закрито вручну: {cnt['manual']}; акта ще нема: {cnt['pending']}; ❗ не підтверджено: {cnt['unproven']})")
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="лише цей місяць, YYYY-MM")
    run(ap.parse_args().month)
    # Запобіжник «тихих нулів» (запит бухгалтера 27.10.2026): PDF не відкрився (напр. зник pdfplumber) → рядок у звіті + код ≠ 0.
    if getattr(run, "unread_pdf", None):
        bad = run.unread_pdf
        _rep = vch.DOCS_DIR / REPORT_NAME
        try:
            _rep.write_text("## ❗ НЕ ПРОЧИТАНО PDF-АКТІВ: " + str(len(bad)) + " — звірка НЕПОВНА, нулі не означають «розбіжностей нема»\n\n"
                            + "\n".join(f"- {a['vendor']} {a['doc_id']}: {a['error']}" for a in bad) + "\n\n"
                            + _rep.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError as e:
            print(f"[AktyZvirka] не вдалось дописати попередження у звіт: {e}", file=sys.stderr)
        print(f"[AktyZvirka] ❗ не прочитано PDF: {len(bad)} — вихід з кодом 1", file=sys.stderr)
        sys.exit(1)
