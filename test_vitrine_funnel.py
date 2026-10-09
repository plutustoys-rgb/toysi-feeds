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
import json
import os
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
    base = dict(total=3468, avail=3131, not_avail=337, in_cpa=3125, in_cpa_pct=99.8, buy_ok=29, buy_n=30, buy_pct=96.7, sample_n=30, buy_err=0, feed_offers=3764, feed_delta_pct=20.2)
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
# аудит #648: Д1 — порожня вітрина мовчала; Д2 — сторінки не читаються мовчали
_check("Д1: total>0, avail=0 → порушення НАВІТЬ без попереднього рядка", any("= 0" in p for p in vf.evaluate(_row(total=50, avail=0, in_cpa=0, in_cpa_pct=0.0, feed_delta_pct=None), None)), True)
_check("Д1: і на наступну добу (prev.avail=0) — теж", any("= 0" in p for p in vf.evaluate(_row(total=50, avail=0, in_cpa=0, in_cpa_pct=0.0, feed_delta_pct=None), {"avail": 0})), True)
_check("Д1: порожній кабінет (total=0) — не вигадуємо порушення", vf.evaluate(_row(total=0, avail=0, in_cpa=0, in_cpa_pct=0.0, buy_n=0, buy_err=0, sample_n=0, feed_delta_pct=None), None), [])
_check("Д2: 20 з 30 сторінок без відповіді → порушення «не читаються»", any("не читаються" in p for p in vf.evaluate(_row(buy_n=10, buy_err=20, buy_ok=10, buy_pct=100.0), None)), True)
_check("Д2: 5 з 30 без відповіді (свіжі позиції 404) — норма", any("не читаються" in p for p in vf.evaluate(_row(buy_n=25, buy_err=5, buy_ok=25, buy_pct=100.0), None)), False)

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
_calls = []


def _flaky(req, timeout=0):
    _calls.append(1)
    if len(_calls) == 1:
        raise OSError("timeout")
    return _R(_pages["ok"])


vf.urllib.request.urlopen = _flaky
_check("один повтор: перший таймаут, другий успіх → результат є", (vf.page_buyable("https://x/ok"), len(_calls)), ((True, True), 2))
_calls.clear()


def _gone(req, timeout=0):
    _calls.append(1)
    raise vf.urllib.error.HTTPError("https://x", 404, "nf", None, None)


vf.urllib.request.urlopen = _gone
_check("HTTP 404 (свіжа картка) — без повтору, (None, None)", (vf.page_buyable("https://x/y"), len(_calls)), ((None, None), 1))
vf.urllib.request.urlopen = _orig

# 3. record(): CSV + алерт
tmp = Path(tempfile.mkdtemp())
vf.CSV_FILE = tmp / "vitrine_funnel.csv"
alerts = []
import telegram_notify as tn
tn.send_throttled_alert = lambda key, text, cooldown_sec=0: alerts.append((key, text)) or True
vf.feed_offer_count = lambda: 100
_NO_ORD = {"last_order_at": "", "days_since_order": None, "orders_14d": None, "orders_note": "нема даних: тест"}
_NO_RET = {"ret_total": None, "ret_in_cab": None, "ret72_total": None, "ret72_in_cab": None, "ret_failed": None,
           "ret_pub_buy": None, "ret_pub_404": None, "ret_pub_n": None, "ret_note": "нема даних: тест"}
vf.last_orders = lambda: dict(_NO_ORD)
vf.returned_fate = lambda cat: dict(_NO_RET)
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

# аудит #648: Д3 — алерт іде ДО запису CSV (заблокований файл не ковтає сигнал); cooldown 24 год; схема відсутня → (None, None)
alerts.clear()
_cd = []
tn.send_throttled_alert = lambda key, text, cooldown_sec=0: (alerts.append((key, text)), _cd.append(cooldown_sec)) and True
vf.CSV_FILE = tmp  # каталог замість файлу: open(..., "a") → PermissionError/IsADirectoryError (OSError)
vf.page_buyable = lambda url: (False, False)
r = vf.record(cat_bad, sample_n=10)
_check("Д3: CSV недоступний — алерт усе одно надіслано, виняток не піднято", (len(alerts), _cd[0]), (1, 24 * 3600))

