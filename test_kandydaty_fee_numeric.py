#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Регрес (запит головного бухгалтера, КОДВ_CHANNEL 2026-10-05): суми комісії/винагороди в кандидатах NovaPay/RozetkaPay
більше не губляться — `fee` зберігається ЧИСЛОМ окремим полем реєстру, а summary не ріжеться посеред числа
(було `note[:120]` → «винагорода НП 0.92» ставало «0.»). Файл реєстру тимчасовий, мережі нема."""
import sys
import tempfile
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import kandydaty_registry as kr
import novapay_registry_kandydaty as nr
import rozetkapay_registry_kandydaty as rr

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

kr.REGISTRY_PATH = Path(tempfile.mkdtemp()) / "reg.json"

# 1. compact_summary: ніколи не ріже посеред фрагмента, числові (перші) фрагменти завжди живі
parts = ["COD НЕ в книзі, ТТН 20451234567890", "прийнято 1250.00", "винагорода НП 0.92", "зараховано 1249.08",
         "зам. 100453105 (prom)", "дата 2026-10-02", "дуже довгий хвіст " * 20]
s = kr.compact_summary(parts, limit=160)
chk("винагорода НП 0.92 ціла в summary", "винагорода НП 0.92" in s)
chk("довгий хвіст відкинуто цілим фрагментом, не обрізано", "дуже довгий хвіст" not in s and len(s) <= 160)
chk("порожні фрагменти пропускаються", kr.compact_summary(["", "a", None, "b"]) == "a | b")
chk("перший фрагмент довший за ліміт усе одно лишається цілим", kr.compact_summary(["x" * 300, "y"], 100) == "x" * 300)

# 2. NovaPay: fee окремим полем + число в summary
cands = [{"ttn": "20451234567890", "sum": 1250.0, "fee": 0.92, "net": 1249.08, "date": "2026-10-02", "order_id": "100453105",
          "platform": "prom", "note": "x" * 400}]
nr.sync_registry(cands)
reg = kr._load_registry()
e = reg["novapay_registry:20451234567890"]
chk("NovaPay: fee=0.92 числом у реєстрі", e.get("fee") == 0.92)
chk("NovaPay: summary містить «винагорода НП 0.92» і не обрізаний на 120", "винагорода НП 0.92" in e["summary"])
cands[0]["fee"] = 1.90   # повторний sync оновлює fee в наявному open-записі
nr.sync_registry(cands)
chk("NovaPay: повторний sync оновлює fee", kr._load_registry()["novapay_registry:20451234567890"].get("fee") == 1.90)

# 3. RozetkaPay: eквайринг і сторно
rcands = [
    {"kind": "acquiring", "order_id": "905000001", "sum": 3600.0, "acquiring": 54.05, "date": "2026-09-30", "in_book": True,
     "book_row": 120, "book_current_i9": 40.0, "note": "n" * 400},
    {"kind": "storno", "order_id": "905000002", "sum": -481.0, "commission_returned": 12.34, "date": "2026-09-08",
     "book_row": 56, "note": "n" * 400},
]
rr.sync_registry(rcands)
reg = kr._load_registry()
a = reg["rozetkapay_registry:905000001:acquiring"]; st = reg["rozetkapay_registry:905000002:storno"]
chk("RozetkaPay: еквайринг 54.05 числом у fee і в summary", a.get("fee") == 54.05 and "54.05" in a["summary"])
chk("RozetkaPay: сторно — комісія повернена 12.34 у fee і в summary", st.get("fee") == 12.34 and "12.34" in st["summary"])
chk("RozetkaPay: sum сторно додатний (як і раніше)", st.get("sum") == 481.0)

# 4. звіт показує колонку «Комісія»
out = Path(tempfile.mkdtemp()) / "r.md"
kr.write_open_report(out_path=out)
txt = out.read_text(encoding="utf-8")
chk("звіт має колонку «Комісія» і значення 0.92/1.9", "| Комісія |" in txt and "1.9" in txt)
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ комісії кандидатів зберігаються числом, summary не ріжеться — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
