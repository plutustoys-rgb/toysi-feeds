#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_prom_prosale_tier_check.py — розбір активного режиму ProSale з кнопок рядка кампанії (без мережі). `python test_prom_prosale_tier_check.py` → exit 0/1."""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import prom_prosale_tier_check as t  # noqa: E402

F = []


def chk(label, ok):
    print(("[OK ] " if ok else "[FAIL] ") + label)
    if not ok:
        F.append(label)


QAIDS = ("disable", "lessSales", "enable", "moreSales", "turbo")
INACT = "I80Um C1o-t tR5OY vFL5E R64la Fhx5R bQA1p"
ACT = "I80Um C1o-t tR5OY vFL5E R64la QSvOm bQA1p"


def row(active):
    return [(q, ACT if q == active else INACT) for q in QAIDS]


chk("активний «Звичайний» (enable) → standard (так було в кабінеті 30.10)", t.parse_active_mode(row("enable")) == "standard")
chk("активний «Економ» (lessSales) → econom", t.parse_active_mode(row("lessSales")) == "econom")
chk("активне «Вимкнено» (disable) → off", t.parse_active_mode(row("disable")) == "off")
chk("«Турбо» / «Більше продажів» розпізнаються", t.parse_active_mode(row("turbo")) == "turbo" and t.parse_active_mode(row("moreSales")) == "more_sales")
chk("жодної відмінної кнопки (усі однакові) → None, не вгадуємо", t.parse_active_mode([(q, INACT) for q in QAIDS]) is None)
chk("дві відмінні кнопки → None", t.parse_active_mode([(q, ACT if q in ("enable", "turbo") else INACT) for q in QAIDS]) is None)
chk("не 5 кнопок → None", t.parse_active_mode(row("enable")[:4]) is None and t.parse_active_mode([]) is None)
chk("Prom змінив хеші класів, але активна одна відмінна → читається далі",
    t.parse_active_mode([(q, "X1 Y2 ZZ9" if q == "lessSales" else "X1 Y2 AA7") for q in QAIDS]) == "econom")
chk("незнайомий data-qaid ігнорується (лишається <5 відомих → None)", t.parse_active_mode(row("enable")[:4] + [("other", INACT)]) is None)

# запис у канал: вставка ПЕРЕД першим «## » (не після шапки/цитат), раз на добу на тему, недоступний канал → False
tmp = Path(tempfile.mkdtemp(prefix="prosale_chan_")) / "EXECUTOR_CHANNEL.md"
tmp.write_text("# Канал\n\n> ## [не заголовок] у цитаті\n\n## [Хтось → Код] 2026-10-29 старе\nтекст\n", encoding="utf-8")
now = datetime(2026, 10, 30, 12, 0)
chk("запис створено", t.post_to_channel("розбіжність: кабінет standard, код econom", "тіло", now, tmp) is True)
c = tmp.read_text(encoding="utf-8")
chk("вставлено ПЕРЕД першим «## » і не всередину цитати", c.index("[Автомат → Код] ProSale:") < c.index("## [Хтось → Код]") and c.index("> ## [не заголовок]") < c.index("[Автомат → Код] ProSale:"))
chk("повторно того ж дня на ту саму тему — не дублюється", t.post_to_channel("розбіжність: кабінет standard, код econom", "тіло", now, tmp) is False and tmp.read_text(encoding="utf-8").count("[Автомат → Код] ProSale:") == 1)
chk("наступного дня — новий запис", t.post_to_channel("розбіжність: кабінет standard, код econom", "тіло", datetime(2026, 10, 31, 9, 0), tmp) is True)
chk("недоступний канал → False без винятку", t.post_to_channel("x", "y", now, tmp.parent / "нема" / "ch.md") is False)

print()
print("ALL OK" if not F else f"FAIL: {len(F)}")
sys.exit(1 if F else 0)
