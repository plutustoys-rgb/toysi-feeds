#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rt_prom_cleanup.py — автоприбирання осиротілих rt-карток Prom (rt_prom_cleanup.py). Офлайн: Prom API, Telegram, мережа — лише моки/заглушки; файли — у тимчасовій теці."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
for _k in ("PROM_RT_CLEANUP", "PROM_RT_CLEANUP_MAX", "PROM_RT_CLEANUP_MIN_HOURS", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.pop(_k, None)
sys.path.insert(0, str(Path(__file__).parent))
import requests  # noqa: E402
import rt_prom  # noqa: E402
import rt_prom_cleanup as rc  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


tmp = Path(tempfile.mkdtemp())
ALERTS = []
rc._alert = lambda key, text: ALERTS.append((key, text))          # без Telegram
NOW = datetime(2026, 11, 25, 12, 0, 0)
OLD = (NOW - timedelta(hours=60)).isoformat(timespec="seconds")   # осиротілий 60 год
YOUNG = (NOW - timedelta(hours=10)).isoformat(timespec="seconds")
_n = [0]


def snap(**kw):
    return {"name": "Товар", "price": 100, "cat": "2606", **kw}


def write_feed(path, active=("rt-active",), stubs=(), toysi=("500", "501"), age_h=1.0):
    parts = ['<?xml version="1.0" encoding="utf-8"?><yml_catalog><shop><offers>']
    parts += [f'<offer id="{i}" available="true"><name>x</name></offer>' for i in toysi]
    parts += [f'<offer id="{i}" available="true"><name>x</name></offer>' for i in active]
    parts += [f'<offer id="{i}" available="false"><name>x</name></offer>' for i in stubs]
    parts.append("</offers></shop></yml_catalog>")
    Path(path).write_text("".join(parts), encoding="utf-8")
    t = (NOW - timedelta(hours=age_h)).timestamp()
    os.utime(path, (t, t))


def setup(listed, deleted=None, feed_kw=None):
    """Новий ізольований стан: журнал, (необов'язково) надгробки, фід."""
    _n[0] += 1
    rt_prom.LISTED_FILE = tmp / f"listed_{_n[0]}.json"
    rt_prom.DELETED_FILE = tmp / f"deleted_{_n[0]}.json"
    rc.FEED_FILE = tmp / f"feed_{_n[0]}.xml"
    rt_prom._atomic_write(rt_prom.LISTED_FILE, {"v": 1, "listed": listed})
    if deleted is not None:
        rt_prom._atomic_write(rt_prom.DELETED_FILE, {"v": 1, "deleted": deleted})
    kw = {"stubs": [k for k in listed if k.startswith("rt-")]}
    kw.update(feed_kw or {})
    write_feed(rc.FEED_FILE, **kw)
    ALERTS.clear()


class Mock:
    """Мок Prom: lookup (by_external_id) і delist; фіксує виклики."""

    def __init__(self, cards=None, indeterminate=(), fail_delist=()):
        self.cards = cards or {}
        self.indet = set(indeterminate)
        self.fail = set(fail_delist)
        self.lookups, self.delisted = [], []

    def lookup(self, ids):
        self.lookups.append(set(ids))
        return {i: self.cards[i] for i in ids if i in self.cards and i not in self.indet}, {i for i in ids if i in self.indet}

    def delist(self, ids):
        ok, err = [], {}
        for i in ids:
            if i in self.fail:
                err[i] = "Prom не підтвердив"
            else:
                ok.append(i)
                self.delisted.append(i)
                self.cards.pop(i, None)                      # як у Prom: видалена картка → 404 за external_id
        return ok, err


def card(rid, **kw):
    return {"id": 9000 + abs(hash(rid)) % 1000, "external_id": rid, "status": "on_display", "presence": "not_available", "quantity_in_stock": 0, **kw}


def go(m, apply=True, orders=lambda: set(), **kw):
    return rc.run(apply, m.lookup, m.delist, now=NOW, orders_fetch=orders, **kw)


NOGATE_NOTE = "PROM_RT_CLEANUP"

# ─────────── брама ───────────
setup({"rt-1": snap(orphan_since=OLD)})
m = Mock({"rt-1": card("rt-1")})
r = go(m)
chk("брама вимкнена (за замовчуванням) + apply → нічого: status=gate_off, жодного lookup/delist, журнал цілий", r["status"] == "gate_off" and not m.lookups and not m.delisted and "rt-1" in rt_prom.load_listed())
chk("gate_on(): лише PROM_RT_CLEANUP=1", not rc.gate_on() and (os.environ.__setitem__("PROM_RT_CLEANUP", "1") or rc.gate_on()) and (os.environ.__setitem__("PROM_RT_CLEANUP", "true") or not rc.gate_on()))
os.environ["PROM_RT_CLEANUP"] = "1"

# ─────────── кандидати за критеріями (чиста функція) ───────────
listed = {
    "rt-old": snap(orphan_since=OLD),              # кандидат
    "rt-young": snap(orphan_since=YOUNG),          # < 48 год
    "rt-nomark": snap(),                           # мітки нема (старий журнал)
    "rt-badmark": snap(orphan_since="сміття"),     # невалідна мітка
    "rt-active": snap(orphan_since=OLD),           # активний у фіді (мітка застаріла) → не кандидат
    "rt-dead": snap(orphan_since=OLD),             # є надгробок
    "100": snap(orphan_since=OLD),                 # Toysi-id
    "rt": snap(orphan_since=OLD),                  # не rt-<id>
    "RT-5": snap(orphan_since=OLD),                # чужий регістр / не наш формат
    "rt-x y": snap(orphan_since=OLD),              # недопустимі символи
    "prom-77": snap(orphan_since=OLD),
}
cands, why = rc.select_candidates(listed, {"rt-active"}, NOW, 48, {"rt-dead": "2026-11-24T00:00:00"})
chk("критерії: кандидат лише rt-old (молодий, без мітки, з битою міткою, активний у фіді, з надгробком, Toysi-id та не-rt id — відсіяні)", cands == ["rt-old"])
chk("відсів порахований за причинами", why["too_young"] == 1 and why["no_orphan_since"] == 2 and why["active_in_feed"] == 1 and why["tombstoned"] == 1 and why["not_rt_id"] == 5)
cands, _ = rc.select_candidates({"rt-b": snap(orphan_since=(NOW - timedelta(hours=30)).isoformat()), "rt-a": snap(orphan_since=OLD)}, set(), NOW, 48)
chk("поріг 48 год: 30 год — ні; порядок — найдавніші першими", cands == ["rt-a"])
os.environ["PROM_RT_CLEANUP_MIN_HOURS"] = "1"
chk("env PROM_RT_CLEANUP_MIN_HOURS не знижує поріг нижче 24 год; 72 → 72; сміття → 48", rc.min_orphan_hours() == 24.0 and (os.environ.__setitem__("PROM_RT_CLEANUP_MIN_HOURS", "72") or rc.min_orphan_hours() == 72.0) and (os.environ.__setitem__("PROM_RT_CLEANUP_MIN_HOURS", "x") or rc.min_orphan_hours() == 48.0))
os.environ.pop("PROM_RT_CLEANUP_MIN_HOURS")

# ─────────── dry: нічого не пише й не видаляє ───────────
setup({"rt-1": snap(orphan_since=OLD), "rt-2": snap(orphan_since=YOUNG), "rt-active": snap()})
m = Mock({"rt-1": card("rt-1")})
before = rt_prom.LISTED_FILE.read_text(encoding="utf-8")
r = go(m, apply=False)
chk("dry: показав би rt-1, delist НЕ викликано, журнал і надгробки не змінені, алертів нема",
    r["status"] == "dry" and r["list"] == ["rt-1"] and not m.delisted and rt_prom.LISTED_FILE.read_text(encoding="utf-8") == before and not rt_prom.DELETED_FILE.exists() and not ALERTS)
os.environ.pop("PROM_RT_CLEANUP")
chk("dry працює і при вимкненій брамі (читання), apply — ні", go(m, apply=False)["status"] == "dry" and go(m, apply=True)["status"] == "gate_off")
os.environ["PROM_RT_CLEANUP"] = "1"
chk("CLI: --apply без брами відхилено кодом 2", (os.environ.pop("PROM_RT_CLEANUP") and False) or rc.main(["--apply"]) == 2)
os.environ["PROM_RT_CLEANUP"] = "1"

# ─────────── apply: видалення, журнал, надгробок, ідемпотентність, Toysi-id недоторкані ───────────
setup({"rt-1": snap(orphan_since=OLD), "rt-2": snap(orphan_since=YOUNG), "rt-active": snap(), "100": snap(orphan_since=OLD)})
m = Mock({"rt-1": card("rt-1"), "100": card("100"), "rt-2": card("rt-2")})
r = go(m)
j = rt_prom.load_listed()
chk("apply: видалено лише rt-1 (молодий rt-2, активний, Toysi-id «100» — недоторкані; «100» навіть не звірявся)", m.delisted == ["rt-1"] and r["status"] == "applied" and r["deleted"] == 1 and all("100" not in s for s in m.lookups))
chk("журнал: rt-1 прибрано (облік осиротілих зменшився), решта на місці; надгробок rt-1 записано", "rt-1" not in j and set(j) == {"rt-2", "rt-active", "100"} and "rt-1" in rt_prom.load_deleted() and r["journal_removed"] == 1)
m2 = Mock({})                                     # у Prom rt-1 уже видалена → 404
r2 = go(m2)
chk("ідемпотентність: другий прогін — нічого видаляти (delist не викликано), журнал той самий", not m2.delisted and r2.get("deleted", 0) == 0 and set(rt_prom.load_listed()) == {"rt-2", "rt-active", "100"})
chk("надгробок свіжий: перевірка «ожила?» — rt-1 звірено ще раз (lookup містить rt-1); 404 → не чіпаємо", any("rt-1" in s for s in m2.lookups))
m3 = Mock({"rt-1": card("rt-1")})        # картка раптом ожила (старий опублікований фід відновив її)
r3 = go(m3)
chk("надгробок < 3 діб: якщо картка ожила — видаляється повторно (ті самі запобіжники), без дубля в журналі", m3.delisted == ["rt-1"])
# надгробок старший за 3 доби — не перевіряємо
setup({"rt-9": snap()}, deleted={"rt-1": (NOW - timedelta(days=5)).isoformat()})
m = Mock({"rt-1": card("rt-1")})
go(m)
chk("надгробок > 3 діб: повторна звірка не робиться", not m.lookups and not m.delisted)

# ─────────── кап ───────────
setup({f"rt-{i:02d}": snap(orphan_since=(NOW - timedelta(hours=100 + i)).isoformat()) for i in range(1, 8)} | {"rt-keep": snap()}, feed_kw={"active": ["rt-keep"]})
m = Mock({f"rt-{i:02d}": card(f"rt-{i:02d}") for i in range(1, 8)})
os.environ["PROM_RT_CLEANUP_MAX"] = "3"
r = go(m)
chk("кап PROM_RT_CLEANUP_MAX=3: видалено рівно 3 (найдавніші осиротілі — rt-07, rt-06, rt-05), решта відкладена; журнал: 4 + keep", len(m.delisted) == 3 and m.delisted == ["rt-07", "rt-06", "rt-05"] and r["deferred"] == 4 and len(rt_prom.load_listed()) == 5)
os.environ.pop("PROM_RT_CLEANUP_MAX")
chk("кап за замовчуванням 50; стеля 200; 0 дозволено (вимкнути видалення)", rc.cap_per_run() == 50 and (os.environ.__setitem__("PROM_RT_CLEANUP_MAX", "9999") or rc.cap_per_run() == 200) and (os.environ.__setitem__("PROM_RT_CLEANUP_MAX", "0") or rc.cap_per_run() == 0))
os.environ.pop("PROM_RT_CLEANUP_MAX")

# ─────────── (в) жива звірка кабінету ───────────
setup({k: snap(orphan_since=OLD) for k in ("rt-ok", "rt-avail", "rt-qty", "rt-ord-ext", "rt-ord-sku", "rt-ord-id", "rt-mismatch", "rt-404", "rt-gone", "rt-pre")} | {"rt-keep": snap()}, feed_kw={"active": ["rt-keep"]})
cards = {"rt-ok": card("rt-ok"), "rt-avail": card("rt-avail", presence="available", quantity_in_stock=5), "rt-qty": card("rt-qty", quantity_in_stock=3),
         "rt-ord-ext": card("rt-ord-ext"), "rt-ord-sku": card("rt-ord-sku", sku="SKU-77"), "rt-ord-id": card("rt-ord-id", id=424242),
         "rt-mismatch": card("rt-mismatch", external_id="rt-other"), "rt-gone": card("rt-gone", status="deleted"), "rt-pre": card("rt-pre", presence="not_available")}
m = Mock(cards)
r = go(m, orders=lambda: {"rt-ord-ext", "SKU-77", "424242"})
chk("звірка: видалено лише чисті картки rt-ok і rt-pre (недоступні, без замовлень)", sorted(m.delisted) == ["rt-ok", "rt-pre"])
chk("звірка: presence=available / залишок>0 / замовлення за external_id, sku, id / розбіжність external_id — заблоковано (картку не чіпаємо)", r["blocked"] == {"presence_available": 1, "has_quantity": 1, "has_orders": 3, "external_id_mismatch": 1})
j = rt_prom.load_listed()
chk("404 і status=deleted → без delist лише прибрані з журналу (rt-404, rt-gone); заблоковані лишаються в журналі", "rt-404" not in j and "rt-gone" not in j and all(k in j for k in ("rt-avail", "rt-qty", "rt-ord-ext", "rt-ord-sku", "rt-ord-id", "rt-mismatch")))

# ─────────── замовлення: fetch_protected_keys ───────────
class Resp:
    def __init__(self, data, code=200):
        self._d, self.status_code = data, code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code))

    def json(self):
        if isinstance(self._d, Exception):
            raise self._d
        return self._d