_orig2 = vf.urllib.request.urlopen
vf.urllib.request.urlopen = lambda req, timeout=0: _R("<html>без схеми</html>")
import importlib
importlib.reload(vf)  # повернути справжній page_buyable (вище підмінений)
vf.urllib.request.urlopen = lambda req, timeout=0: _R("<html>без схеми</html>")
_check("сторінка без schema.org/availability → (None, None), не «недоступний»", vf.page_buyable("https://x/y"), (None, None))
vf.urllib.request.urlopen = _orig2

# ── 5. Замовлення Prom і доля повернених (запит Консультанта 09.10) ───────────
# 5a. evaluate_orders: тиша ≥14 діб, подія «перше замовлення», None не вигадується
_s, _e = vf.evaluate_orders({"days_since_order": 15, "last_order_at": "2026-09-24 10:00", "orders_14d": 0}, None)
_check("5a. 15 діб тиші → тривога тиші, події нема", (len(_s), _e), (1, []))
_s, _e = vf.evaluate_orders({"days_since_order": 3, "last_order_at": "2026-10-12 10:00", "orders_14d": 2}, {"avail": 100, "days_since_order": 18})
_check("5b. після розриву 18 діб прийшло замовлення → подія, тиші нема", (_s, len(_e)), ([], 1))
_s, _e = vf.evaluate_orders({"days_since_order": None}, {"avail": 1, "days_since_order": 20})
_check("5c. немає даних про замовлення → нічого не вигадуємо", (_s, _e), ([], []))
_s, _e = vf.evaluate_orders({"days_since_order": 5}, {"avail": 1, "days_since_order": 3})
_check("5d. розрив 3→5 діб (замовлень нема, але <14) — ні тиші, ні події", (_s, _e), ([], []))
# 5e. evaluate_returns
_r = vf.evaluate_returns({"ret72_total": 1000, "ret72_in_cab": 300, "ret_pub_n": 15, "ret_pub_buy": 14, "ret_pub_404": 1})
_check("5e. з 1000 повернених >72 год лише 300 у кабінеті (30%) → «паперовий» приріст", any("паперовий" in x for x in _r), True)
_r = vf.evaluate_returns({"ret72_total": 1000, "ret72_in_cab": 900, "ret_pub_n": 15, "ret_pub_buy": 5, "ret_pub_404": 10})
_check("5f. у кабінеті 90%, але публічні з кнопкою 5/15 → окреме порушення", (len(_r), "лише 5/15" in _r[0]), (1, True))
_check("5g. дані відсутні (None) → без порушень", vf.evaluate_returns(dict(_NO_RET)), [])
_check("5h. мало повернених (<100) → не робимо висновків", vf.evaluate_returns({"ret72_total": 40, "ret72_in_cab": 1}), [])


# 5i. last_orders: мок requests.get
class _Resp:
    def __init__(self, code, data):
        self.status_code, self._d = code, data

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


import requests as _rq
_orig_get = _rq.get
_now = vf.datetime(2026, 10, 9, 12, 0, 0)
os.environ["PROM_API_KEY"] = "t"
_rq.get = lambda *a, **k: _Resp(200, {"orders": [{"id": 2, "date_created": "2026-09-24T08:30:00.123"}, {"id": 1, "date_created": "2026-09-01T10:00:00"}]})
_o = vf.last_orders(_now)
_check("5i. останнє 24.09 → 15 діб розриву, 0 за 14 діб", (_o["last_order_at"], _o["days_since_order"], _o["orders_14d"]), ("2026-09-24 08:30", 15, 0))
_rq.get = lambda *a, **k: _Resp(401, {})
_check("5j. 401 → «нема даних», діб None", (vf.last_orders(_now)["days_since_order"], vf.last_orders(_now)["orders_note"].startswith("нема даних")), (None, True))
_rq.get = lambda *a, **k: _Resp(200, {"orders": []})
_o = vf.last_orders(_now)
_check("5k. жодного замовлення за 60 діб → days_since = 60 і примітка", (_o["days_since_order"], "жодного" in _o["orders_note"]), (60, True))
_rq.get = _orig_get
_old_key = os.environ.pop("PROM_API_KEY", None)
vf.BASE_DIR = Path(tempfile.mkdtemp())   # щоб load_dotenv не підтягнув справжній .env
_check("5l. нема PROM_API_KEY → «нема даних»", vf.last_orders(_now)["orders_note"].startswith("нема даних"), True)

