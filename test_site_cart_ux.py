#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_cart_ux.py — кошик: «−» при кількості 1 НЕ видаляє товар (видалення лише явним «Прибрати»), цілі дотику ≥44 px (зауваження Тестувальника 10.10).
Статична перевірка app.js/styles.css + `node` для логіки dec (якщо node є). Самодостатній."""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


js = open(os.path.join(HERE, "site", "assets", "app.js"), encoding="utf-8").read()
css = open(os.path.join(HERE, "site", "assets", "styles.css"), encoding="utf-8").read()
chk("dec: зменшує лише коли кількість >1", 'else if(act==="dec"){ var cur=c[id]?c[id].qty:0; if(cur>1) PT.setQty(id, cur-1); }' in js)
chk("кнопка «−» disabled при кількості 1 і має aria-label", "(it.qty<=1?' disabled':'')" in js and 'aria-label="Менше"' in js and 'aria-label="Більше"' in js)
chk("«Прибрати» лишається явним видаленням", 'data-act="rm"' in js and 'else if(act==="rm") PT.remove(id);' in js)
m = re.search(r"\.qty button\{width:(\d+)px;height:(\d+)px", css)
chk("«−»/«+» ≥44×44 px", m is not None and int(m.group(1)) >= 44 and int(m.group(2)) >= 44)
chk("«Прибрати» має min-height ≥44 px", re.search(r"\.ci-rm\{[^}]*min-height:(\d+)px", css) is not None and int(re.search(r"\.ci-rm\{[^}]*min-height:(\d+)px", css).group(1)) >= 44)
chk(".qty button:disabled стилізовано", ".qty button:disabled{" in css)
chk("node --check app.js", shutil.which("node") is None or subprocess.run(["node", "--check", os.path.join(HERE, "site", "assets", "app.js")]).returncode == 0)
print()
if F:
    print("FAILED:", F)
    sys.exit(1)
print("ALL OK")