def pages(*ps):
    it = iter(ps)
    calls = []

    def g(url, headers=None, params=None, timeout=None):
        calls.append(dict(params))
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    g.calls = calls
    return g


O = lambda i, st, *prods: {"id": i, "status": st, "products": [dict(p) for p in prods]}  # noqa: E731
g = pages(Resp({"orders": [O(1, "pending", {"external_id": "rt-a", "sku": "rt-a", "id": 11}), O(2, "canceled", {"external_id": "rt-c"}), O(3, "delivered", {"external_id": "rt-d"})]}))
keys = rc.fetch_protected_keys(NOW, http_get=g, api_key="k")
chk("замовлення: pending і delivered захищають, canceled — ні; ключі external_id/sku/id", keys == {"rt-a", "11", "rt-d"} and "rt-c" not in keys and g.calls[0]["date_from"] == "2026-09-26T12:00:00")
full = [O(i, "pending", {"external_id": f"x{i}"}) for i in range(100)]
g = pages(Resp({"orders": full}), Resp({"orders": [O(200, "received", {"external_id": "rt-last"})]}))
keys = rc.fetch_protected_keys(NOW, http_get=g, api_key="k")
chk("замовлення: пагінація last_id (друга сторінка запитана з last_id=99), rt-last враховано", "rt-last" in keys and g.calls[1]["last_id"] == 99 and len(g.calls) == 2)


