#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_basket_blocked.py — 22.10.2026: POST /api/basket_blocked (фронт повідомляє про змішаний кошик, заблокований ще в кошику).
Навіщо: після блокування на вході такий кошик до /api/order не доходить, а лічильник відхилень (site_rejected_baskets, PR #970) — вхід для рішення «будувати поділ замовлень».
ІНВАРІАНТИ:
  - змішаний кошик (Toysi+rt, Toysi+zb, rt+zb) → {"ok": true} і РІВНО один рядок обліку виду mixed_blocked_cart; не змішаний/невідомий id/qty<1/сміття → {"ok": false}, обліку нема;
  - у записі лише агрегати (рядки, штуки, сума, n_rt/n_toysi/n_zb): жодних ПІБ/телефону/назв/id товарів, навіть якщо їх надіслали;
  - ліміти: на IP (за замовчуванням 10/хв) і загальний (60/хв) → 429;
  - збій запису обліку не ламає відповідь; report_line показує новий вид окремо."""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
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


so.price_map = lambda: {"100": {"price": 250, "name": "Іграшка"}, "101": {"price": 100, "name": "Інша"}, "rt-1": {"price": 400, "name": "РТ"}, "zb-1": {"price": 480, "name": "Лежак"}}
srv = ThreadingHTTPServer(("127.0.0.1", 0), so.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}/api/basket_blocked"


def post(body, raw=False):
    data = body if raw else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(URL, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def lines():
    try:
        return [json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        return []


def fresh_limiters(per_ip=1000, glob=1000):
    so._BLOCKED_LIMITER = so.RateLimiter(per_ip, 60)
    so._BLOCKED_GLOBAL = so.RateLimiter(glob, 60)


fresh_limiters()
c, r = post({"items": [{"id": "100", "qty": 2}, {"id": "rt-1", "qty": 1}]})
L = lines()
chk("Toysi + rt: ok=true, один рядок обліку виду mixed_blocked_cart", c == 200 and r == {"ok": True} and len(L) == 1 and L[0]["kind"] == rb.KIND_BLOCKED)
chk("запис: 2 рядки, 3 шт, сума 900, n_rt=1, n_toysi=1, n_zb нема", L[0]["n_lines"] == 2 and L[0]["qty"] == 3 and abs(L[0]["total"] - 900) < 1e-6 and L[0]["n_rt"] == 1 and L[0]["n_toysi"] == 1 and "n_zb" not in L[0])
c, r = post({"items": [{"id": "100", "qty": 1}, {"id": "zb-1", "qty": 1}], "name": "Іван Петренко", "phone": "+380501112233", "city_name": "Київ", "email": "a@b.c"})
L = lines()
chk("Toysi + zb: ok=true, другий рядок обліку, n_zb=1, n_toysi=1", c == 200 and r == {"ok": True} and len(L) == 2 and L[1]["n_zb"] == 1 and L[1]["n_toysi"] == 1 and L[1]["n_rt"] == 0)
blob = LOG.read_text(encoding="utf-8")
chk("персональних даних і id/назв товарів у файлі обліку НЕМАЄ (навіть якщо їх надіслали)", all(w not in blob for w in ("Петренко", "380501112233", "Київ", "a@b.c", "Лежак", "Іграшка", "rt-1", "zb-1", "\"100\"")))
chk("ключі запису — лише агрегати", set(L[0]) == {"ts", "kind", "n_lines", "qty", "total", "n_rt", "n_toysi"} and set(L[1]) == {"ts", "kind", "n_lines", "qty", "total", "n_rt", "n_toysi", "n_zb"})
c, r = post({"items": [{"id": "rt-1", "qty": 1}, {"id": "zb-1", "qty": 1}]})
chk("rt + zb теж змішаний: ok=true", c == 200 and r == {"ok": True} and len(lines()) == 3)
n0 = len(lines())
for label, body in (("два товари Toysi (не змішаний)", {"items": [{"id": "100", "qty": 1}, {"id": "101", "qty": 1}]}),
                    ("лише rt (не змішаний)", {"items": [{"id": "rt-1", "qty": 2}]}),
                    ("невідомий id", {"items": [{"id": "100", "qty": 1}, {"id": "999999", "qty": 1}]}),
                    ("qty 0", {"items": [{"id": "100", "qty": 0}, {"id": "rt-1", "qty": 1}]}),
                    ("qty надто велика", {"items": [{"id": "100", "qty": 9999}, {"id": "rt-1", "qty": 1}]}),
                    ("qty не число", {"items": [{"id": "100", "qty": "abc"}, {"id": "rt-1", "qty": 1}]}),
                    ("items не список", {"items": "x"}),
                    ("порожній items", {"items": []}),
                    ("нема items", {}),
                    ("рядок замість об'єкта", {"items": ["100", "rt-1"]})):
    c, r = post(body)
    chk(f"{label}: 200 ok=false, обліку нема", c == 200 and r == {"ok": False} and len(lines()) == n0)
c, r = post({"items": [{"id": "100", "qty": 1}, {"id": "100", "qty": 1}, {"id": "rt-1", "qty": 1}]})
chk("дублі id в одному запиті (підробка, яка роздуває суму): ok=false, обліку нема", c == 200 and r == {"ok": False} and len(lines()) == n0)
c, r = post("{не json".encode("utf-8"), raw=True)
chk("сміття замість JSON: 200 ok=false, обліку нема", c == 200 and r == {"ok": False} and len(lines()) == n0)
c, r = post({"items": [{"id": "100", "qty": 1}] * (so.MAX_ITEMS + 1)})
chk("забагато рядків (> MAX_ITEMS): ok=false, обліку нема", c == 200 and r == {"ok": False} and len(lines()) == n0)

# ліміти
fresh_limiters(per_ip=3, glob=1000)
mixed = {"items": [{"id": "100", "qty": 1}, {"id": "rt-1", "qty": 1}]}
codes = [post(mixed)[0] for _ in range(5)]
chk(f"ліміт на IP (3/хв): перші 3 → 200, далі 429 ({codes})", codes == [200, 200, 200, 429, 429])
fresh_limiters(per_ip=1000, glob=2)
codes = [post(mixed)[0] for _ in range(4)]
chk(f"загальний ліміт (2/хв): 200, 200, 429, 429 ({codes})", codes == [200, 200, 429, 429])
fresh_limiters()

# збій запису не ламає відповідь
os.environ["SITE_REJECTED_BASKETS_FILE"] = str(TMP)   # каталог замість файлу — запис неможливий
c, r = post(mixed)
chk("недоступний файл обліку: відповідь 200 {ok: true} (облік best-effort, подію не записано), без винятку", c == 200 and r == {"ok": True})
os.environ["SITE_REJECTED_BASKETS_FILE"] = str(LOG)

# розмір файлу обмежено (ротації нема): понад ліміт нові події не пишуться, відповідь не змінюється
n_before = len(lines())
saved = rb.MAX_FILE_BYTES
rb.MAX_FILE_BYTES = LOG.stat().st_size
c, r = post(mixed)
chk("файл обліку досяг ліміту розміру: відповідь 200, нового рядка НЕМАЄ", c == 200 and len(lines()) == n_before)
rb.MAX_FILE_BYTES = saved
c, r = post(mixed)
chk("після зняття ліміту запис знову працює", c == 200 and len(lines()) == n_before + 1)

# звіт
n_blocked = sum(1 for x in lines() if x["kind"] == rb.KIND_BLOCKED)
chk(f"report_line: новий вид показано окремо і число збігається з файлом ({n_blocked})", f"змішаний, заблоковано в кошику: {n_blocked} за 7 діб" in rb.report_line())
chk("існуючі види в звіті на місці", "змішаний Toysi+RT:" in rb.report_line() and "RT + накладений:" in rb.report_line())
srv.shutdown()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
