#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_eva_category_check.py — щоденна звірка категорій кабінету EVA з фідом (eva_category_check.py, 16.10.2026).
Інваріанти: (1) parse_rows читає рядки таблиці кабінету (id мерчанта → назва, статус); (2) analyze рахує available-товари фіду в НЕСПІВСТАВЛЕНИХ категоріях і окремо в невідомих кабінету; не-available не рахуються;
(3) post_to_channel додає ОДИН запис «[Автомат → Код]» на добу над першим розділом (дедуп за датою), нічого не пише, якщо каналу нема; (4) звіт містить число. `python test_eva_category_check.py` → exit 0/1."""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import eva_category_check as ec  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


page = "ID КАТЕГОРІЇ\tНАЗВА\tСТАТУС\n\n 99256 \n ID: 140091 \n \t3d друк\tНова\n\n 100344 \n ID: 140254 \n \tБрелоки\tНова\n"
rows = ec.parse_rows(page)
chk("parse_rows: 2 рядки, id → (назва, статус)", rows == {"99256": ("3d друк", "Нова"), "100344": ("Брелоки", "Нова")})
feed = """<yml_catalog><shop><offers>
<offer id="1" available="true"><categoryId>100344</categoryId></offer>
<offer id="2" available="true"><categoryId>100344</categoryId></offer>
<offer id="3" available="false"><categoryId>100344</categoryId></offer>
<offer id="4" available="true"><categoryId>200</categoryId></offer>
<offer id="5" available="true"><categoryId>300</categoryId></offer>
</offers></shop></yml_catalog>"""
res = ec.analyze({"100344": ("Брелоки", "Нова"), "99256": ("3d друк", "Нова")}, {"200": ("Пазли", "Доступна")}, feed)
chk("analyze: 2 available у незіставленій 100344 (не-available не рахується), 1 у співставленій, 1 у невідомій кабінету", res["bad_offers"] == 2 and res["bad_categories"] == 1 and res["unknown_offers"] == 1 and res["feed_available"] == 4)
chk("топ: (2, '100344', 'Брелоки')", res["top"] == [(2, "100344", "Брелоки")])
chk("звіт містить число 2 і назву", "2 товарів" in ec.render_report(res, datetime(2026, 10, 16, 12, 0)) and "Брелоки" in ec.render_report(res, datetime(2026, 10, 16, 12, 0)))
ch = Path(tempfile.mkdtemp()) / "EXECUTOR_CHANNEL.md"
ch.write_text("# Канал\n\n## [Аудитор → Код] старе\nтекст\n", encoding="utf-8")
now = datetime(2026, 10, 16, 12, 0)
a1 = ec.post_to_channel(res, now, ch)
a2 = ec.post_to_channel(res, datetime(2026, 10, 16, 18, 0), ch)
txt = ch.read_text(encoding="utf-8")
chk("запис додано один раз за добу (другий виклик того ж дня — ні)", a1 and not a2 and txt.count(ec.MARK) == 1)
chk("запис над першим розділом, старе збережено", txt.index(ec.MARK) < txt.index("## [Аудитор → Код] старе") and "текст" in txt)
a3 = ec.post_to_channel(res, datetime(2026, 10, 17, 12, 0), ch)
chk("наступної доби — новий запис", a3 and ch.read_text(encoding="utf-8").count(ec.MARK) == 2)
chk("каналу нема → False без винятку", ec.post_to_channel(res, now, Path(tempfile.mkdtemp()) / "нема.md") is False)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