def aborts(fn):
    try:
        fn()
        return False
    except rc.CleanupAbort:
        return True


chk("замовлення: мережева помилка / HTTP 500 / не-JSON / відповідь без списку / нема ключа → CleanupAbort (fail-closed)",
    aborts(lambda: rc.fetch_protected_keys(NOW, http_get=pages(requests.exceptions.ConnectionError("x")), api_key="k"))
    and aborts(lambda: rc.fetch_protected_keys(NOW, http_get=pages(Resp({}, 500)), api_key="k"))
    and aborts(lambda: rc.fetch_protected_keys(NOW, http_get=pages(Resp(ValueError("bad"))), api_key="k"))
    and aborts(lambda: rc.fetch_protected_keys(NOW, http_get=pages(Resp({"orders": None})), api_key="k"))
    and aborts(lambda: rc.fetch_protected_keys(NOW, http_get=pages(Resp({"orders": []})), api_key="")))
rc_pages, rc.ORDER_MAX_PAGES = rc.ORDER_MAX_PAGES, 2
chk("замовлення: ліміт сторінок вичерпано при повних сторінках → CleanupAbort (повного списку не маємо)", aborts(lambda: rc.fetch_protected_keys(NOW, http_get=pages(Resp({"orders": full}), Resp({"orders": full})), api_key="k")))
rc.ORDER_MAX_PAGES = rc_pages

