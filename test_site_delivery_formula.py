#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_delivery_formula.py — 12.10.2026: вартість доставки з формулою (Код, getDocumentPrice із акаунту НП).
Комісія НП за накладений платіж = 20 ₴ + 2% від суми (точно на 190/400/1000/3000); тариф 70 ₴ Київ / 90 ₴ інші великі міста (оголошена вартість 500).
Перевіряємо: (1) deliveryEstimate з app.js (node) дає 70+20+2%…90+20+2% і НЕ друкує комісію понад стелю накладеного платежу 3000;
(2) сторінка «Доставка» й кошик друкують ФОРМУЛУ, а не діапазон на око; (3) немає обіцянки, що комісію платить одержувач, і немає безкоштовної доставки;
(4) вікно цін у JS = константи сервера (SITE_COD_CEILING)."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
for k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID"):
    os.environ.pop(k, None)
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

FAILS = []


def chk(name, ok):
    print(("[OK]   " if ok else "[FAIL] ") + name)
    if not ok:
        FAILS.append(name)


app = (HERE / "site" / "assets" / "app.js").read_text(encoding="utf-8")
m = re.search(r"(var DELIV_TARIFF_MIN=.*?function deliveryEstimate\(goods, prepaid, parcels\)\{.*?\n  \})", app, re.S)
chk("app.js: блок deliveryEstimate знайдено", bool(m))

if m:
    code = m.group(1) + "\nconsole.log(JSON.stringify([0,1,50,190,200,210,250,400,500,501,704,1000,3000,3001,5000,-5,NaN].map(function(g){return [g,deliveryEstimate(g)];})));"
    try:
        out = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30)
        res = dict((str(k), v) for k, v in json.loads(out.stdout))
    except Exception as e:  # noqa: BLE001
        res = {}
        chk(f"node недоступний/зламався ({e}) — перевірка поведінки неможлива", False)
    if res:
        chk("190 ₴ → 70+20+3,8 … 90+20+3,8 = 94–114", res.get("190") == "орієнтовно 94–114 ₴")
        chk("200 ₴ → 94–114 (приклад на сторінці «Доставка»)", res.get("200") == "орієнтовно 94–114 ₴")
        chk("210 ₴ → 94–114 (94,2 округлюється вниз; Math.ceil дав би 95 — аудит #699 F7)", res.get("210") == "орієнтовно 94–114 ₴")
        chk("250 ₴ → 95–115 (рівно 25 ₴ комісії)", res.get("250") == "орієнтовно 95–115 ₴")
        chk("400 ₴ → 98–118", res.get("400") == "орієнтовно 98–118 ₴")
        chk("500 ₴ → 100–120 (оголошена 500 = мінімум, надбавки тарифу ще нема)", res.get("500") == "орієнтовно 100–120 ₴")
        chk("501 ₴ → 103–123 (тариф +0,5% × S лише понад 500)", res.get("501") == "орієнтовно 103–123 ₴")
        chk("704 ₴ (реальна ТТН Коду: 70+3,52 + комісія 34,08 = 107,6) → 108–128", res.get("704") == "орієнтовно 108–128 ₴")
        chk("1000 ₴ → 115–135 (Код: Київ 115 / інші 135; верх діапазону «до 1 000 ₴ ≈ 90–135»)", res.get("1000") == "орієнтовно 115–135 ₴")
        chk("3000 ₴ (стеля накладеного платежу) → 165–185 (комісія 80 ₴ + 0,5% × 3000 = 15)", res.get("3000") == "орієнтовно 165–185 ₴")
        chk("понад стелю 3000 накладеного платежу немає → лише тариф, без комісії", res.get("3001") == "орієнтовно 70–90 ₴ до 2 кг" and res.get("5000") == "орієнтовно 70–90 ₴ до 2 кг")
        chk("порожній/негативний/NaN кошик → безпечний тариф, не NaN", all(res.get(k) == "орієнтовно 70–90 ₴ до 2 кг" for k in ("0", "-5", "None")) and "NaN" not in json.dumps(res, ensure_ascii=False))  # JSON.stringify(NaN) = null → "None"
        chk("малий кошик (50 ₴) → нижня межа 91 ₴ (70+20+1)", res.get("50") == "орієнтовно 91–111 ₴")

chk("стеля JS = стеля сервера (SITE_COD_CEILING=3000)", "DELIV_COD_MAX=3000" in app and re.search(r"SITE_COD_CEILING[^\n]*3000", (HERE / "site_order_api.py").read_text(encoding="utf-8")) is not None)

dl = bs._DELIVERY
chk("«Доставка»: формула «20 ₴ + 2% від суми замовлення» і підсумок «до 1 000 ₴ — орієнтовно 90–130 ₴»", "<b>20 ₴ + 2% від суми замовлення</b>" in dl and "до 1 000 ₴ разом це <b>орієнтовно 90–135 ₴</b>" in dl and "понад 500 ₴ тариф трохи зростає" in dl and "Точну суму назве Нова Пошта при отриманні" in dl)
chk("«Доставка»: приклад 200 ₴ узгоджений із формулою (70–90 + 24 = 94–114)", "замовлення на 200 ₴: 70–90 ₴ + 24 ₴ = 94–114 ₴" in dl)
chk("«Доставка»: НЕ стверджує, що комісію платить одержувач (платник комісії не доведений) — лише «доставку оплачує одержувач при отриманні»", "комісію оплачує" not in dl and "комісію платить" not in dl and "Доставку оплачує одержувач при отриманні посилки" in dl)
chk("є застереження: важчі посилки без суми; тариф залежить від міста й оголошеної вартості", "Для посилок важчих за 2 кг сума вища" in dl and "залежить від міста та оголошеної вартості" in dl)
chk("картка товару не суперечить кошику: поряд із тарифом є комісія НП «20 ₴ + 2% від суми замовлення» (аудит #699 F3)", "при накладеному платежі додається комісія НП: ' + DELIVERY_FEE_FORMULA_SHORT" in (HERE / "site" / "build_site.py").read_text(encoding="utf-8") and bs.DELIVERY_FEE_FORMULA_SHORT == "20 ₴ + 2% від суми замовлення")
chk("безкоштовної доставки не обіцяємо", "безкоштовн" not in dl.lower() and "безкоштовн" not in bs.DELIVERY_NOTE.lower())
chk("DELIVERY_NOTE містить формулу і «точну покаже Нова Пошта»", "20 ₴ + 2% від суми замовлення" in bs.DELIVERY_NOTE and "точну суму назве Нова Пошта при отриманні" in bs.DELIVERY_NOTE and "для замовлень понад 500 ₴ тариф трохи зростає" in bs.DELIVERY_NOTE and "(0,5% від суми замовлення)" in dl)

print("\nУСЕ OK" if not FAILS else f"\nFAILED ({len(FAILS)}):")
for f in FAILS:
    print("  -", f)
sys.exit(1 if FAILS else 0)