# 5m. returned_fate: мок публічного price_state і сторінок
_state = {"_readded_at": {**{f"a{i}": "2026-10-08T10:00:00" for i in range(120)}, **{f"n{i}": "2026-10-09T11:30:00" for i in range(10)}},
          "_readd_failed": {"z1": "x"}}
_cat = {f"a{i}": {"view_catalog_url": f"https://x/a{i}"} for i in range(60)}   # 60 з 120 «старих» повернених є в кабінеті


class _RS:
    def __init__(self, t):
        self._t = t

    def read(self):
        return self._t


_orig_open = vf.urllib.request.urlopen
vf.urllib.request.urlopen = lambda req, timeout=0: _RS(json.dumps(_state).encode("utf-8"))
_ps = vf.page_state
_seq = iter(["buy"] * 10 + ["404"] * 5)
vf.page_state = lambda url: next(_seq)
datetime_now = vf.datetime(2026, 10, 9, 12, 0, 0)
_f = vf.returned_fate(_cat, datetime_now, sample_n=15)
_check("5m. повернені >24 год 120, у кабінеті 60; >72 год — ті самі 120 (8.10 10:00 — це ~26 год)", (_f["ret_total"], _f["ret_in_cab"]), (120, 60))
_check("5n. >72 год: повернених 0 (усі 8.10 10:00 — лише ~26 год), failed=1", (_f["ret72_total"], _f["ret_failed"]), (0, 1))
_check("5o. вибірка сторінок: 10 купити, 5 — 404, n=15", (_f["ret_pub_buy"], _f["ret_pub_404"], _f["ret_pub_n"]), (10, 5, 15))
vf.page_state = _ps
vf.urllib.request.urlopen = _boom
_f = vf.returned_fate(_cat, datetime_now)
_check("5p. price_state недоступний → «нема даних», числа None", (_f["ret_total"], _f["ret_note"].startswith("нема даних")), (None, True))
vf.urllib.request.urlopen = _orig_open

# 5q. page_state: розрізняє купити / не купити / 404 / збій
vf.urllib.request.urlopen = lambda req, timeout=0: _R(_pages["ok"])
_check("5q1. page_state: InStock+кнопка → buy", vf.page_state("https://x/ok"), "buy")
vf.urllib.request.urlopen = lambda req, timeout=0: _R(_pages["oos"])
_check("5q2. page_state: OutOfStock → nobuy", vf.page_state("https://x/oos"), "nobuy")
vf.urllib.request.urlopen = _gone
_check("5q3. page_state: 404 → '404'", vf.page_state("https://x/gone"), "404")
vf.urllib.request.urlopen = _boom
_check("5q4. page_state: мережа → 'err'", vf.page_state("https://x/net"), "err")
vf.urllib.request.urlopen = _orig_open

# 5r. ротація CSV при зміні заголовка
_tmp2 = Path(tempfile.mkdtemp())
vf.CSV_FILE = _tmp2 / "vitrine_funnel.csv"
vf.CSV_FILE.write_text("at,total\n2026-10-09,1\n", encoding="utf-8")
vf._rotate_csv_if_header_changed()
_check("5r. старий заголовок → файл відкладено (.old.csv), нового ще нема", (vf.CSV_FILE.exists(), len(list(_tmp2.glob("*.old.csv")))), (False, 1))