# ─────────── fail-closed: збої → нічого не видаляємо + алерт ───────────
def failclosed(name, setup_args, mock, expect, **kw):
    setup(*setup_args[0], **setup_args[1])
    r = go(mock, **kw)
    chk(f"fail-closed: {name} → aborted, нічого не видалено, журнал цілий, алерт надіслано", r["status"] == "aborted" and not mock.delisted and expect(r) and ALERTS and set(rt_prom.load_listed()) == set(setup_args[0][0]))


L = {"rt-1": snap(orphan_since=OLD), "rt-2": snap(orphan_since=OLD), "rt-keep": snap()}
fk = {"feed_kw": {"active": ["rt-keep"]}}
failclosed("Prom API недоступний для звірки (усі indeterminate)", ((L,), fk), Mock({"rt-1": card("rt-1"), "rt-2": card("rt-2")}, indeterminate={"rt-1", "rt-2"}), lambda r: "неповна" in r["reason"])
failclosed("збій отримання замовлень", ((L,), fk), Mock({"rt-1": card("rt-1"), "rt-2": card("rt-2")}), lambda r: "замовлен" in r["reason"], orders=lambda: (_ for _ in ()).throw(rc.CleanupAbort("замовлення: мережа")))
failclosed("несподіваний виняток у lookup", ((L,), fk), type("M", (Mock,), {"lookup": lambda self, ids: (_ for _ in ()).throw(RuntimeError("boom"))})(), lambda r: "RuntimeError" in r["reason"])
# одна невизначена з 20 (5%) — пропускається точково, решта обробляється
big = {f"rt-{i:02d}": snap(orphan_since=OLD) for i in range(20)} | {"rt-keep": snap()}
setup(big, feed_kw={"active": ["rt-keep"]})
m = Mock({k: card(k) for k in big if k != "rt-keep"}, indeterminate={"rt-00"})
r = go(m)
chk("одна невизначена із 20 (5% ≤ 10%) — лише вона пропущена й лишається в журналі; решта 19 видалено", len(m.delisted) == 19 and "rt-00" not in m.delisted and "rt-00" in rt_prom.load_listed())
# delist-помилки
setup({"rt-1": snap(orphan_since=OLD), "rt-2": snap(orphan_since=OLD), "rt-keep": snap()}, feed_kw={"active": ["rt-keep"]})
m = Mock({"rt-1": card("rt-1"), "rt-2": card("rt-2")}, fail_delist={"rt-2"})
r = go(m)
chk("збій delist для rt-2: він ЛИШАЄТЬСЯ в журналі й без надгробка (повторимо), rt-1 прибрано; алерт про помилки", m.delisted == ["rt-1"] and "rt-2" in rt_prom.load_listed() and "rt-2" not in rt_prom.load_deleted() and r["errors"] == 1 and any(k == "errors" for k, _ in ALERTS))

