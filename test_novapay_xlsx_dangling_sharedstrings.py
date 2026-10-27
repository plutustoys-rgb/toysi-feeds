#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_novapay_xlsx_dangling_sharedstrings.py — регрес: зведена виписка NovaPay, збережена LibreOffice (09.10.2026):
усі клітинки inlineStr, частини xl/sharedStrings.xml у архіві НЕМАЄ, але [Content_Types].xml і workbook.xml.rels на неї посилаються;
у заголовку нема «РЕЄСТР ПЕРЕКАЗІВ №…» (зведена за період). Раніше парсер падав («There is no item named 'xl/sharedStrings.xml'»),
і виписка щодня мовчки пропускалась у novapay_registry_kandydaty. Мережі нема. `python test_novapay_xlsx_dangling_sharedstrings.py` → exit 0/1."""
import io
import os
import sys
import zipfile

os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import novapay_statement as ns

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def _cell(ref, text):
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def build(title, with_shared_declared=True, shared_part=False):
    header = ["№", "Дата перерахунку коштів", "Номер реєстру", "Сума принятих коштів", "Сума утриманої винагороди",
              "Сума перерахованих коштів", "Сервіс", "ПІБ Покупця", "№ ЕН НП", "Номер замовлення"]
    pay = ["1", "20.07.2026 03:42", "BO012959418", "39", "0.2", "38.8", "Безготівковий 2", "Клієнт Тестовий", "20451489076273", "prom_416114712 / 10044570"]
    cols = "ABCDEFGHIJ"
    rows = [[title], [], ["Всього:", "", "", "39", "0.2", "38.8"], header, pay]
    sd = ""
    for r, vals in enumerate(rows, 1):
        cells = "".join(_cell(f"{cols[c]}{r}", v) for c, v in enumerate(vals) if v != "")
        sd += f'<row r="{r}">{cells}</row>'
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f"<sheetData>{sd}</sheetData></worksheet>")
    ct = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
          + ('<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>' if with_shared_declared else "")
          + "</Types>")
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    wb = ('<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wbrels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
              + ('<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>' if with_shared_declared else "")
              + "</Relationships>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wbrels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        if shared_part:
            z.writestr("xl/sharedStrings.xml", '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="0" uniqueCount="0"/>')
    return buf.getvalue()


unnumbered = build("Реєстр переказів з 2026-07-01 по 2026-09-30")
numbered = build("РЕЄСТР ПЕРЕКАЗІВ № 4242 від 01.10.2026")

# 1. висяче посилання на sharedStrings: нумерований реєстр читається звичайним шляхом (раніше падало)
r = ns.parse_registry_xlsx(numbered)
chk("висяче посилання на sharedStrings знімається: нумерований реєстр читається, 1 платіж", r["registry_number"] == "4242" and len(r["rows"]) == 1)
chk("платіж розібрано (внутр. номер замовлення, ТТН, сума)", r["rows"][0]["internal_order_id"] == "prom_416114712" and str(r["rows"][0]["ttn"]) == "20451489076273" and str(r["rows"][0]["amount_received"]) == "39")

# 2. VPS-поведінка за замовчуванням НЕ змінена: зведена виписка без номера відхиляється
try:
    ns.parse_registry_xlsx(unnumbered)
    chk("типово (VPS-звірка): зведена виписка без номера реєстру відхиляється", False)
except ns.NovaPayStatementError:
    chk("типово (VPS-звірка): зведена виписка без номера реєстру відхиляється", True)

# 3. локальний збирач кандидатів: allow_unnumbered=True читає зведену виписку
r2 = ns.parse_registry_xlsx(unnumbered, allow_unnumbered=True)
chk("allow_unnumbered=True: зведена виписка читається, мітка «зведена виписка», 1 платіж", r2["registry_number"] == "зведена виписка" and len(r2["rows"]) == 1)

# 4. нормальний файл (sharedStrings є в архіві) не зачеплений
ok = build("РЕЄСТР ПЕРЕКАЗІВ № 777", with_shared_declared=True, shared_part=True)
chk("файл із наявною частиною sharedStrings читається як раніше", ns.parse_registry_xlsx(ok)["registry_number"] == "777")

# 5. рядок «Всього:» не рахується платежем
chk("підсумковий рядок «Всього:» не потрапляє в платежі", all(x["raw_order_ref"].startswith("prom_") for x in r2["rows"]))

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
