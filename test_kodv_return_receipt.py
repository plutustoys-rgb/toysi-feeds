# -*- coding: utf-8 -*-
"""Регрес kodv_return_receipt.py — БЕЗ мережі (усі виклики Checkbox підміняються). Дані ВИГАДАНІ (репо публічний —
жодних реальних телефонів/ідентифікаторів покупців). Перевіряє відмови, тіло запиту, пошук по вікнах, дубль (у т.ч.
повторну перевірку перед POST), замок, обов'язковість --expect-fiscal для живого запуску, ізоляцію dry-run,
обробку помилок і опитування."""
import copy
import io
import contextlib
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import checkbox_client as cb
import kodv_return_receipt as k

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

SELL = {
    "id": "00000000-0000-4000-8000-000000000056", "serial": 56, "type": "SELL", "status": "DONE", "is_test": False,
    "fiscal_code": "TESTFISCAL56", "total_sum": 48100, "created_at": "2026-09-08T07:38:32+00:00",
    "goods": [{"good": {"code": "100001", "name": 'Тестова гра "Око" (укр)', "price": 48100}, "quantity": 1000, "is_return": False,
               "taxes": [], "discounts": []}],
    "payments": [{"type": "CASHLESS", "value": 48100, "label": "Картка"}],
    "delivery": {"phone": "+380501234567", "email": None},
    "taxes": [], "discounts": [],
}
def mod(**kw):
    r = copy.deepcopy(SELL); r.update(kw); return r
def refuses(sell, allr=None, **kw):
    try:
        k.validate(sell, allr if allr is not None else [sell], **kw); return False
    except k.Refusal:
        return True
def quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rv = fn(*a, **kw)
    return rv, buf.getvalue()

chk("нормальний чек проходить", not refuses(SELL))
chk("відмова: type=RETURN", refuses(mod(type="RETURN")))
chk("відмова: status=ERROR", refuses(mod(status="ERROR")))
chk("відмова: тестовий чек", refuses(mod(is_test=True)))
chk("відмова: 2 товари", refuses(mod(goods=SELL["goods"] * 2)))
chk("відмова: 2 оплати", refuses(mod(payments=SELL["payments"] * 2)))
chk("відмова: оплата ≠ товар", refuses(mod(payments=[{"type": "CASHLESS", "value": 40000}])))
chk("відмова: невідомий тип оплати", refuses(mod(payments=[{"type": "BONUS", "value": 48100}])))
chk("відмова: total_sum ≠ товар", refuses(mod(total_sum=48000)))
g_tax = copy.deepcopy(SELL["goods"]); g_tax[0]["taxes"] = [{"code": 1, "value": 20}]
chk("відмова: податки в рядку товару", refuses(mod(goods=g_tax)))
g_dis = copy.deepcopy(SELL["goods"]); g_dis[0]["discounts"] = [{"type": "DISCOUNT", "value": 100}]
chk("відмова: знижка в рядку товару", refuses(mod(goods=g_dis)))
chk("відмова: податки на рівні чека", refuses(mod(taxes=[{"code": 1}])))
chk("відмова: знижки на рівні чека", refuses(mod(discounts=[{"value": 5}])))
g_nn = copy.deepcopy(SELL["goods"]); g_nn[0]["good"]["name"] = None
chk("відмова: нема назви товару", refuses(mod(goods=g_nn)))
chk("відмова: дубль RETURN (DONE)", refuses(SELL, [SELL, {"type": "RETURN", "related_receipt_id": SELL["id"], "status": "DONE", "serial": 99}]))
chk("відмова: дубль RETURN (CREATED, ще не DONE)", refuses(SELL, [SELL, {"type": "RETURN", "related_receipt_id": SELL["id"], "status": "CREATED", "serial": 99}]))
chk("дозволено: попередній RETURN у ERROR не блокує", not refuses(SELL, [SELL, {"type": "RETURN", "related_receipt_id": SELL["id"], "status": "ERROR"}]))
chk("дозволено: RETURN до ІНШОГО чека не блокує", not refuses(SELL, [SELL, {"type": "RETURN", "related_receipt_id": "other", "status": "DONE"}]))
chk("відмова: --expect-fiscal не збігся", refuses(SELL, expect_fiscal="XXXX"))
chk("ок: --expect-fiscal збігся", not refuses(SELL, expect_fiscal="TESTFISCAL56"))
chk("відмова: --expect-sum не збігся", refuses(SELL, expect_sum=221.0))
chk("ок: --expect-sum збігся", not refuses(SELL, expect_sum=481.0))

