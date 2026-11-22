#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_support_relay_watch.py — «пейджер» чату підтримки (support_relay_watch.py), 22.11.2026.
ІНВАРІАНТИ: (1) вивід `list` розбирається у (id, ts, chat, text), сторонні рядки ігноруються; (2) КОЖНЕ нове повідомлення друкується рівно ОДИН раз окремою подією, уже бачені — ніколи; перший прохід видає поточну чергу;
(3) збій каналу друкується ОДИН раз на серію збоїв, відновлення — один раз; (4) скрипт нічого не пише на VPS (лише `list`).
Без мережі. `python test_support_relay_watch.py` → exit 0/1."""
import sys
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import support_relay_watch as pw  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


OUT1 = "#2 2026-11-22T12:55:05 чат 6805880186: Доброго дня, скиньте фото\n#3 2026-11-22T12:57:00 чат 6805880186: Що там ?\n(черга порожня: на всі вхідні вже відповіла людина)\nсміття"
rows = pw.parse_list(OUT1)
chk("parse_list: 2 повідомлення, сторонні рядки ігноруються", rows == [(2, "2026-11-22T12:55:05", 6805880186, "Доброго дня, скиньте фото"), (3, "2026-11-22T12:57:00", 6805880186, "Що там ?")])
chk("parse_list: порожній/None → []", pw.parse_list("") == [] and pw.parse_list(None) == [])
events = []
seen, state = set(), {}
fetch_out = {"rc": 0, "out": OUT1}
fetch = lambda: (fetch_out["rc"], fetch_out["out"])
n = pw.step(seen, state, fetch, events.append)
chk("перший прохід: обидва повідомлення, формат події, кожне окремим рядком", n == 2 and len(events) == 2 and events[0].startswith("НОВЕ ПОВІДОМЛЕННЯ КЛІЄНТА #2 ") and "Що там ?" in events[1])
n = pw.step(seen, state, fetch, events.append)
chk("другий прохід без змін: НІЧОГО нового (жодних дублів)", n == 0 and len(events) == 2)
fetch_out["out"] = OUT1 + "\n#4 2026-11-22T13:10:00 чат 111: Нове питання"
n = pw.step(seen, state, fetch, events.append)
chk("нове повідомлення → рівно одна нова подія", n == 1 and len(events) == 3 and "#4" in events[2] and "Нове питання" in events[2])
fetch_out["out"] = "(черга порожня: на всі вхідні вже відповіла людина)"
pw.step(seen, state, fetch, events.append)
fetch_out["out"] = OUT1
n = pw.step(seen, state, fetch, events.append)
chk("повідомлення зникло з черги й повернулось (той самий id) → НЕ повторюється", n == 0 and len(events) == 3)
fetch_out["rc"], fetch_out["out"] = 1, ""
pw.step(seen, state, fetch, events.append)
pw.step(seen, state, fetch, events.append)
pw.step(seen, state, fetch, events.append)
down = [e for e in events if "НЕДОСТУПНИЙ" in e]
chk("збій каналу x3 поспіль → ОДНЕ повідомлення про збій", len(down) == 1)
fetch_out["rc"], fetch_out["out"] = 0, OUT1 + "\n#5 2026-11-22T13:30:00 чат 222: Після збою"
pw.step(seen, state, fetch, events.append)
chk("відновлення: одне «відновився» і нове повідомлення після збою не втрачене", sum("відновився" in e for e in events) == 1 and any("#5" in e and "Після збою" in e for e in events))
src = (HERE / "support_relay_watch.py").read_text(encoding="utf-8")
chk("скрипт викликає лише `list` (жодного reply/digest/запису у коді)", 'run_remote("list")' in src and "run_remote(" in src and src.count("run_remote(") == 1)
print("\nПідсумок:", "УСЕ ЗЕЛЕНЕ" if not F else f"ПРОВАЛИ: {F}")
sys.exit(1 if F else 0)
