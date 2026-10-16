#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rozetka_counts_snapshot.py — знімок лічильників кабінету Rozetka і правила застою (rozetka_counts_snapshot.py, 16.10.2026).
Інваріанти: (1) extract читає структуру /v1/goods/counts (реальна відповідь 16.10); відсутні розділи → 0; (2) record пише один рядок на добу (перезапис дня), історія відсортована; (3) stalled: черга>0 і за 3+ доби не зменшувалась → True;
зменшилась хоч раз, нуль, або історія коротша за 3 доби → False; (4) evaluate: застій і помилки>0 пишуть по одному запису [Автомат → Код] на добу (дедуп), нічого не пише у нормі; нема каналу → без винятку. `python test_rozetka_counts_snapshot.py` → exit 0/1."""
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import rozetka_counts_snapshot as rc  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


real = {"success": True, "content": {"active_items": {"sum": 2793, "tabs": {"items_on_sale_tab": 2759, "items_not_on_sale_tab": 34}},
        "inactive_items": {"sum": 7571, "tabs": {"items_errors": 0, "not_valid": 0, "not_available": 7491, "items_hidden": 80, "items_archive": 0}},
        "new_items": {"sum": 8, "tabs": {"items_new": 8}}, "on_moderation_items": {"sum": 896, "tabs": {"items_on_moderation": 896}},
        "items_blocked_by_stop_label": {"sum": 89, "tabs": {}}, "items_waiting_approve": {"sum": 844, "tabs": {"items_waiting_approve": 828}}, "all_items": {"sum": 11268, "tabs": {"items_all_tab": 11268}}}}
s = rc.extract(real)
chk("extract: реальна відповідь 16.10 (всі 11268, активні 2793, на модерації 896, очікують 844, сховані 80, помилки 0)", (s["all"], s["active"], s["on_moderation"], s["waiting_approve"], s["hidden"], s["errors"], s["not_valid"]) == (11268, 2793, 896, 844, 80, 0, 0) and rc.queue(s) == 1740)
chk("extract: порожня/зіпсована відповідь → нулі без винятку", rc.extract({}) == rc.extract(None) and rc.extract({"content": {"on_moderation_items": {"sum": "x"}}})["on_moderation"] == 0)

d = Path(tempfile.mkdtemp()) / "h.jsonl"


def snap(on=100, wa=50, err=0, nv=0):
    return {"all": 1, "active": 1, "on_moderation": on, "waiting_approve": wa, "new": 0, "hidden": 0, "errors": err, "not_valid": nv, "not_available": 0, "stop_labels": 0}


h = rc.record(snap(), date(2026, 10, 16), d)
h = rc.record(snap(on=90), date(2026, 10, 16), d)
chk("record: один рядок на добу (повторний запуск перезаписує)", len(h) == 1 and h[0]["on_moderation"] == 90 and len(rc.load_history(d)) == 1)
d2 = Path(tempfile.mkdtemp()) / "h2.jsonl"
for day, on in ((13, 900), (14, 900), (15, 905), (16, 910)):
    h = rc.record(snap(on=on), date(2026, 10, day), d2)
chk("застій: 4 знімки за 3 доби, черга 950→960 (не зменшувалась) → True", rc.stalled(h))
h2 = [dict(x) for x in h]
h2[2]["on_moderation"] = 800
chk("черга зменшилась у середині вікна → не застій (False)", not rc.stalled(h2))
chk("історія коротша за 3 доби (знімки 15 і 16) → False", not rc.stalled(h[-2:]))
h3 = [dict(x, on_moderation=0, waiting_approve=0) for x in h]
chk("черга 0 → не застій", not rc.stalled(h3))
chk("зменшується стабільно (910→…→850) → False", not rc.stalled([dict(x, on_moderation=910 - 20 * i) for i, x in enumerate(h)]))

ch = Path(tempfile.mkdtemp()) / "EXECUTOR_CHANNEL.md"
ch.write_text("# Канал\n\n## [Аудитор → Код] старе\nтекст\n", encoding="utf-8")
now = datetime(2026, 10, 16, 12, 0)
f1 = rc.evaluate(h, now, ch)
f2 = rc.evaluate(h, datetime(2026, 10, 16, 18, 0), ch)
txt = ch.read_text(encoding="utf-8")
chk("застій: один запис [Автомат → Код] на добу (другий запуск дедуплікується)", f1 == ["застій"] and f2 == ["застій (запис за сьогодні вже є)"] and txt.count(rc.MARK) == 1 and txt.index(rc.MARK) < txt.index("## [Аудитор → Код] старе"))
hn = [dict(x, on_moderation=100 - 30 * i, waiting_approve=0) for i, x in enumerate(h)]
ch2 = Path(tempfile.mkdtemp()) / "c.md"
ch2.write_text("## x\n", encoding="utf-8")
chk("норма (черга зменшується, помилок 0) → нічого не пишемо", rc.evaluate(hn, now, ch2) == [] and rc.MARK not in ch2.read_text(encoding="utf-8"))
he = [dict(hn[-1], errors=3)]
chk("помилки > 0 → запис про помилки", rc.evaluate(he, now, ch2) == ["помилки"] and "помилки/невалідні > 0" in ch2.read_text(encoding="utf-8"))
chk("нема каналу → без винятку", rc.evaluate(he, now, Path(tempfile.mkdtemp()) / "нема.md") == ["помилки (запис за сьогодні вже є)"])
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