b = k.build_body(SELL, new_id="11111111-1111-1111-1111-111111111111")
g = b["goods"][0]
chk("тіло: related_receipt_id = id продажу", b["related_receipt_id"] == SELL["id"])
chk("тіло: is_return=true, ціна й кількість з чека", g["is_return"] is True and g["good"]["price"] == 48100 and g["quantity"] == 1000)
chk("тіло: оплата того ж типу на всю суму", b["payments"] == [{"type": "CASHLESS", "value": 48100}])
chk("тіло: телефон нормалізовано", b.get("delivery") == {"phone": "+380501234567"})
chk("тіло: id заданий/uuid", b["id"] == "11111111-1111-1111-1111-111111111111" and len(k.build_body(SELL)["id"]) == 36)
chk("тіло: без телефону — без delivery", "delivery" not in k.build_body(mod(delivery={"phone": None})))
chk("тіло: не мутує чек продажу", SELL["goods"][0]["is_return"] is False)
m = k._masked(b)
chk("друк: телефон замасковано, тіло запиту не змінене", m["delivery"]["phone"] == "+38050****567" and b["delivery"]["phone"] == "+380501234567")

# ── пошук по вікнах ──
calls = []
def fake_fetch(headers, f, t):
    calls.append((f, t))
    if len(calls) == 1: return [{"serial": 70, "id": "x"}]
    if len(calls) == 2: return [{"serial": 5, "id": "y"}, SELL]
    return []
