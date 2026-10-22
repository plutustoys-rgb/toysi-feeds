#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_site_rejected_baskets.py — облік відхилених кошиків сайту (змішаний Toysi+RT, RT+накладений); прохання Консультанта 22.10.

ІНВАРІАНТИ:
  - відхилення в site_order_api.build_order пишуть подію (вид, рядки, штуки, сума, n_rt/n_toysi) і НЕ ЗМІНЮЮТЬ відповідь (OrderError як раніше);
  - у події НЕМАЄ персональних даних (ПІБ/телефон/адреса/назви товарів) — лише перелічені поля;
  - прийняті кошики (rt+prepaid, звичайний cod/prepaid) події НЕ створюють;
  - збій запису (недоступний файл) не ламає прийом: OrderError лишається тим самим, без винятку з обліку;
  - summary/report_line: 7/30 діб, сума за 7, «0» видно, а не мовчання; битий рядок у файлі не валить.
Тимчасовий файл (SITE_REJECTED_BASKETS_FILE), без мережі й боєвої БД.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
LOG = TMP / "rej.jsonl"
os.environ["SITE_REJECTED_BASKETS_FILE"] = str(LOG)
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "site"))
import site_order_api as so  # noqa: E402
import site_rejected_baskets as rb  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def events():
    return [json.loads(ln) for ln in LOG.read_text(encoding="utf-8").splitlines()] if LOG.exists() else []


def raises(fn):
    try:
        fn()
    except so.OrderError:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


so.price_map = lambda: {"100": {"price": 250, "name": "Секретна назва товару"}, "rt-101": {"price": 400, "name": "Іграшка RT"}}
so.stock_snapshot = lambda: ({"100": 5, "rt-101": 3}, None)
base = {"items": [{"id": "rt-101", "qty": 1}], "name": "Іван Петренко", "phone": "0501234567", "city_name": "Київ",
        "warehouse_name": "відділення 1", "payment_method": "prepaid"}

# --- прийняті кошики подій не створюють
so.build_order(dict(base))                                             # rt + prepaid
so.build_order(dict(base, items=[{"id": "100", "qty": 2}], payment_method="cod"))   # звичайний cod
so.build_order(dict(base, items=[{"id": "100", "qty": 1}]))            # звичайний prepaid
chk("прийняті кошики (rt+prepaid, звичайні cod/prepaid) не логуються", events() == [])

# --- змішаний
mixed = dict(base, items=[{"id": "100", "qty": 2}, {"id": "rt-101", "qty": 1}])
chk("змішаний кошик: OrderError як раніше", raises(lambda: so.build_order(dict(mixed))))
ev = events()
chk("змішаний: рівно одна подія виду mixed_toysi_rt", len(ev) == 1 and ev[0]["kind"] == rb.KIND_MIXED)
chk("змішаний: n_lines=2, qty=3, total=900 (250*2+400), n_rt=1, n_toysi=1",
    ev[0]["n_lines"] == 2 and ev[0]["qty"] == 3 and ev[0]["total"] == 900 and ev[0]["n_rt"] == 1 and ev[0]["n_toysi"] == 1)
chk("подія має ЛИШЕ дозволені поля (без ПІБ/телефону/адреси/назв)",
    set(ev[0]) == {"ts", "kind", "n_lines", "qty", "total", "n_rt", "n_toysi"})
raw = LOG.read_text(encoding="utf-8")
chk("у файлі немає персональних даних і назв товарів",
    not any(x in raw for x in ("Петренко", "0501234567", "Київ", "відділення", "Секретна назва", "Іграшка RT")))

# --- rt + накладений / невідомий спосіб
chk("rt + cod: OrderError як раніше", raises(lambda: so.build_order(dict(base, payment_method="cod"))))
chk("rt + спосіб не вказано: OrderError як раніше", raises(lambda: so.build_order({k: v for k, v in base.items() if k != "payment_method"})))
ev = events()
chk("rt+cod і rt+без способу: дві події rt_cod", [e["kind"] for e in ev[1:]] == [rb.KIND_RT_COD, rb.KIND_RT_COD])
chk("змішаний кошик НЕ дає другої події rt_cod (відхиляється першим)", sum(1 for e in ev if e["kind"] == rb.KIND_MIXED) == 1)

# --- збій запису не ламає прийом
os.environ["SITE_REJECTED_BASKETS_FILE"] = str(TMP)          # «файл» = каталог -> запис неможливий
n_before = len(events()) if LOG.exists() else 0
chk("недоступний файл обліку: build_order все одно кидає той самий OrderError (не інший виняток)",
    raises(lambda: so.build_order(dict(mixed))))
chk("недоступний файл обліку: прийнятий кошик проходить", so.build_order(dict(base))[1] == 400)
os.environ["SITE_REJECTED_BASKETS_FILE"] = str(LOG)

# --- summary / report_line
now = datetime(2026, 10, 22, 12, 0, 0)
fake = TMP / "fake.jsonl"
os.environ["SITE_REJECTED_BASKETS_FILE"] = str(fake)
rows = [
    {"ts": (now - timedelta(days=1)).isoformat(), "kind": rb.KIND_MIXED, "n_lines": 2, "qty": 2, "total": 1000, "n_rt": 1, "n_toysi": 1},
    {"ts": (now - timedelta(days=6)).isoformat(), "kind": rb.KIND_MIXED, "n_lines": 3, "qty": 3, "total": 500.5, "n_rt": 1, "n_toysi": 2},
    {"ts": (now - timedelta(days=20)).isoformat(), "kind": rb.KIND_MIXED, "n_lines": 2, "qty": 2, "total": 700, "n_rt": 1, "n_toysi": 1},
    {"ts": (now - timedelta(days=40)).isoformat(), "kind": rb.KIND_MIXED, "n_lines": 1, "qty": 1, "total": 9, "n_rt": 1, "n_toysi": 0},
    {"ts": (now - timedelta(days=2)).isoformat(), "kind": rb.KIND_RT_COD, "n_lines": 1, "qty": 1, "total": 400, "n_rt": 1, "n_toysi": 0},
]
fake.write_text("\n".join(json.dumps(r) for r in rows) + "\n{битий json\n" + json.dumps({"kind": "x"}) + "\n", encoding="utf-8")
s = rb.summary(now)
chk("summary: mixed 2 за 7 діб, 3 за 30 (40-денна подія поза вікном), сума за 7 = 1500.5",
    s[rb.KIND_MIXED]["7d"] == 2 and s[rb.KIND_MIXED]["30d"] == 3 and abs(s[rb.KIND_MIXED]["total7"] - 1500.5) < 1e-6)
chk("summary: rt_cod 1 за 7 і 30; битий рядок і запис без ts не валять", s[rb.KIND_RT_COD]["7d"] == 1 and s[rb.KIND_RT_COD]["30d"] == 1)
line = rb.report_line(now)
chk("report_line: обидва види, 7/30 діб і сума", "змішаний Toysi+RT: 2 за 7 діб (3 за 30)" in line and "RT + накладений: 1 за 7 діб (1 за 30)" in line
    and "1500 грн" in line)
fake.write_text("", encoding="utf-8")
chk("report_line при порожньому обліку: «0» видно, а не мовчання", "0 за 7 діб (0 за 30)" in rb.report_line(now) and "обліку ще нема подій" in rb.report_line(now))
os.environ["SITE_REJECTED_BASKETS_FILE"] = str(TMP / "nonexistent.jsonl")
chk("report_line без файлу не падає", "Відхилені кошики сайту" in rb.report_line(now))

sys.exit(1 if F else 0)
