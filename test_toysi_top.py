#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_toysi_top.py — ТОП Toysi → site/toysi_top.json (toysi_top.py, 16.10.2026).
Інваріанти: (1) parse_ids бере ID з data-photo2="/p/<n>/enl_<ID>.jpg", порядок збережено, без повторів; (2) main пише файл зі списками shipments/yesterday/week/month, ids — рядки, лише ті, що є в index.json (порядок Toysi);
(3) <10 карток на будь-якій сторінці або мережевий збій → файл НЕ перезаписується (старий лишається), код 1; (4) порожній index.json → файл не пишеться; (5) атомарно (.tmp не лишається); (6) формат {"date","lists","counts"}. `python test_toysi_top.py` → exit 0/1."""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import toysi_top as tt  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def page(ids):
    return "<html>" + "".join(f'<div class="product" data-photo2="/p/2/enl_{i}.jpg"></div>' for i in ids) + "</html>"


chk("parse_ids: порядок, без повторів, лише enl_-картки", tt.parse_ids(page([5, 3, 5, 9]) + '<img src="/p/enl_777.jpg">') == ["5", "3", "9"])
ids_all = list(range(1000, 1060))


class R:
    def __init__(self, text, code=200):
        self.text, self.status_code = text, code


def getter_ok(url):
    if "top-prodazh-nedeli" in url:
        return R(page(ids_all[10:62]))
    return R(page(ids_all[:52]))


d = Path(tempfile.mkdtemp())
idx = d / "index.json"
idx.write_text(json.dumps([{"id": str(i), "pr": 100} for i in ids_all if i % 2 == 0]), encoding="utf-8")
out = d / "toysi_top.json"
now = datetime(2026, 10, 16, 18, 0)
tt.time.sleep = lambda x: None
rc = tt.main(getter_ok, now, out, idx)
data = json.loads(out.read_text(encoding="utf-8"))
chk("main: код 0, формат {date, lists, counts}, 4 списки", rc == 0 and data["date"] == "2026-10-16" and set(data["lists"]) == {"shipments", "yesterday", "week", "month"} and set(data["counts"]) == set(data["lists"]))
chk("ids — рядки, лише ті, що є в index.json (парні), порядок Toysi", all(isinstance(i, str) and int(i) % 2 == 0 for i in data["lists"]["shipments"]) and data["lists"]["shipments"] == [str(i) for i in ids_all[:52] if i % 2 == 0])
chk("counts: toysi=52, on_site=26 для shipments; week інший склад", data["counts"]["shipments"] == {"toysi": 52, "on_site": 26} and data["lists"]["week"] != data["lists"]["shipments"])
chk(".tmp не лишився", not list(d.glob("*.tmp")))
before = out.read_text(encoding="utf-8")
rc = tt.main(lambda u: R(page([1, 2, 3])), now, out, idx)
chk("<10 карток на сторінці (змінилась розмітка): код 1, файл не змінено", rc == 1 and out.read_text(encoding="utf-8") == before)
rc = tt.main(lambda u: R("", 503), now, out, idx)
chk("збій мережі/503: код 1, файл не змінено", rc == 1 and out.read_text(encoding="utf-8") == before)
def boom(u):
    raise RuntimeError("net")
rc = tt.main(boom, now, out, idx)
chk("виняток мережі: код 1, без падіння, файл не змінено", rc == 1 and out.read_text(encoding="utf-8") == before)
empty = d / "empty.json"
empty.write_text("[]", encoding="utf-8")
out2 = d / "o2.json"
chk("порожній index.json → код 1, файл не створено", tt.main(getter_ok, now, out2, empty) == 1 and not out2.exists())
# правки аудиту #828: обрізаний HTML (блоків product більше, ніж фото-атрибутів) і порожній після фільтра список не затирають файл
trunc = page(ids_all[:52]) + "<div class=\"product product-req\"></div>" * 3
before2 = out.read_text(encoding="utf-8")
rc = tt.main(lambda u: R(trunc), now, out, idx)
chk("блоків product (55) ≠ data-photo2 (52) → розмітка змінилась/обрізано: код 1, файл не змінено", rc == 1 and out.read_text(encoding="utf-8") == before2)
none_on_site = d / "none.json"
none_on_site.write_text(json.dumps([{"id": "999999", "pr": 1}]), encoding="utf-8")
rc = tt.main(getter_ok, now, out, none_on_site)
chk("жодного нашого товару у списку (усі поза index.json) → код 1, добрий файл не затерто порожнім", rc == 1 and out.read_text(encoding="utf-8") == before2)
chk("fetched_at має часовий пояс", "+" in data["fetched_at"][10:] or "-" in data["fetched_at"][10:] or data["fetched_at"].endswith("Z"))
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