now = datetime(2026, 10, 5, 12, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
sell, allr = k.find_sell(56, {}, fake_fetch, now)
chk("пошук: знайдено у 2-му вікні, зупинилось", sell["serial"] == 56 and len(calls) == 2)
chk("пошук: зібрано й новіші чеки (для перевірки дубля)", {r["serial"] for r in allr} == {70, 5, 56})
chk("пошук: вікна ≤ 89 днів і суміжні", all((t - f).days <= 90 for f, t in calls) and calls[1][1] == calls[0][0])
try:
    k.find_sell(1, {}, lambda h, f, t: [], now); nf = False
except k.Refusal:
    nf = True
chk("пошук: не знайдено → Refusal", nf)

# ── run(): dry-run, живий шлях, відмови, помилки ──
tmp = Path(tempfile.mkdtemp(prefix="kodv_ret_test_"))
posted, shifts = [], []
def mk_env(**over):
    e = dict(auth=lambda: "tok", fetch_window=lambda h, f, t: [copy.deepcopy(SELL)],
             ensure_shift=lambda t: shifts.append(1),
             post=lambda t, body: (posted.append(body), {"id": body["id"]})[1],
             get=lambda t, rid: {"status": "DONE", "serial": 130, "fiscal_code": "NEW", "total_sum": 48100, "created_at": "2026-10-05T09:00:00+00:00"},
             sleep=lambda s: None, lock_dir=tmp, now=now)
    e.update(over); return e

rc, out = quiet(k.run, 56, True, **mk_env())
chk("dry-run: код 0, НІЧОГО не відправлено, зміна не відкривалась, замка нема", rc == 0 and not posted and not shifts and not list(tmp.iterdir()))
chk("dry-run: телефон у виводі замасковано", "+380501234567" not in out and "****" in out)
rc, _ = quiet(k.run, 56, False, **mk_env())
chk("живий БЕЗ --expect-fiscal: код 2, нічого не відправлено", rc == 2 and not posted)
rc, _ = quiet(k.run, 56, False, "TESTFISCAL56", 481.0, **mk_env())
chk("живий: код 0, рівно 1 тіло, зміну відкрито ДО POST, замок знято", rc == 0 and len(posted) == 1 and posted[0]["goods"][0]["is_return"] and len(shifts) == 1 and not list(tmp.iterdir()))
posted.clear(); shifts.clear()
rc, _ = quiet(k.run, 56, False, "WRONG", **mk_env())
chk("неправильний fiscal: код 2, нічого не відправлено, замка нема", rc == 2 and not posted and not list(tmp.iterdir()))
dup = {"type": "RETURN", "related_receipt_id": SELL["id"], "status": "DONE", "serial": 99}
rc, _ = quiet(k.run, 56, False, "TESTFISCAL56", **mk_env(fetch_window=lambda h, f, t: [copy.deepcopy(SELL), dup]))
chk("дубль: код 2, нічого не відправлено", rc == 2 and not posted)
# дубль, що з'явився МІЖ першим пошуком і POST (повторна перевірка перед POST)
seq = {"n": 0}
def racy(h, f, t):
    seq["n"] += 1
    return [copy.deepcopy(SELL)] if seq["n"] == 1 else [copy.deepcopy(SELL), dup]
rc, _ = quiet(k.run, 56, False, "TESTFISCAL56", **mk_env(fetch_window=racy))
chk("перегони: RETURN з'явився перед POST → код 2, нічого не відправлено, замок знято", rc == 2 and not posted and not list(tmp.iterdir()))

def boom(t, body): raise cb.CheckboxAPIError("422 test")
rc, out = quiet(k.run, 56, False, "TESTFISCAL56", **mk_env(post=boom))
chk("помилка POST: код 3, повідомлення «СТАН НЕВІДОМИЙ», замок ЛИШЕНО", rc == 3 and "НЕВІДОМИЙ" in out and len(list(tmp.iterdir())) == 1)
rc, out = quiet(k.run, 56, False, "TESTFISCAL56", **mk_env())
chk("повторний запуск при замку: код 2 і нічого не відправлено", rc == 2 and not posted and "замок" in out)
for f in tmp.iterdir(): f.unlink()
rc, out = quiet(k.run, 56, False, "TESTFISCAL56", **mk_env(get=lambda t, rid: {"status": "ERROR"}))
chk("статус ERROR: код 3, замок лишено", rc == 3 and len(list(tmp.iterdir())) == 1)
for f in tmp.iterdir(): f.unlink()
rc, _ = quiet(k.run, 56, False, "TESTFISCAL56", **mk_env(get=lambda t, rid: {"status": "CREATED"}))
chk("таймаут опитування: код 3, замок лишено", rc == 3 and len(list(tmp.iterdir())) == 1)
for f in tmp.iterdir(): f.unlink()
def kbi(t, rid): raise KeyboardInterrupt()
rc, out = quiet(k.run, 56, False, "TESTFISCAL56", **mk_env(get=kbi))
chk("Ctrl+C під час опитування: код 3, «СТАН НЕВІДОМИЙ», замок лишено", rc == 3 and "НЕВІДОМИЙ" in out and len(list(tmp.iterdir())) == 1)
for f in tmp.iterdir(): f.unlink()
import requests as _rq
posted.clear()
def httperr(h, f, t): raise _rq.exceptions.HTTPError("400 date.wrong_interval")
rc, out = quiet(k.run, 56, False, "TESTFISCAL56", **mk_env(fetch_window=httperr))
chk("HTTPError пошуку: код 3, «ще НЕ надсилався», нічого не відправлено", rc == 3 and "НЕ надсилався" in out and not posted and not list(tmp.iterdir()))
rc, out = quiet(k.run, 56, False, "TESTFISCAL56", **mk_env(auth=lambda: (_ for _ in ()).throw(cb.CheckboxAPIError("no pin"))))
chk("помилка авторизації: код 3, нічого не надсилалось", rc == 3 and "НЕ надсилався" in out)

# ── опитування: разова помилка GET не обриває, 3 поспіль — обривають ──
state = {"n": 0}
def flaky(t, rid):
    state["n"] += 1
    if state["n"] == 1: raise cb.CheckboxAPIError("5xx")
    return {"status": "DONE", "serial": 1}
chk("опитування: разова помилка GET переживається", k.wait_done("t", "r", flaky, lambda s: None).get("serial") == 1)
def always(t, rid): raise cb.CheckboxAPIError("5xx")
try:
    k.wait_done("t", "r", always, lambda s: None); three = False
except cb.CheckboxAPIError:
    three = True
chk("опитування: 3 помилки поспіль → виняток", three)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ kodv_return_receipt — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