# журнал/надгробки/фід
setup({"rt-1": snap(orphan_since=OLD)})
rt_prom.LISTED_FILE.unlink()
m = Mock({"rt-1": card("rt-1")})
r = go(m)
chk("журнал відсутній → нічого (aborted + алерт), без жодного звернення до Prom", r["status"] == "aborted" and not m.lookups and not m.delisted and ALERTS)
setup({"rt-1": snap(orphan_since=OLD)})
rt_prom.LISTED_FILE.write_text("{не json", encoding="utf-8")
m = Mock({"rt-1": card("rt-1")})
r = go(m)
chk("журнал пошкоджений → нічого; файл не перезаписано", r["status"] == "aborted" and not m.lookups and rt_prom.LISTED_FILE.read_text(encoding="utf-8") == "{не json")
setup({"rt-1": snap(orphan_since=OLD)})
rt_prom.DELETED_FILE.write_text("{ні", encoding="utf-8")
m = Mock({"rt-1": card("rt-1")})
chk("файл надгробків пошкоджений → нічого", go(m)["status"] == "aborted" and not m.delisted)
setup({"rt-1": snap(orphan_since=OLD)}, feed_kw={"active": ["rt-keep"]})
rc.FEED_FILE.unlink()
m = Mock({"rt-1": card("rt-1")})
chk("фід відсутній → нічого", go(m)["status"] == "aborted" and not m.delisted)
setup({"rt-1": snap(orphan_since=OLD)}, feed_kw={"active": ["rt-keep"], "age_h": 20})
m = Mock({"rt-1": card("rt-1")})
chk("фід старший за 14 год → нічого", go(m)["status"] == "aborted" and not m.delisted and "застарів" in go(m)["reason"])
setup({"rt-1": snap(orphan_since=OLD)}, feed_kw={"active": []})
m = Mock({"rt-1": card("rt-1")})
chk("у фіді нема жодного активного rt- (RT-хук не відпрацював/брама PROM_RT_IN_FEED вимкнена) → нічого", go(m)["status"] == "aborted" and not m.delisted)
setup({"rt-1": snap(orphan_since=OLD)}, feed_kw={"active": ["rt-keep"]})
rc.FEED_FILE.write_text(rc.FEED_FILE.read_text(encoding="utf-8")[:-40], encoding="utf-8")
m = Mock({"rt-1": card("rt-1")})
chk("фід обрізаний (не розбирається) → нічого", go(m)["status"] == "aborted" and not m.delisted)
setup({"rt-1": snap(orphan_since=OLD), "rt-active": snap(orphan_since=OLD)}, feed_kw={"active": ["rt-active"]})
m = Mock({"rt-1": card("rt-1"), "rt-active": card("rt-active")})
go(m)
chk("rt-id, що є АКТИВНИМ у поточному фіді, не видаляється, навіть із давньою міткою orphan_since", m.delisted == ["rt-1"])

