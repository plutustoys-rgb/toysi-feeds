#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_gmc_price_competitiveness.py — мітка g:custom_label_1 = vyhidni/ni (рішення власника 13.11 «Реклама лише вигідних»).
Без мережі: сесія Merchant API підмінена. `python test_gmc_price_competitiveness.py` → exit 0/1."""
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
import gmc_price_competitiveness as g  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


alerts = []
g._alert = lambda k, t: alerts.append(k)


def P(units, cur="UAH"):
    return {"amountMicros": str(int(units * 1_000_000)), "currencyCode": cur}


def row(oid, price, bench, country="UA", cur="UAH", bcur=None):
    r = {"offerId": oid, "reportCountryCode": country, "id": f"online~uk~UA~{oid}"}
    if price is not None:
        r["price"] = P(price, cur)
    if bench is not None:
        r["benchmarkPrice"] = P(bench, bcur or cur)
    return r


# ── правило
labels, st = g.classify([row("a", 100, 120), row("b", 100, 100), row("c", 130, 120), row("d", 100, None), row("e", None, 100), row("f", 100, 120, bcur="EUR")])
chk("ціна < контрольної → vyhidni; ціна = контрольній → vyhidni (ціна ≤ benchmark)", labels["a"] == "vyhidni" and labels["b"] == "vyhidni")
chk("дорожче за контрольну → ni", labels["c"] == "ni")
chk("без контрольної ціни → ni (доведено вигідний ≠ не доведено протилежне)", labels["d"] == "ni")
chk("без нашої ціни → ni; інша валюта → ni (не порівнюємо різні валюти)", labels["e"] == "ni" and labels["f"] == "ni")
chk("зведення: units=6 vyhidni=2 ni_pricier=1 ni_no_benchmark=3", st["units"] == 6 and st["vyhidni"] == 2 and st["ni_pricier"] == 1 and st["ni_no_benchmark"] == 3)

# ── одиниця: uk+ru рядки одного товару = один товар
labels2, st2 = g.classify([row("x", 100, 120), row("x", 100, 120), row("y", 100, 120), row("y", 130, 120), row("z", 100, None), row("z", 100, 120)])
chk("два рядки (uk+ru) одного товару — ОДИН товар (units=3, не 6)", st2["units"] == 3 and st2["rows"] == 6)
chk("кілька рядків: vyhidni лише коли жоден рядок із контрольною ціною не дорожчий (x — так, y — ні; z — один рядок без контрольної, інший вигідний → vyhidni)", labels2 == {"x": "vyhidni", "y": "ni", "z": "vyhidni"})
labels3, st3 = g.classify([row("u", 100, 120, country="PL"), row("v", 100, 120), {"price": P(1), "reportCountryCode": "UA"}])
chk("країна ≠ UA пропускається; рядок без offerId пропускається й рахується", "u" not in labels3 and st3["rows_other_country"] == 1 and st3["rows_no_offer"] == 1 and labels3 == {"v": "vyhidni"})
chk("порожній звіт → порожні мітки", g.classify([])[0] == {} and g.classify([])[1]["units"] == 0)

# ── API: пагінація, кандидати запиту, зведення
class Resp:
    def __init__(self, d, code=200, text=""):
        self.d, self.code, self.text = d, code, text

    def raise_for_status(self):
        if self.code >= 400:
            e = RuntimeError(f"HTTP {self.code}")
            e.response = self
            raise e

    def json(self):
        return self.d


class S:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return self.handler(len(self.calls), json)


def wrap(r):
    return {"priceCompetitivenessProductView": r}


def h_ok(n, body):
    if "pageToken" not in body:
        return Resp({"results": [wrap(row("a", 100, 120)), wrap(row("b", 130, 120))], "nextPageToken": "T2"})
    return Resp({"results": [wrap(row("c", 90, None))]})


s = S(h_ok)
rows, variant = g.fetch_any(s)
chk("пагінація nextPageToken: усі 3 рядки зібрано, 2 запити; перший варіант таблиці спрацював", len(rows) == 3 and len(s.calls) == 2 and variant == "price_competitiveness_product_view")
chk("запит: POST reports:search акаунта, SELECT з id, offer_id, report_country_code, price, benchmark_price FROM таблиці (id і країна — обов'язкові в SELECT за документацією)",
    s.calls[0][0].endswith("/accounts/5824264949/reports:search") and all(f in s.calls[0][1]["query"] for f in ("id", "offer_id", "report_country_code", "price", "benchmark_price")) and "FROM price_competitiveness_product_view" in s.calls[0][1]["query"])


def h_second(n, body):
    if "price_competitiveness_product_view" in body["query"]:
        return Resp({}, 400, "INVALID table")
    return Resp({"results": [wrap(row("a", 100, 120))]})


s = S(h_second)
rows, variant = g.fetch_any(s)
chk("перший варіант таблиці відхилено (400) → пробується другий; назва вдалого запам'ятовується", variant == "PriceCompetitivenessProductView" and len(rows) == 1)
s = S(lambda n, b: Resp({}, 403, "no scope"))
try:
    g.fetch_any(s)
    ok = False
except RuntimeError as e:
    ok = "403" in str(e) and "no scope" in str(e)
chk("усі варіанти відхилено → RuntimeError із відповіддю API (для алерту/каналу)", ok)

# ── знімок: запис, свіжість, відмова не перезаписує
tmp = Path(tempfile.mkdtemp()) / "snap.json"
NOW = datetime(2026, 11, 13, 12, 0, 0)
rc = g.run(session=S(h_ok), now=NOW, path=tmp)
d = json.loads(tmp.read_text(encoding="utf-8"))
chk("run: знімок записано (labels, stats, date, query_variant), код 0", rc == 0 and d["labels"] == {"a": "vyhidni", "b": "ni", "c": "ni"} and d["stats"]["units"] == 3 and d["date"] == "2026-11-13" and d["query_variant"])
chk("load_labels: свіжий знімок читається", g.load_labels(tmp, now=NOW + timedelta(days=1)) == d["labels"])
chk("load_labels: старший за 7 діб → {} (застарілі мітки не ставимо)", g.load_labels(tmp, now=NOW + timedelta(days=8)) == {})
chk("load_labels: нема файлу / битий файл → {} без винятку", g.load_labels(Path(tempfile.mkdtemp()) / "none.json") == {} and g.load_labels(Path(__file__)) == {})
before = tmp.read_text(encoding="utf-8")
alerts.clear()
rc = g.run(session=S(lambda n, b: Resp({}, 403, "no scope")), now=NOW, path=tmp)
chk("збій API: код 1, алерт, попередній знімок НЕ перезаписано", rc == 1 and "gmc_price_comp_fail" in alerts and tmp.read_text(encoding="utf-8") == before)
alerts.clear()
rc = g.run(session=S(lambda n, b: Resp({"results": []})), now=NOW, path=tmp)
chk("порожній звіт: код 1, алерт, знімок не перезаписано", rc == 1 and "gmc_price_comp_empty" in alerts and tmp.read_text(encoding="utf-8") == before)
txt = g.summary_text(d["stats"], d["date"], d["query_variant"])
chk("зведення називає одиницю (товар offerId), дату знімка і три числа", "ОДИНИЦЯ — товар" in txt and "2026-11-13" in txt and "vyhidni" in txt and "контрольної ціни нема" in txt)

# ── refresh_if_stale
calls = []
g._REFRESH_DONE = False
g.refresh_if_stale(now=NOW + timedelta(hours=1), path=tmp, runner=lambda: calls.append(1))
chk("refresh_if_stale: знімок свіжий (<20 год) → не оновлюємо", calls == [])
g._REFRESH_DONE = False
g.refresh_if_stale(now=NOW + timedelta(hours=30), path=tmp, runner=lambda: calls.append(1))
chk("refresh_if_stale: знімок старий → оновлюємо раз", calls == [1])
g.refresh_if_stale(now=NOW + timedelta(hours=30), path=tmp, runner=lambda: calls.append(1))
chk("refresh_if_stale: раз за процес", calls == [1])
g._REFRESH_DONE = False


def boom():
    raise SystemExit("нема google-auth")


g.refresh_if_stale(now=NOW + timedelta(hours=30), path=tmp, runner=boom)
chk("refresh_if_stale: SystemExit/виняток у runner не валить виклик (фід-генерація)", True)
g._REFRESH_DONE = True

# ── фід: _build_xml ставить custom_label_1, custom_label_0 лишається
import generate_google_feed as gf  # noqa: E402


def item(pid, price="100.00 UAH", band="100-250"):
    return {"id": pid, "title": f"Товар {pid}", "description": "d", "link": f"https://plutustoys.com.ua/product-{pid}.html", "image_link": "https://plutustoys.com.ua/i.jpg", "price": price,
            "availability": "in_stock", "condition": "new", "brand": "B", "google_product_category": "1", "category_name": "Іграшки", "price_band": band}


gf._price_labels = lambda: {"1": "vyhidni", "2": "ni"}
root = gf._build_xml([item("1"), item("2"), item("3")])
NS = "{http://base.google.com/ns/1.0}"
ent = {e.findtext(NS + "id"): e for e in root.iter("item")}
chk("XML: товар 1 → vyhidni, 2 → ni, 3 (нема в свіжому знімку = контрольної ціни нема) → ni", ent["1"].findtext(NS + "custom_label_1") == "vyhidni" and ent["2"].findtext(NS + "custom_label_1") == "ni" and ent["3"].findtext(NS + "custom_label_1") == "ni")
chk("XML: custom_label_0 (цінова смуга) не зачеплено", all(e.findtext(NS + "custom_label_0") == "100-250" for e in ent.values()))
gf._price_labels = lambda: {}
root = gf._build_xml([item("1")])
chk("нема міток (знімка нема/старий) → фід без custom_label_1, як було", next(root.iter("item")).find(NS + "custom_label_1") is None)
import importlib  # noqa: E402
importlib.reload(gf)
orig = g.labels_for_feed
g.labels_for_feed = lambda: (_ for _ in ()).throw(RuntimeError("x"))
chk("_price_labels: збій модуля мітки → {} без винятку (фід не падає)", gf._price_labels() == {})
g.labels_for_feed = orig

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
