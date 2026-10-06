"""
akty_zvirka.py — звіт-звірка «місячні акти контрагентів (Вчасно) ↔ сума кандидатів комісій».

НАВІЩО (запит головного бухгалтера, КОДВ_CHANNEL.md 2026-10-06; «одне правило» — довідник §3, журнал (142)):
комісії NovaPay, RozetkaPay, Rozetka (роялті/логістика), EVA і Prom вносяться в книгу ОДНИМ рядком на місячний
акт, а не в рядки продажів. Реєстри/кандидати лишаються ІНСТРУМЕНТОМ ЗВІРКИ акта із замовленнями. Раніше цю
звірку 05.10 робив вручну агент; тут вона автоматична: для кожного контрагента й місяця —
«акт є/нема», сума акта (із PDF), сума кандидатів (замовлення/реєстри), різниця, «акт уже в книзі рядком №…».

ЩО ВОНО РОБИТЬ (READ-ONLY щодо книги й реєстрів; пише лише `документи_КОДВ/_zvirka_aktiv.md/.json`):
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


_AMT = r"(\d[\d\s ]*,\d{2})"


# ── парсери актів (чисті функції над текстом PDF) ─────────────────────────────────────────────────────────
def parse_novapay(text: str) -> dict:
    m = re.search(r"Сума винагороди Платіжної установи за надані\s+послуги\s+" + _AMT + r"\s*грн", text)
    return {"stream": "novapay", "total": _num(m.group(1)) if m else None, "basis": "без ПДВ"}


def parse_rozetkapay(text: str) -> dict:
    m = re.search(r"Разом\s+" + _AMT + r"\s+" + _AMT, text)
    return {"stream": "rozetkapay", "total": _num(m.group(2)) if m else None, "basis": "без ПДВ"}


def parse_eva(text: str) -> dict:
    """Два різні акти EVA: «використання ТОРГОВЕЛЬНИХ МАРОК» (роялті, не об'єкт ПДВ) і «надання ПОСЛУГИ ДОСТУПУ»
    (сума вже з ПДВ). Повертає частину потоку `eva`: part=royalty|access."""
    if "ТОРГОВЕЛЬНИХ МАРОК" in text.upper() and "роялті" in text:
        m = re.search(r"Загальна сума роялті[^\n]*?склала\s+" + _AMT + r"\s*грн", text, re.S)
        return {"stream": "eva", "part": "royalty", "total": _num(m.group(1)) if m else None, "basis": "без ПДВ (не об'єкт ПДВ)"}
    m = re.search(r"Загальна сума за послуги складає\s+" + _AMT + r"\s*грн", text)
    return {"stream": "eva", "part": "access", "total": _num(m.group(1)) if m else None, "basis": "з ПДВ"}


def parse_rozetka(text: str) -> dict:
    """Два акти Термінал Розетка. Роялті-акт: «Роялті за користування торгової марки» (сума без ПДВ).
    Акт «доступ»: логістика «Організація видачі», щомісячна плата, «доступ ... в зв'язку з обсягом продажу» — усе
    без ПДВ; у книзі комісії з ПДВ → ×1,2 (журнал 322,13 = 178,97 + 143,16; 61,20)."""
    if "Роялті за користування" in text:
        m = re.search(r"Роялті за користування[^\n]*?" + _AMT + r"\s+" + _AMT + r"\s*\n", text)
        return {"doc": "royalty", "royalty_novat": _num(m.group(2)) if m else None}
    out = {"doc": "access"}
    m = re.search(r"Організація видачі відправлень[^\n]*?шт\s+[\d,\s]+?\s" + _AMT + r"\s*\n", text)
    out["logistics_novat"] = _num(m.group(1)) if m else None
    m = re.search(r"Безумовна щомісячна плата[^\n]*\n\s*\d+\s+\d+\s+послуга\s+[\d,\s]+?\s" + _AMT + r"\s*\n", text)
    out["monthly_novat"] = _num(m.group(1)) if m else None
    m = re.search(r"в\s+зв.язку з обсягом продажу", text)
    m2 = re.search(r"\n\s*\d+\s+\d+\s+послуга\s+[\d,\s]+?\s" + _AMT + r"\s*\n[^\n]*обсягом продажу", text)
    out["volume_novat"] = _num(m2.group(1)) if (m and m2) else None
    return out


def parse_prom(text: str) -> dict:
    """Позиції верхнього рівня «N <назва> 1 грн ціна сума» (без крапки в номері). Пакет «програмної продукції» —
    передоплата послуги (виняток §3), НЕ комісія замовлень → виключається. Решта (ProSale, компенсація доставки,
    «Оплатити частинами» тощо) ×1,2 = порівнюване з кандидатами (cpa Prom — з ПДВ)."""
    items = []
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        m = re.match(r"^(\d+)\s+(.*?)\s+1\s+грн\s+" + _AMT + r"\s+" + _AMT + r"\s*$", ln.strip())
        if not m:
            continue
        label = (m.group(2) + " " + (lines[i + 1] if i + 1 < len(lines) else "")).strip()
        items.append({"n": m.group(1), "label": label[:90], "novat": _num(m.group(4))})
    commission = [x for x in items if "програмної продукції" not in x["label"]]
    package = [x for x in items if "програмної продукції" in x["label"]]
    nov = round(sum(x["novat"] for x in commission), 2) if commission else None
    return {"stream": "prom", "items": items, "commission_novat": nov,
            "total": round(nov * VAT, 2) if nov is not None else None, "basis": "(без ПДВ)×1,2",
            "package_novat": round(sum(x["novat"] for x in package), 2)}


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
            r["total"] = round(sum(v for v in parts.values() if v is not None), 2) if not miss else None
            r["parts"] = parts
        elif vendor == "rozetka":
            roy = acc = None
            for a in group:
                p = a["parsed"] or {}
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
                res[("rozetka_royalty", period)] = {"docs": docs, "total": None, "basis": "", "parts": {},
                                                  "missing": ["потрібні ОБИДВА акти Розетки (роялті + доступ) з прочитаними сумами"]}
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
    хибного ✅ через побічну згадку, рядок 119) АБО в перших 250 символах клітинки, що починається з опису акта
    («…Акт прийому-передачі … (Вчасно BO0000489750)» — формат бухгалтера для НоваПей, рядки 188/190);
    'mentioned' — номер лише десь далі в тексті; None — нема."""
    if vch._already_in_book(doc_id, [cell]):
        return "declared"
    head = cell[:250]
    core = _core(doc_id)
    if "Акт" in head and (doc_id in head or (core and core in head)):
        return "declared"
    if doc_id in cell or (core and core in cell):
        return "mentioned"
    return None


def book_rows_for(doc_ids: list) -> dict:
    """READ-ONLY: {doc_id: {"declared": [рядки], "mentioned": [рядки]}} за графами 5 і 12. Книгу не пише; недоступну —
    порожньо + попередження (тоді «в книзі» невідомо, звіт це не вигадує)."""
    res = {d: {"declared": [], "mentioned": []} for d in doc_ids}
    if not vch.KODV_XLSX.exists():
        return res
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(vch.KODV_XLSX), data_only=True, read_only=True)
        ws = wb["КОДВ"]
        for row in ws.iter_rows(min_row=7):
            for col in (4, 11):          # графа 5 (E) і графа 12 (L)
                v = row[col].value if len(row) > col else None
                if not v:
                    continue
                cell = str(v)
                for d in doc_ids:
                    kind = classify_cell(d, cell)
                    if kind:
                        res[d][kind].append(row[4].row)
    except Exception as e:  # noqa: BLE001
        print(f"[AktyZvirka] книгу не прочитано ({e}) — «в книзі» невідомо.", file=sys.stderr)
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
        rows.append({
            "stream": stream, "period": ym, "docs": docs, "act_total": total, "act_basis": (a or {}).get("basis", ""),
            "act_missing": (a or {}).get("missing", []), "cand_sum": csum, "cand_n": (c or {}).get("n", 0),
            "complete": stream in COMPLETE_STREAMS, "diff": diff, "status": status,
            "book_rows": sorted({r for d in docs for r in (book.get(d) or {}).get("declared", [])}),
            "book_mentions": sorted({r for d in docs for r in (book.get(d) or {}).get("mentioned", [])}
                                    - {r for d in docs for r in (book.get(d) or {}).get("declared", [])}),
            "orders": (c or {}).get("orders", []),
        })
    return rows


