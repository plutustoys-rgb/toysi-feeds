#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_meta_excluded_ids.py — товари, відхилені Meta за Правилами торгівлі, не потрапляють у Meta-фід (14.11.2026: «Quest Love» id 302282).
Лише Meta: Google/Bing-генератори файл не читають. `python test_meta_excluded_ids.py` → exit 0/1."""
import json
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import generate_meta_feed as m  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


real = m.load_excluded_ids()
chk("репозиторний meta_excluded_ids.json: id 302282 («Quest Love») виключено, причина записана", "302282" in real and "Meta" in real["302282"])
items = [{"id": "302282"}, {"id": "302283"}, {"id": 302284}]
kept, n = m.drop_excluded(items, real)
chk("drop_excluded: 302282 прибрано, решта лишається (порядок збережено), лічильник 1", [i["id"] for i in kept] == ["302283", 302284] and n == 1)
kept, n = m.drop_excluded(items, {})
chk("порожній список → нічого не прибрано", kept == items and n == 0)
kept, n = m.drop_excluded([{"id": 302282}], {"302282": "x"})
chk("id як число теж збігається зі строковим ключем", kept == [] and n == 1)
d = Path(tempfile.mkdtemp())
chk("нема файлу → {} без винятку", m.load_excluded_ids(d / "none.json") == {})
(d / "bad.json").write_text("{не json", encoding="utf-8")
chk("битий файл → {} без винятку (Meta-фід не падає)", m.load_excluded_ids(d / "bad.json") == {})
(d / "shape.json").write_text(json.dumps({"ids": ["302282"]}), encoding="utf-8")
chk("неправильна форма (ids — список) → {}", m.load_excluded_ids(d / "shape.json") == {})
(d / "ok.json").write_text(json.dumps({"ids": {"1": "a", 2: "b"}}), encoding="utf-8")
chk("ключі приводяться до рядків", m.load_excluded_ids(d / "ok.json") == {"1": "a", "2": "b"})
src = Path("generate_google_feed.py").read_text(encoding="utf-8") + Path("generate_bing_feed.py").read_text(encoding="utf-8")
chk("Google/Bing-генератори список Meta НЕ використовують", "meta_excluded_ids" not in src)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
