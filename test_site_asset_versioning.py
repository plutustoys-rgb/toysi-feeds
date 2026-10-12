#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_asset_versioning.py — 11.10.2026: /assets/ кешується браузером на добу (Cache-Control max-age=86400), імена файлів без хешів → після деплою фіксу відвідувач,
що вже був на сайті, до доби виконує СТАРИЙ app.js/styles.css при НОВОМУ HTML (так було 10.10). Інваріанти:
  • page() підключає styles.css і app.js з `?v=<8 hex sha1 вмісту>`;
  • версія змінюється лише коли змінився вміст; CRLF↔LF її не змінює (Windows-checkout ≠ Linux-VPS);
  • файлу нема (тека без assets) → URL без версії, без винятку;
  • у згенерованому <head>/<body> нема НЕверсійованих посилань на styles.css/app.js."""
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


h = bs.page("Тест", "<p>x</p>")
links = re.findall(r'(?:href|src)="(assets/(?:styles\.css|app\.js)[^"]*)"', h)
chk("page(): styles.css і app.js підключено з ?v=<8 hex>", len(links) == 2 and all(re.fullmatch(r"assets/(?:styles\.css|app\.js)\?v=[0-9a-f]{8}", x) for x in links))
chk("page(): жодного НЕверсійованого посилання на styles.css/app.js", not re.search(r'(?:href|src)="assets/(?:styles\.css|app\.js)"', h))
h404 = bs.page("404", "<p>x</p>", base_root=True)
chk("сторінка з <base href=\"/\"> теж версійована", 'assets/app.js?v=' in h404 and 'assets/styles.css?v=' in h404)

# ── поведінка на підміненій теці assets
tmp = Path(tempfile.mkdtemp())
(tmp / "assets").mkdir()
orig_file = bs.__file__
bs.__file__ = str(tmp / "build_site.py")
try:
    (tmp / "assets" / "x.js").write_bytes(b"var a=1;\nvar b=2;\n")
    bs._ASSET_VER.clear()
    v1 = bs.asset_url("assets/x.js")
    chk("версія є і має формат ?v=<8 hex>", re.fullmatch(r"assets/x\.js\?v=[0-9a-f]{8}", v1) is not None)
    (tmp / "assets" / "x.js").write_bytes(b"var a=1;\r\nvar b=2;\r\n")
    bs._ASSET_VER.clear()
    chk("CRLF замість LF версію НЕ змінює", bs.asset_url("assets/x.js") == v1)
    (tmp / "assets" / "x.js").write_bytes(b"var a=1;\nvar b=3;\n")
    bs._ASSET_VER.clear()
    v2 = bs.asset_url("assets/x.js")
    chk("зміна вмісту → інша версія (фікс доїде до тих, хто вже був)", v2 != v1)
    bs._ASSET_VER.clear()
    chk("нема файлу → URL без версії і без винятку", bs.asset_url("assets/missing.js") == "assets/missing.js")
finally:
    bs.__file__ = orig_file
    bs._ASSET_VER.clear()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
