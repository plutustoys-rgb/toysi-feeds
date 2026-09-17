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


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Вотчер «Код» бачить CONSULTANT_CHANNEL.md і КОДВ_CHANNEL.md")
sys.exit(0)