# ── 6. Фікси аудиту #655: межі, часовий пояс, скасовані, збійна форма даних, ротація, пагінація ─────────────
for _d, _exp in ((13, 0), (14, 1), (15, 1)):
    _s, _e = vf.evaluate_orders({"days_since_order": _d, "last_order_at": "x", "orders_14d": 0}, None)
    _check(f"6a. межа тиші: {_d} діб → тривог {_exp}", len(_s), _exp)
_s, _e = vf.evaluate_orders({"days_since_order": 13}, {"days_since_order": 14})
_check("6b. подія: prev 14 → cur 13 → є", len(_e), 1)
_s, _e = vf.evaluate_orders({"days_since_order": 0}, {"days_since_order": 14})
_check("6c. подія: prev 14 → cur 0 → є", len(_e), 1)
_s, _e = vf.evaluate_orders({"days_since_order": 0}, {"days_since_order": 13})
_check("6d. prev 13 (розриву ще не було) → події нема", len(_e), 0)
_s, _e = vf.evaluate_orders({"days_since_order": 15}, {"days_since_order": 20})
_check("6e. 20 → 15 (усе ще ≥14) → події нема, тиша є", (len(_e), len(_s)), (0, 1))
_s, _e = vf.evaluate_orders({"days_since_order": 5}, {"days_since_order": 5})
_check("6f. cur не менший за prev → події нема", len(_e), 0)
_r = vf.evaluate_returns({"ret72_total": 100, "ret72_in_cab": 49})
_check("6g. 100 повернених, 49 у кабінеті (49%) → порушення", len(_r), 1)
_r = vf.evaluate_returns({"ret72_total": 100, "ret72_in_cab": 50})
_check("6h. 100 повернених, 50 у кабінеті (50%) → норма", len(_r), 0)
_r = vf.evaluate_returns({"ret_pub_n": 9, "ret_pub_buy": 0})
_check("6i. вибірка 9 (<10) → висновків нема", len(_r), 0)
_r = vf.evaluate_returns({"ret_pub_n": 10, "ret_pub_buy": 6})
_check("6j. вибірка 10, кнопка 6/10 (<70%) → порушення", len(_r), 1)

# часовий пояс і скасовані
_utc = vf._parse_prom_dt("2026-09-24T13:23:56.878746+00:00")
_local = vf.datetime(2026, 9, 24, 13, 23, 56, tzinfo=vf.timezone.utc).astimezone().replace(tzinfo=None)
_check("6k. date_created у UTC → переведено в локальний час (не обрізано до [:19])", _utc.replace(microsecond=0), _local)
_check("6l. дата без поясу — як є; сміття → None", (vf._parse_prom_dt("2026-09-24T13:23:56"), vf._parse_prom_dt("xx")), (vf.datetime(2026, 9, 24, 13, 23, 56), None))

os.environ["PROM_API_KEY"] = "t"
_now2 = vf.datetime(2026, 10, 9, 12, 0, 0)
_rq.get = lambda *a, **k: _Resp(200, {"orders": [
    {"id": 3, "date_created": "2026-10-08T09:00:00", "status": "canceled"},      # скасоване — ігнорується
    {"id": 2, "date_created": "2026-09-26T10:00:00", "status": "delivered"},      # 13 діб тому → у вікні 14 діб
    {"id": 1, "date_created": "2026-09-24T10:00:00", "status": "paid"}]})        # 15 діб → поза вікном
_o = vf.last_orders(_now2)
_check("6m. скасоване ігнорується; останнє — 26.09 (13 діб), у вікні 14 діб 1 замовлення", (_o["last_order_at"], _o["days_since_order"], _o["orders_14d"]), ("2026-09-26 10:00", 13, 1))

_pages_n = []


def _endless(*a, **k):
    _pages_n.append(1)
    return _Resp(200, {"orders": [{"id": len(_pages_n) * 1000 + i, "date_created": "2026-10-01T10:00:00", "status": "paid"} for i in range(100)]})