def _f(x) -> str:
    return "—" if x is None else f"{x:.2f}"


def render(rows: list, today: str) -> str:
    L = [f"# Звірка місячних актів контрагентів ↔ кандидати комісій ({today})", "",
         "Звіт ЛИШЕ для звірки (правило «одне правило», довідник §3): комісії вносяться ОДНИМ рядком на акт, не в рядки продажів. "
         "Різниця = кандидати − акт. ✅ — збіг до 5 копійок. «Повнота»: **повні** — усі архівні реєстри (NovaPay, RozetkaPay); "
         "**неповні** — лише те, що бачили кабінет/API (Rozetka, EVA, Prom): різниця тут НЕ доводить помилку, лише підказує. "
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
            (", ".join(str(x) for x in r["book_rows"]) or ("нема" if r["docs"] else "—"))
            + (f" (лише згадано: {', '.join(str(x) for x in r['book_mentions'])})" if r["book_mentions"] else ""),
            r["status"] + (f" ({'; '.join(r['act_missing'])})" if r["act_missing"] else "")]) + " |")
    diffs = [r for r in rows if r["status"] == "⚠️ різниця" and r["orders"]]
    if diffs:
        L += ["", "## Замовлення потоків із різницею (для пошуку зайвого/пропущеного)", ""]
        for r in diffs:
            items = sorted(r["orders"], key=lambda x: -abs(x[1]))[:40]
            L.append(f"- **{r['period']} {STREAM_UA.get(r['stream'], r['stream'])}** (різниця {r['diff']:+.2f}; "
                     f"{'повні' if r['complete'] else 'неповні'} дані): " + "; ".join(f"{o} {a:.2f}" for o, a in items)
                     + ("; …" if len(r["orders"]) > 40 else ""))
    return "\n".join(L) + "\n"


def run(only_month: str = None) -> list:
    acts = collect_acts()
    cands = {"novapay": candidates_novapay(), "rozetkapay": candidates_rozetkapay(), "prom": candidates_prom()}
    cands.update(candidates_from_registry(kandydaty_registry._load_registry()))
    book = book_rows_for(sorted({a["doc_id"] for a in acts}))
    rows = build_rows(acts, cands, book, only_month)
    out_md = vch.DOCS_DIR / REPORT_NAME
    out_md.write_text(render(rows, datetime.now().strftime("%Y-%m-%d")), encoding="utf-8")
    (vch.DOCS_DIR / JSON_NAME).write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    unread = [a for a in acts if a["error"] or not a["parsed"]]
    print(f"[AktyZvirka] актів: {len(acts)} (не прочитано: {len(unread)}); рядків звіту: {len(rows)}; "
          f"⚠️ різниця: {sum(1 for r in rows if r['status'] == '⚠️ різниця')}; звіт: {out_md}")
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="лише цей місяць, YYYY-MM")
    run(ap.parse_args().month)
