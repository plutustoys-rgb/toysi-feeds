#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_gmc_compare_and_delivery.py — порівняння GMC-звіту з попереднім днем + прапорці (gmc_status_report.compare_with_previous), діф налаштувань (gmc_account_settings.diff_settings) і доставка файлів SEO (daily_files_to_shared).
Інваріанти: (1) перший запуск — «попереднього знімка нема», прапорців нема; (2) approved впав → 🚩; approved виріс — прапорця нема; (3) НОВА причина (раніше в жодному знімку) → 🚩, відома причина — ні; історія кодів gmc_seen_codes.json оновлюється;
(4) порівняння не ламає запис звіту (summary містить блок «Порівняння»); (5) diff_settings: змінений/новий/зниклий розділ; однакові → []; (6) latest_per_pattern бере найновіший файл кожного шаблону; flags_in_summary читає лише рядки 🚩; post_flag — один запис на дату, перед першим рядком «## », блок-цитата ціла.
`python test_gmc_compare_and_delivery.py` → exit 0/1."""
import csv
import json
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import daily_files_to_shared as dl  # noqa: E402
import gmc_account_settings as gs  # noqa: E402
import gmc_status_report as g  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def row(oid, status, codes=""):
    return {"date": "x", "offerId": oid, "contentLanguage": "uk", "feedLabel": "UA", "dataSource": "1", "status": status, "issue_codes": codes, "issue_severities": "", "issue_descriptions": "", "disapproved_countries": "", "approved_countries": "", "pending_countries": ""}


def write_csv(d, day, rows):
    with open(d / f"gmc_status_{day}.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=g.CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)


d = Path(tempfile.mkdtemp())
cur = [row("1", "approved"), row("2", "disapproved", "a | b")]
lines, flags = g.compare_with_previous(cur, d, "2026-10-16")
chk("перший запуск: «попереднього знімка нема», прапорців нема, історія кодів створена", "нема" in lines[0] and flags == [] and set(json.loads((d / "gmc_seen_codes.json").read_text(encoding="utf-8"))) == {"a", "b"})
write_csv(d, "2026-10-16", cur)
nxt = [row("1", "approved"), row("2", "approved"), row("3", "disapproved", "a")]
lines, flags = g.compare_with_previous(nxt, d, "2026-10-17")
chk("approved 1→2 виріс, причина a відома → прапорців нема; у рядках порівняння є числа", flags == [] and any("approved 1→2 (+1)" in x for x in lines))
write_csv(d, "2026-10-17", nxt)
drop = [row("1", "disapproved", "a"), row("2", "disapproved", "a | c")]
lines, flags = g.compare_with_previous(drop, d, "2026-10-18")
chk("approved впав 2→0 → 🚩; НОВА причина c → 🚩; відома a — ні", len(flags) == 2 and "approved ВПАВ: 2 → 0" in flags[0] and "c (1)" in flags[1] and "a (" not in flags[1])
chk("історія кодів дописана новою причиною c", "c" in json.loads((d / "gmc_seen_codes.json").read_text(encoding="utf-8")))
write_csv(d, "2026-10-18", drop)
lines, flags = g.compare_with_previous(drop, d, "2026-10-19")
chk("ті самі дані наступного дня: нічого нового, прапорців нема", flags == [])
d2 = Path(tempfile.mkdtemp())
g.write_report(cur, d2, "2026-10-16")
g.write_report(nxt, d2, "2026-10-17")
t = (d2 / "gmc_summary_2026-10-17.txt").read_text(encoding="utf-8")
chk("write_report: у summary є блок «Порівняння з 2026-10-16»", "Порівняння з 2026-10-16" in t and "Прапорців нема" in t)

old = {"programs": {"status": 200, "data": {"x": 1}}, "homepage": {"status": 200, "data": {"claimed": True}}, "gone": {"status": 200, "data": {"a": 1}}}
new = {"programs": {"status": 200, "data": {"x": 2}}, "homepage": {"status": 200, "data": {"claimed": True}}, "gone": {"status": 403, "error": "q"}, "fresh": {"status": 200, "data": {}}}
dd = gs.diff_settings(old, new)
chk("diff_settings: programs змінився, gone перестав читатися, fresh з'явився; homepage без змін", dd == ["fresh: з'явився", "gone: перестав читатися (код 403)", "programs: змінився"] and gs.diff_settings(old, old) == [])

names = ["gmc_status_2026-10-15.csv", "gmc_status_2026-10-16.csv", "gmc_summary_2026-10-16.txt", "googlebot_daily_2026-10-14.csv", "gsc_daily_2026-10-16.csv", "gmc_seen_codes.json", "other.txt", "eva_full_import_2026-10-16.md", "eva_full_import_2026-10-17.md"]
got = dl.latest_per_pattern(names)
chk("latest_per_pattern: найновіший кожного шаблону, службові/чужі файли ігноруються", sorted(got) == ["gmc_status_2026-10-16.csv", "gmc_summary_2026-10-16.txt", "googlebot_daily_2026-10-14.csv", "gsc_daily_2026-10-16.csv", "eva_full_import_2026-10-17.md"])
chk("flags_in_summary: лише рядки з 🚩", dl.flags_in_summary("a\n  🚩 approved ВПАВ\nПРАПОРЦІ:\n  🚩 НОВА причина x\nпросто текст") == ["🚩 approved ВПАВ", "🚩 НОВА причина x"] and dl.flags_in_summary("Прапорців нема") == [])
ch = Path(tempfile.mkdtemp()) / "SEO_CHANNEL.md"
ch.write_text("<!-- шапка -->" + chr(10) + "> ## 📇 адреси" + chr(10) + ">" + chr(10) + chr(10) + "## [SEO → Код] старе" + chr(10) + "текст" + chr(10), encoding="utf-8")
a1 = dl.post_flag(["🚩 x"], "2026-10-17", ch)
a2 = dl.post_flag(["🚩 x"], "2026-10-17", ch)
a3 = dl.post_flag(["🚩 y"], "2026-10-18", ch)
tt = ch.read_text(encoding="utf-8")
chk("post_flag: один запис на дату, нова дата — новий; перед першим «## »; блок-цитата ціла", a1 and not a2 and a3 and tt.count(dl.MARK) == 2 and "> ## 📇 адреси" in tt and tt.index(dl.MARK) < tt.index("## [SEO → Код] старе") and "> ## [Автомат" not in tt)
chk("нема каналу → False без винятку", dl.post_flag(["x"], "2026-10-17", Path(tempfile.mkdtemp()) / "нема.md") is False)
# правки аудиту #825: повторний запуск того ж дня не стирає «нову причину»; .tmp і чужі імена не беруться
d3 = Path(tempfile.mkdtemp())
write_csv(d3, "2026-10-16", [row("1", "approved", "a")])
l1, f1 = g.compare_with_previous([row("1", "approved", "a"), row("2", "disapproved", "z")], d3, "2026-10-17")
l2, f2 = g.compare_with_previous([row("1", "approved", "a"), row("2", "disapproved", "z")], d3, "2026-10-17")
chk("повторний запуск того ж дня: прапорець «НОВА причина z» лишається (first_seen == сьогодні)", any("z (1)" in x for x in f1) and any("z (1)" in x for x in f2))
l3, f3 = g.compare_with_previous([row("1", "approved", "a"), row("2", "disapproved", "z")], d3, "2026-10-18") if (write_csv(d3, "2026-10-17", [row("1", "approved", "a"), row("2", "disapproved", "z")]) or True) else None
chk("наступного дня z уже не нова", not any("z (" in x for x in f3))
chk("latest_per_pattern: .csv.tmp, пробіли і «;» в імені ігноруються", dl.latest_per_pattern(["gmc_status_2026-10-18.csv.tmp", "gmc_status_2026-10-16.csv", "gmc_summary_2026-10-17.txt x y", "gmc_status_2026-10-19.csv; touch x"]) == ["gmc_status_2026-10-16.csv"])
l4, f4 = g.compare_with_previous([row("1", "disapproved", "a")], d3, "2026-10-19")
chk("прапорець «approved ВПАВ» містить загальну кількість і disapproved/unknown", any("усього товарів" in x and "disapproved" in x for x in f4))
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
