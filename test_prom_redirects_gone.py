#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_prom_redirects_gone.py — 11.10.2026: мапа старих Prom-URL тепер керує і 410 (Apache віддає 410 Gone для КОЖНОГО prom_id, якого в мапі нема).
Інваріанти generate_prom_redirects.py:
  • у мапу потрапляють товари з карткою в index.json І товари з живою сторінкою «без залишку» (файл product-<id>.html у теці сайту) — інакше Prom-URL товару,
    що тимчасово закінчився, віддавав би 410 замість 301 на його сторінку;
  • Prom-ID, чий товар зник зовсім (нема ні в індексі, ні файлу сторінки), у мапі немає → 410;
  • гард: нова мапа менша за 50% попередньої (при попередній ≥100 рядків) НЕ перезаписує файл і дає exit 2 (збій кешу/індексу не має вбити видимість масовим 410);
    PROM_REDIRECTS_ALLOW_SHRINK=1 — свідоме скорочення.
Самодостатній."""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import generate_prom_redirects as gpr  # noqa: E402
import telegram_notify  # noqa: E402
_alerts = []
telegram_notify.send_throttled_alert = lambda *a, **k: (_alerts.append((a, k)) or True)    # з тесту реальних алертів не слати

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


tmp = Path(tempfile.mkdtemp())
site = tmp / "site"
site.mkdir()
(site / "index.json").write_text(json.dumps([{"id": "1"}, {"id": "2"}, {"id": "3"}]), encoding="utf-8")
(site / "product-77.html").write_text("oos page", encoding="utf-8")          # товар без залишку: сторінка жива
(site / "product-88.html").write_text("oos page", encoding="utf-8")
(site / "catalog.html").write_text("not a product", encoding="utf-8")
cache = {"1": {"prom_id": 1001}, "2": {"prom_id": 1002}, "77": {"prom_id": 1077}, "999": {"prom_id": 1999}, "5": {}}
(tmp / "cache.json").write_text(json.dumps(cache), encoding="utf-8")
gpr.CACHE_FILE = tmp / "cache.json"
gpr.INDEX_FILE = site / "index.json"
gpr.OUT_FILE = tmp / "prom.map"
gpr.OUT_FILE_APACHE = tmp / "prom_apache.map"
(tmp / "groups.json").write_text("{}", encoding="utf-8")        # мапа груп Prom (12.10.2026) тут не предмет — окремий test_prom_group_redirects.py; але в репо нічого не пишемо
gpr.GROUP_JSON = tmp / "groups.json"
gpr.OUT_FILE_GROUPS_APACHE = tmp / "groups_apache.map"

valid = gpr.load_valid_ids()
chk("валідні id = індекс (1,2,3) + сторінки без залишку (77,88); catalog.html не рахується", valid == {"1", "2", "3", "77", "88"})
gpr.main()
lines = [l for l in gpr.OUT_FILE_APACHE.read_text(encoding="utf-8").splitlines() if l[:1].isdigit()]
chk("мапа Apache: 1001→product-1, 1002→product-2, 1077→product-77 (сторінка без залишку)", sorted(lines) == ["1001 /product-1.html", "1002 /product-2.html", "1077 /product-77.html"])
chk("товар, якого нема ніде (999), у мапі немає → його Prom-URL віддасть 410", not any(l.startswith("1999 ") for l in lines))

# ── гард скорочення
big = ["# header"] + [f"{9000 + i} /product-{i}.html" for i in range(200)]
gpr.OUT_FILE_APACHE.write_text("\n".join(big) + "\n", encoding="utf-8")
before = gpr.OUT_FILE_APACHE.read_text(encoding="utf-8")
os.environ.pop("PROM_REDIRECTS_ALLOW_SHRINK", None)
try:
    gpr.main()
    chk("гард: різке скорочення (3 проти 200) → SystemExit(2)", False)
except SystemExit as e:
    chk("гард: різке скорочення (3 проти 200) → SystemExit(2)", e.code == 2)
chk("гард: стара мапа лишилась без змін", gpr.OUT_FILE_APACHE.read_text(encoding="utf-8") == before)
chk("гард: шле (тротлений) алерт у Telegram — не мовчить у journal", len(_alerts) == 1 and _alerts[0][0][0] == "prom_redirects_shrink" and "410" in _alerts[0][0][1])
os.environ["PROM_REDIRECTS_ALLOW_SHRINK"] = "1"
gpr.main()
chk("PROM_REDIRECTS_ALLOW_SHRINK=1 дозволяє свідоме скорочення", len([l for l in gpr.OUT_FILE_APACHE.read_text(encoding="utf-8").splitlines() if l[:1].isdigit()]) == 3)
del os.environ["PROM_REDIRECTS_ALLOW_SHRINK"]

# мала попередня мапа (<100) гард не вмикає (перший запуск / малий каталог)
gpr.OUT_FILE_APACHE.write_text("# h\n1 /product-1.html\n2 /product-2.html\n3 /product-3.html\n4 /product-4.html\n5 /product-5.html\n", encoding="utf-8")
gpr.main()
chk("мала попередня мапа (<100 рядків): гард не діє", len([l for l in gpr.OUT_FILE_APACHE.read_text(encoding="utf-8").splitlines() if l[:1].isdigit()]) == 3)

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ мапа Prom-URL / 410 — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
