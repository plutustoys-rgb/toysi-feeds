# -*- coding: utf-8 -*-
"""SMM P2 (2026-10-06): trust-рядок «Фіскальний чек» + клікабельний телефон на картці товару; маскот більший на десктопі."""
import sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
H = Path(__file__).parent
F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)
src = (H / "site" / "build_site.py").read_text(encoding="utf-8")
css = (H / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
chk("картка товару: «✓ Фіскальний чек» у trust-рядку", '<span class="tb">✓ Фіскальний чек</span>' in src)
chk("картка товару: клікабельний tel: біля кнопки", 'class="ask">Є питання? <a href="tel:+380730150815">' in src)
chk("CSS: .ask і десктопний маскот (min-width:900px)", ".ask{" in css and "@media(min-width:900px){.hero .mascot{height:120px}" in css)
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ SMM P2 — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
