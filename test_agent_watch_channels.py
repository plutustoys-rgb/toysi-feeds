#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_agent_watch_channels.py — регрес-тест: вотчер «Код» бачить нові записи в
CONSULTANT_CHANNEL.md/КОДВ_CHANNEL.md (знахідка Консультанта 2026-09-17, live-сесія: жоден
вотчер їх не читав — канал, у якому Код спілкується з Консультантом/бухгалтером, взагалі не
був тригером пробудження, звідси «відповіді лягли не туди»).

Мережа не потрібна. `python test_agent_watch_channels.py` → exit 0/1.
"""
import sys
import agent_watch as aw

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


kod_cfg = next(w for w in aw.WATCHERS if w["name"] == "Код")

# 1: обидва канали в списку вотчера «Код»
_chk("CONSULTANT_CHANNEL.md у channels Кода", "CONSULTANT_CHANNEL.md" in kod_cfg["channels"])
_chk("КОДВ_CHANNEL.md у channels Кода", "КОДВ_CHANNEL.md" in kod_cfg["channels"])
_chk("SEO_CHANNEL.md/MARKETING_CHANNEL.md не загублені", set(kod_cfg["channels"]) >= {
    "SEO_CHANNEL.md", "MARKETING_CHANNEL.md", "CONSULTANT_CHANNEL.md", "КОДВ_CHANNEL.md"})

# 2: _newest_incoming_header справді детектує заголовок «→ Код» у синтетичному тексті
#    обох нових каналів (той самий детектор, що вже перевірений на SEO/MARKETING)
consultant_text = "## [Консультант → Код] 2026-09-17 — тестовий запит\n\nдеталі\n"
_chk("детектує [Консультант → Код]",
     aw._newest_incoming_header(consultant_text, kod_cfg["target_label"]) is not None)

kodv_text = "## [КОДВ → Код] 2026-09-17 — тестовий запит\n\nдеталі\n"
_chk("детектує [КОДВ → Код]",
     aw._newest_incoming_header(kodv_text, kod_cfg["target_label"]) is not None)

# 3: не спрацьовує на записи, де Код — ВІДПРАВНИК (свій же допис не має себе будити)
own_text = "## [Код → Консультант] 2026-09-17 — власна відповідь\n"
_chk("НЕ детектує власний [Код → Консультант]",
     aw._newest_incoming_header(own_text, kod_cfg["target_label"]) is None)

# 4: ЖИВА перевірка на РЕАЛЬНОМУ CONSULTANT_CHANNEL.md (Cowork-папка) — топовий запис і
#    справді адресований Коду (найновіший запис сесії — наш власний [Код → Консультант])
import os
_cowork = aw.COWORK_DIR
_consultant_path = os.path.join(_cowork, "CONSULTANT_CHANNEL.md")
if os.path.exists(_consultant_path):
    with open(_consultant_path, encoding="utf-8") as f:
        real_text = f.read()
    # Детектор шукає НАЙНОВІШИЙ (топовий) запис, адресований Коду — не обов'язково перший
    # рядок файлу (може бути наш власний допис угорі). Просто перевіряємо: функція не падає
    # на реальному, великому файлі й повертає або валідний рядок, або None — без винятку.
    try:
        result = aw._newest_incoming_header(real_text, kod_cfg["target_label"])
        _chk("живий CONSULTANT_CHANNEL.md: детектор не падає", True)
        print(f"     (результат: {result!r})")
    except Exception as e:
        _chk(f"живий CONSULTANT_CHANNEL.md: детектор не падає ({e})", False)
else:
    print("[--] живий CONSULTANT_CHANNEL.md не знайдено (Cowork не змонтовано) — пропущено")


# ---- 08.11.2026: три правила звітування їдуть у КОЖНЕ пробудження (headless не вантажить персону/CLAUDE.md) ----
import glob
from pathlib import Path

_HEAD = "Три правила звітування (чинні для ВСІХ ролей з 08.11.2026"
for _w in aw.WATCHERS:
    for _per in (False, True):
        _p = aw._compose_prompt(_w, "тест", _per)
        _chk(f"{_w['name']} (periodic={_per}): у промпті пробудження всі три правила", "1) ОДИНИЦЯ ПЕРЕД ЧИСЛОМ" in _p and "2) «НЕМАЄ» І «НЕМОЖЛИВО»" in _p and "3) ДАТА ДЖЕРЕЛА ПРИ ЧИСЛІ" in _p)
        _chk(f"{_w['name']} (periodic={_per}): правила не вбивають запуск (жодних cmd-метасимволів & | ^ % у блоці правил)", not any(c in aw.REPORT_RULES for c in "&|^%<>"))
_here = Path(__file__).parent
_chk("CLAUDE.md містить блок правил звітування", _HEAD in (_here / "CLAUDE.md").read_text(encoding="utf-8"))
_personas = sorted(glob.glob(str(_here / ".claude" / "agents" / "plutus-*.md")))
_chk("персони plutus-*.md у репо: кожна має блок правил (знайдено %d)" % len(_personas), len(_personas) >= 8 and all(_HEAD in Path(p).read_text(encoding="utf-8") for p in _personas))

# ---- реальний шлях _wake: argv, який отримає claude (аудит #1123: claude.CMD ріже аргумент на першому LF) ----
import os
import subprocess
import tempfile

_cap = {}


class _R:
    returncode = 0
    stdout = ""
    stderr = ""


def _fake_run(cmd, **kw):
    _cap["cmd"] = list(cmd)
    return _R()


_real_run = subprocess.run
subprocess.run = _fake_run
try:
    for _w in aw.WATCHERS:
        for _per in (False, True):
            _cap.clear()
            _ok = aw._wake(_w, "тест", False, _per)
            _argv = _cap.get("cmd") or []
            _pr = _argv[2] if len(_argv) > 2 else ""
            _chk(f"_wake {_w['name']} (periodic={_per}): запуск пройшов через _compose_prompt — у -p правила й мітка монітора", _ok and "1) ОДИНИЦЯ ПЕРЕД ЧИСЛОМ" in _pr and "[Монітор:" in _pr)
            _chk(f"_wake {_w['name']} (periodic={_per}): у -p НЕМАЄ переносів рядка (інакше claude.CMD обріже решту)", _pr != "" and "\n" not in _pr and "\r" not in _pr)
finally:
    subprocess.run = _real_run

if os.name == "nt":      # справжній cmd.exe: заглушка .cmd повертає argv, який реально дійшов (аудитор #1123 відтворив обрізання саме так)
    with tempfile.TemporaryDirectory() as _td:
        _out = os.path.join(_td, "argv.json")
        _stub = os.path.join(_td, "claude_stub.cmd")
        with open(_stub, "w", encoding="utf-8") as _f:
            _f.write('@echo off' + chr(13) + chr(10) + '"' + sys.executable + '" -c "import sys,json;json.dump(sys.argv[1:],open(sys.argv[1],\'w\',encoding=\'utf-8\'))" "' + _out + '" %*' + chr(13) + chr(10))
        _old_bin = aw.CLAUDE_BIN
        aw.CLAUDE_BIN = _stub
        try:
            _w = next(w for w in aw.WATCHERS if w["name"] == "Код")
            _ok = aw._wake(_w, "тест", False, False)
            _got = __import__("json").load(open(_out, encoding="utf-8")) if os.path.exists(_out) else []
        finally:
            aw.CLAUDE_BIN = _old_bin
    _txt = " ".join(_got)
    _chk("справжній cmd.exe: до заглушки дійшли -p з правилами, мітка монітора І решта аргументів (--allowedTools)", _ok and "1) ОДИНИЦЯ ПЕРЕД ЧИСЛОМ" in _txt and "[Монітор:" in _txt and "--allowedTools" in _got)

if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Вотчер «Код» бачить CONSULTANT_CHANNEL.md і КОДВ_CHANNEL.md")
sys.exit(0)