# мас-запобіжник
mass = {f"rt-{i:03d}": snap(orphan_since=OLD) for i in range(120)} | {"rt-keep": snap()}
setup(mass, feed_kw={"active": ["rt-keep"]})
m = Mock({k: card(k) for k in mass})
r = go(m)
chk("мас-запобіжник: 120 кандидатів із 121 (>50%) → нічого, алерт «збій відбору»", r["status"] == "aborted" and not m.delisted and not m.lookups and ALERTS)
r = go(m, allow_mass=True)
chk("--allow-mass (огляд людиною) обходить запобіжник; кап 50 діє", len(m.delisted) == 50 and r["status"] == "applied")

# порожньо
setup({"rt-keep": snap()}, feed_kw={"active": ["rt-keep"]})
chk("журнал без осиротілих → status=nothing, нічого не звірялось", go(Mock())["status"] == "nothing")

# ─────────── інтеграція з prom_catalog_sync ───────────
src = Path(__file__).with_name("prom_catalog_sync.py").read_text(encoding="utf-8")
chk("prom_catalog_sync: run_rt_orphan_cleanup викликається в main() до find_stale_external_ids і використовує fetch_prom_products_by_external_ids + deactivate",
    "run_rt_orphan_cleanup(bool(args.apply))" in src and src.index("run_rt_orphan_cleanup(bool(args.apply))") < src.index("stale_ids = find_stale_external_ids(prom_products") and "rt_prom_cleanup.run(apply, fetch_prom_products_by_external_ids, deactivate)" in src)
os.environ.pop("PROM_RT_CLEANUP", None)
try:
    import prom_catalog_sync as pcs
    chk("prom_catalog_sync.run_rt_orphan_cleanup при вимкненій брамі → None (нічого не читає й не пише)", pcs.run_rt_orphan_cleanup(True) is None and pcs.run_rt_orphan_cleanup(False) is None)
except ImportError as e:
    print(f"[SKIP] prom_catalog_sync не імпортується в цьому середовищі: {e}")

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