_rq.get = _endless
vf.last_orders(_now2)
_check("6n. пагінація має стелю ORDERS_MAX_PAGES (не нескінченний цикл)", len(_pages_n), vf.ORDERS_MAX_PAGES)
_rq.get = _orig_get
os.environ.pop("PROM_API_KEY", None)

# returned_fate: збійна форма даних → «нема даних», а не виняток
vf.urllib.request.urlopen = lambda req, timeout=0: _RS(json.dumps({"_readded_at": ["не", "словник"]}).encode("utf-8"))
_f = vf.returned_fate({"a": {}}, vf.datetime(2026, 10, 9, 12, 0, 0))
_check("6o. _readded_at не словник → «нема даних: помилка розбору», числа None", (_f["ret_total"], _f["ret_note"].startswith("нема даних")), (None, True))
vf.urllib.request.urlopen = lambda req, timeout=0: _RS(json.dumps([1, 2]).encode("utf-8"))
_f = vf.returned_fate({"a": {}}, vf.datetime(2026, 10, 9, 12, 0, 0))
_check("6p. state — список → «нема даних»", (_f["ret_total"], _f["ret_note"].startswith("нема даних")), (None, True))
vf.urllib.request.urlopen = _orig_open

# вікно «>24 год» (RET_MIN_HOURS): 23 год ще не рахуємо, 25 — вже
_st24 = {"_readded_at": {"a": "2026-10-08T13:00:00", "b": "2026-10-08T11:00:00"}, "_readd_failed": {}}   # 23 год і 25 год до 2026-10-09 12:00
vf.urllib.request.urlopen = lambda req, timeout=0: _RS(json.dumps(_st24).encode("utf-8"))
_f = vf.returned_fate({}, vf.datetime(2026, 10, 9, 12, 0, 0))
_check("6q. повернена 23 год тому не рахується, 25 год — рахується (RET_MIN_HOURS=24)", _f["ret_total"], 1)
vf.urllib.request.urlopen = _orig_open

# ротація: якщо rename не вдався — рядок НЕ дописується під чужий заголовок
_tmp3 = Path(tempfile.mkdtemp())
vf.CSV_FILE = _tmp3 / "vitrine_funnel.csv"
vf.CSV_FILE.write_text("at,total\n2026-10-09,1\n", encoding="utf-8")
_orig_rename = Path.rename
Path.rename = lambda self, target: (_ for _ in ()).throw(PermissionError("locked"))
vf.last_orders = lambda: dict(_NO_ORD)
vf.returned_fate = lambda cat: dict(_NO_RET)
vf.feed_offer_count = lambda: 100
vf.page_buyable = lambda url: (True, True)
vf.record({"1": {"presence": "avail", "in_running_cpa": True, "view_catalog_url": "https://x"}}, sample_n=1, alert=False)
Path.rename = _orig_rename
_check("6r. ротація впала → файл не змінено (дані не лягли під старий заголовок)", vf.CSV_FILE.read_text(encoding="utf-8"), "at,total\n2026-10-09,1\n")

# prev: розрив беремо з останнього ВІДОМОГО рядка (якщо останній запуск без даних про замовлення)
_tmp4 = Path(tempfile.mkdtemp())
vf.CSV_FILE = _tmp4 / "vitrine_funnel.csv"
with open(vf.CSV_FILE, "w", encoding="utf-8", newline="") as _fh:
    _w = csv.DictWriter(_fh, fieldnames=vf.COLUMNS)
    _w.writeheader()
    _w.writerow({"at": "a", "avail": 10, "days_since_order": 16})
    _w.writerow({"at": "b", "avail": 10, "days_since_order": ""})
_check("6s. prev.days_since_order = 16 з попереднього рядка (останній — без даних)", vf._prev_row()["days_since_order"], 16)

print()
if _FAILS:
    print("FAILED:", _FAILS)
    sys.exit(1)
print("ALL OK")
