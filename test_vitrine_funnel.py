#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_vitrine_funnel.py — воронка Prom-вітрини (vitrine_funnel.py).

Інваріанти (інцидент 08–09.10.2026: кампанія вимкнена → 3 463 з 3 468 «Недоступен», а фід був «нормальний»):
  1. evaluate(): норма → без порушень; кампанія вимкнена (in_cpa 0) → порушення; кнопка покупки <90% → порушення; фід ≠ кабінет >10% → порушення; падіння «В наявності» >15% → порушення.
  2. page_buyable(): розбір реальної форми сторінки (InStock + buy_now_btn; OutOfStock без кнопки).
  3. record(): дописує рядок у CSV (заголовок один раз), мережа підмінена; при порушенні — один throttled-алерт, а при нормі — жодного.
Самодостатній: мережа/Telegram підмінені.
"""
import csv
import sys
import tempfile
from pathlib import Path

import vitrine_funnel as vf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_FAILS = []


def _check(name, got, exp):
    ok = got == exp
    print(f"[{'OK ' if ok else 'FAIL'}] {name}: got={got!r} exp={exp!r}")
    if not ok:
        _FAILS.append(name)


def _row(**kw):
    base = dict(total=3468, avail=3131, not_avail=337, in_cpa=3125, in_cpa_pct=99.8, buy_ok=29, buy_n=30, buy_pct=96.7, feed_offers=3764, feed_delta_pct=20.2)
    base.update(kw)
    return base


# 1. пороги
_check("норма: фід більший на 20% — це черга створення, не порушення", len(vf.evaluate(_row(feed_delta_pct=20.2), None)), 0)
_check("фід більший на 60% (імпорт не створює) → порушення", any("не створює" in p for p in vf.evaluate(_row(feed_delta_pct=60.0), None)), True)
_check("фід менший за кабінет на 15% → порушення", any("без фіду" in p for p in vf.evaluate(_row(feed_delta_pct=-15.0), None)), True)
_check("кампанія вимкнена (0 з 3 131) → порушення", len(vf.evaluate(_row(in_cpa=0, in_cpa_pct=0.0), None)) >= 1, True)
_check("кнопка покупки 5 з 30 → порушення", any("кнопка покупки" in p for p in vf.evaluate(_row(buy_ok=5, buy_pct=16.7), None)), True)
_check("падіння «В наявності» 3131 → 2000 → порушення", any("впало" in p for p in vf.evaluate(_row(avail=2000), {"avail": 3131})), True)
_check("падіння 3131 → 2900 (−7%) — норма", any("впало" in p for p in vf.evaluate(_row(avail=2900, feed_delta_pct=0.0), {"avail": 3131})), False)
_check("без вибірки сторінок (мережа) — порушення по кнопці не вигадується", any("кнопка" in p for p in vf.evaluate(_row(buy_n=0, buy_ok=0, buy_pct=0.0, feed_delta_pct=0.0), None)), False)

# 2. розбір сторінки
class _R:
    def __init__(self, t):
        self._t = t

    def read(self):
        return self._t.encode("utf-8")


_pages = {
    "ok": '..."availability":"http://schema.org/InStock"... data-qaid="buy_now_btn" ...',
    "oos": '..."availability":"http://schema.org/OutOfStock"... Недоступен ...',
}
_orig = vf.urllib.request.urlopen
vf.urllib.request.urlopen = lambda req, timeout=0: _R(_pages["ok"] if req.full_url.endswith("/ok") else _pages["oos"])
_check("сторінка InStock + кнопка", vf.page_buyable("https://x/ok"), (True, True))
_check("сторінка OutOfStock без кнопки", vf.page_buyable("https://x/oos"), (False, False))


def _boom(req, timeout=0):
    raise OSError("net")


vf.urllib.request.urlopen = _boom
_check("збій мережі → (None, None), не «недоступний»", vf.page_buyable("https://x/ok"), (None, None))
vf.urllib.request.urlopen = _orig

# 3. record(): CSV + алерт
tmp = Path(tempfile.mkdtemp())
vf.CSV_FILE = tmp / "vitrine_funnel.csv"
alerts = []
import telegram_notify as tn
tn.send_throttled_alert = lambda key, text, cooldown_sec=0: alerts.append((key, text)) or True
vf.feed_offer_count = lambda: 100
cat_ok = {str(i): {"presence": "avail", "in_running_cpa": True, "view_catalog_url": "https://x/ok"} for i in range(100)}
vf.page_buyable = lambda url: (True, True)
r = vf.record(cat_ok, sample_n=10)
_check("норма: рядок у CSV, алерта нема", (len(list(csv.DictReader(open(vf.CSV_FILE, encoding="utf-8")))), len(alerts), r["alerts"]), (1, 0, ""))
cat_bad = {str(i): {"presence": "avail", "in_running_cpa": False, "view_catalog_url": "https://x/oos"} for i in range(100)}
vf.page_buyable = lambda url: (False, False)
r = vf.record(cat_bad, sample_n=10)
rows = list(csv.DictReader(open(vf.CSV_FILE, encoding="utf-8")))
_check("кампанія вимкнена + кнопки нема: другий рядок, один алерт з обома причинами",
       (len(rows), len(alerts), "у кампанії" in alerts[0][1] and "кнопка покупки" in alerts[0][1]), (2, 1, True))
_check("заголовок CSV один раз", open(vf.CSV_FILE, encoding="utf-8").read().count("at,total"), 1)

print()
if _FAILS:
    print("FAILED:", _FAILS)
    sys.exit(1)
print("ALL OK")
