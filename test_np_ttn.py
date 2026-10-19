#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_np_ttn.py — модуль створення ТТН НП (np_ttn.py): payload, ворота реальних дій, розрахунок без створення, маркування.
Інваріанти: (1) нормалізація телефону і розбір ПІБ; (2) місця: по одному на КОЖНУ ПОЗИЦІЮ (вимога RoyalToys «на кожен товар по одному місцю»; штуки одного товару = одне місце, вага × кількість), оголошена вартість ≥ 200; (3) payload: платник одержувач, WarehouseWarehouse, відправник — наш контрагент + с. Мила №2 (Refs), БЕЗ зворотної доставки для передплати; зворотна доставка (накладений платіж) — цілі гривні;
(4) за замовчуванням dry_run: НЕ викликає Counterparty.save/InternetDocument.save/delete, питає лише getDocumentPrice; (5) apply=True БЕЗ NP_TTN_APPLY=1 — все одно dry_run; (6) apply+NP_TTN_APPLY=1: створює отримувача й накладну, повертає номер; (7) невизначене відділення → TtnError (fail-closed); (8) delete_ttn під воротами; (9) ключ НП не потрапляє в payload/результат. `python test_np_ttn.py` → exit 0/1."""
import os
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("NP_API_KEY", "TESTKEY-SECRET")
sys.path.insert(0, str(Path(__file__).parent))
import nova_poshta as np_  # noqa: E402
import np_ttn as t  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


chk("телефон: 0501234567, +38 (050) 123-45-67, 380501234567 → 380501234567", all(t.normalize_phone(x) == "380501234567" for x in ("0501234567", "+38 (050) 123-45-67", "380501234567")))
for bad in ("123", "", None, "+1 202 555 0100"):
    try:
        t.normalize_phone(bad)
        ok = False
    except t.TtnError:
        ok = True
    chk(f"телефон {bad!r} → TtnError", ok)
chk("ПІБ: «Іваненко Іван Іванович» → (прізвище, ім'я, по батькові); два слова; порожнє → TtnError", t.split_fio("Іваненко  Іван Іванович") == ("Іваненко", "Іван", "Іванович") and t.split_fio("Іваненко Іван") == ("Іваненко", "Іван", ""))
try:
    t.split_fio("  ")
    ok = False
except t.TtnError:
    ok = True
chk("порожнє ПІБ → TtnError", ok)
items = [{"price": 300, "qty": 2}, {"price": 150, "qty": 1}]
seats = t.seats_for_items(items)
chk("місця: 2 позиції = 2 місця (300×2 → одне місце 1,0 кг, 150×1 → 0,5 кг), НЕ по місцю на штуку", len(seats) == 2 and seats[0]["weight"] == "1.0" and seats[1]["weight"] == "0.5")
chk("оголошена вартість = сума позицій (750), мінімум 200", t.declared_cost(items) == 750 and t.declared_cost([{"price": 50, "qty": 1}]) == 200)

SENDER = {"ref": "S-REF", "contact_ref": "S-CONTACT", "phone": "0990000000", "city_ref": t.RT_SENDER_CITY_REF, "address_ref": t.RT_SENDER_WAREHOUSE_REF}
RCP = {"ref": "R-REF", "contact_ref": "R-CONTACT", "phone": "0501234567", "city_ref": "CITY-X", "address_ref": "WH-X"}
ORDER = {"internal_order_id": "site_PT-1", "items": items, "customer_name": "Іваненко Іван Іванович", "phone": "0501234567", "np_city_ref": "8d5a980d-391c-11dd-90d9-001a92567626", "np_warehouse_number": "5"}
NOW = datetime(2026, 10, 19, 12, 0)
p = t.build_ttn_payload(ORDER, SENDER, RCP, now=NOW)
chk("payload: платник одержувач, WarehouseWarehouse, дата 19.10.2026, 2 місця, відправник наш + Мила №2 (Refs з констант)", p["PayerType"] == "Recipient" and p["ServiceType"] == "WarehouseWarehouse" and p["DateTime"] == "19.10.2026" and p["SeatsAmount"] == "2" and p["CitySender"] == "905c11b6-13d6-11e4-acce-0050568002cf" and p["SenderAddress"] == "c80e75dd-05e3-11e9-ad0d-005056b24375" and p["Sender"] == "S-REF")
chk("передплата: БЕЗ BackwardDeliveryData; телефони нормалізовано; Cost 750", "BackwardDeliveryData" not in p and p["SendersPhone"] == "380990000000" and p["RecipientsPhone"] == "380501234567" and p["Cost"] == "750")
pc = t.build_ttn_payload(ORDER, SENDER, RCP, now=NOW, cod_amount=750)
chk("накладений: BackwardDeliveryData Money, одержувач платить, сума цілими гривнями", pc["BackwardDeliveryData"] == [{"PayerType": "Recipient", "CargoType": "Money", "RedeliveryString": "750"}])
chk("price_payload: без отримувача/телефонів, з місцями; для накладеного RedeliveryCalculate", "RecipientsPhone" not in t.price_payload(p) and "OptionsSeat" in t.price_payload(p) and t.price_payload(pc)["RedeliveryCalculate"]["Amount"] == "750")

calls = []


def fake_call(model, method, props):
    calls.append((model, method))
    if method == "getCounterparties":
        return [{"Ref": "S-REF"}]
    if method == "getCounterpartyContactPersons":
        return [{"Ref": "S-CONTACT", "Phones": "380990000000"}]
    if method == "getDocumentPrice":
        return [{"Cost": 88, "CostRedelivery": 0}]
    if method == "save" and model == "Counterparty":
        return [{"Ref": "NEW-R", "ContactPerson": {"data": [{"Ref": "NEW-C"}]}}]
    if method == "save" and model == "InternetDocument":
        return [{"IntDocNumber": "20451500000001", "Ref": "DOC-REF", "CostOnSite": 88}]
    if method == "delete":
        return [{"Ref": props["DocumentRefs"]}]
    return []


np_._call = fake_call
np_.search_warehouses = lambda city, q="", limit=12: [{"ref": "WH-5", "number": "5", "description": "Відділення №5"}]
t._SENDER_CACHE.clear()
os.environ.pop(t.APPLY_ENV, None)
r = t.create_ttn(ORDER, apply=False, now=NOW)
chk("dry_run за замовчуванням: payload із DRY-RUN-отримувачем, вартість з НП, нічого не створено (жодного save/delete)", r["dry_run"] and r["payload"]["Recipient"] == "DRY-RUN" and r["price"]["cost"] == 88 and not any(m in ("save", "delete") for _, m in calls))
calls.clear()
r2 = t.create_ttn(ORDER, apply=True, now=NOW)
chk("apply=True БЕЗ NP_TTN_APPLY=1 → усе одно dry_run, жодного save", r2["dry_run"] and not any(m in ("save", "delete") for _, m in calls))
os.environ[t.APPLY_ENV] = "1"
calls.clear()
r3 = t.create_ttn(ORDER, apply=True, now=NOW)
chk("apply=True + NP_TTN_APPLY=1: створено отримувача й накладну, повернуто номер і Ref", not r3["dry_run"] and r3["number"] == "20451500000001" and r3["ref"] == "DOC-REF" and ("Counterparty", "save") in calls and ("InternetDocument", "save") in calls)
chk("ключ НП не потрапляє в payload і результат", "TESTKEY-SECRET" not in str(r) and "TESTKEY-SECRET" not in str(r3))
np_.search_warehouses = lambda city, q="", limit=12: []
try:
    t.create_ttn(ORDER, apply=False, now=NOW)
    ok = False
except t.TtnError:
    ok = True
chk("відділення не знайдено (0) → TtnError, нічого не створено", ok)
np_.search_warehouses = lambda city, q="", limit=12: [{"ref": "A", "number": "5"}, {"ref": "B", "number": "5"}]
try:
    t.create_ttn(ORDER, apply=False, now=NOW)
    ok = False
except t.TtnError:
    ok = True
chk("відділення неоднозначне (2) → TtnError", ok)
try:
    t.resolve_recipient_address({"np_city_ref": "", "np_warehouse_number": ""})
    ok = False
except t.TtnError:
    ok = True
chk("нема np_city_ref/np_warehouse_number → TtnError", ok)
os.environ.pop(t.APPLY_ENV, None)
calls.clear()
chk("delete_ttn без воріт → False і без виклику", t.delete_ttn("REF", apply=True) is False and not calls and t.delete_ttn("REF", apply=False) is False)
os.environ[t.APPLY_ENV] = "1"
chk("delete_ttn під воротами → True і виклик delete", t.delete_ttn("REF", apply=True) is True and ("InternetDocument", "delete") in calls)

# --- зауваження аудиту PR #858 ---
os.environ.pop(t.APPLY_ENV, None)
try:
    t.create_recipient("Іваненко Іван", "0501234567")
    ok = False
except t.TtnError:
    ok = True
chk("create_recipient без воріт → TtnError (власні ворота)", ok)
chk("apply='0' (рядок) не відкриває ворота", (os.environ.__setitem__(t.APPLY_ENV, "1"), t._apply_allowed("0"))[1] is False)
try:
    t.split_fio("Іваненко")
    ok = False
except t.TtnError:
    ok = True
chk("ПІБ з одного слова → TtnError", ok)
for bad_item in ({"price": 100, "qty": 0}, {"price": 100, "qty": "x"}, {"price": None, "qty": 1}, {"price": 0, "qty": 1}):
    try:
        t.declared_cost([bad_item])
        t.seats_for_items([bad_item])
        ok = False
    except t.TtnError:
        ok = True
    chk(f"позиція {bad_item} → TtnError", ok)
sv = t.seats_for_items([{"price": 100, "qty": 3}])[0]
chk("об'єм місця в м³ (25×20×10 = 0.005), від кількості не залежить", sv["volumetricVolume"] == "0.005" and t.seats_for_items([{"price": 100, "qty": 1}])[0]["volumetricVolume"] == "0.005")
chk("payload без VolumeGeneral", "VolumeGeneral" not in p and "VolumeGeneral" not in t.price_payload(p))
# валідація до створення контрагента: погана позиція → TtnError, жодного Counterparty.save
calls.clear()
np_.search_warehouses = lambda city, q="", limit=12: [{"ref": "WH-5", "number": "5", "description": "Відділення №5"}]
bad = dict(ORDER, items=[{"price": 100, "qty": "x"}])
try:
    t.create_ttn(bad, apply=True, now=NOW)
    ok = False
except t.TtnError:
    ok = True
chk("погане замовлення при apply → TtnError ДО Counterparty.save (нема сирот)", ok and not any(m == "save" for _, m in calls))
calls.clear()
try:
    t.create_ttn(dict(ORDER, ttn_number="2045"), apply=True, now=NOW)
    ok = False
except t.TtnError:
    ok = True
chk("замовлення з існуючою ТТН + apply → TtnError, нічого не створено", ok and not calls)
# маркування: ключ не витікає у винятку
import requests as _rq  # noqa: E402


def _boom(url, timeout=0):
    raise _rq.ConnectionError(f"HTTPSConnectionPool: {url}")


_orig_get = _rq.get
_rq.get = _boom
try:
    t.fetch_marking_pdf("11111111-2222-3333-4444-555555555555")
    msg = ""
except t.TtnError as e:
    msg = str(e)
_rq.get = _orig_get
chk("fetch_marking_pdf: збій з'єднання → TtnError без ключа", bool(msg) and "TESTKEY-SECRET" not in msg and "apiKey" not in msg)
try:
    t.fetch_marking_pdf("../x")
    ok = False
except t.TtnError:
    ok = True
chk("fetch_marking_pdf: Ref не GUID → TtnError", ok)

# --- габарити з картки постачальника (19.10, порівняння з ТТН Toysi) ---
s1 = t.seats_for_items([{"price": 100, "qty": 1, "dims_cm": [28, 4, 20]}])[0]
chk("габарити картки 28×4×20 → вага = Д×Ш×В/4000 = 0,56 кг (як у ТТН Toysi), об'єм 0,00224 м³", s1["weight"] == "0.56" and s1["volumetricVolume"] == "0.0022")
s2 = t.seats_for_items([{"price": 100, "qty": 1, "dims_cm": [70, 21, 61], "weight_kg": 6}])[0]
chk("ходунки 70×21×61, реальна вага 6 кг → вага ТТН = об'ємна 22,4 кг (НП рахує за більшим)", s2["weight"] == "22.418")
s3 = t.seats_for_items([{"price": 100, "qty": 1, "dims_cm": [10, 10, 10], "weight_kg": 3}])[0]
chk("важкий малий товар: реальна 3 кг > об'ємна 0,25 → 3 кг", s3["weight"] == "3.0")
s4 = t.seats_for_items([{"price": 100, "qty": 2, "dims_cm": [28, 4, 20]}])[0]
chk("qty=2: найменший розмір ×2 (28×8×20) → 1,12 кг, одне місце", s4["weight"] == "1.12")
try:
    t.seats_for_items([{"price": 100, "qty": 1, "dims_cm": [0, 5, 5]}])
    ok = False
except t.TtnError:
    ok = True
chk("нульовий габарит → TtnError", ok)
import json as _j, tempfile as _tf
with _tf.TemporaryDirectory() as td:
    f = Path(td) / "sup.json"
    f.write_text(_j.dumps({"updated": "2026-10-19T00:00:00+00:00", "items": {"rt-5": {"dims_cm": [10, 20, 30], "weight_kg": 1.5}, "rt-6": {"dims_cm": None}}}), encoding="utf-8")
    o2 = t.with_supplier_dims({"items": [{"toysi_code": "rt-5", "qty": 1, "price": 100}, {"toysi_code": "100", "qty": 1, "price": 50}]}, f)
    chk("with_supplier_dims: rt-5 отримав dims/вагу, Toysi-позиція не змінена", o2["items"][0]["dims_cm"] == [10, 20, 30] and o2["items"][0]["weight_kg"] == 1.5 and "dims_cm" not in o2["items"][1])
    for code in ("rt-6", "rt-7"):
        try:
            t.with_supplier_dims({"items": [{"toysi_code": code, "qty": 1, "price": 100}]}, f)
            ok = False
        except t.TtnError:
            ok = True
        chk(f"{code}: нема габаритів/картки → TtnError (fail-closed)", ok)
    try:
        t.with_supplier_dims({"items": [{"toysi_code": "rt-5", "qty": 1, "price": 100}]}, Path(td) / "none.json")
        ok = False
    except t.TtnError:
        ok = True
    chk("нема supplier_rt.json → TtnError", ok)
for bad_it in ({"dims_cm": [float("nan"), 5, 5]}, {"dims_cm": [5, 5, float("inf")]}, {"dims_cm": "123"}, {"dims_cm": [1, 2, 3], "weight_kg": float("nan")}, {"dims_cm": [1, 2, 3], "weight_kg": float("inf")}, {"dims_cm": [1, 2, 3], "weight_kg": -1}, {"dims_cm": [1, 2, 3], "weight_kg": True}):
    try:
        t.seats_for_items([dict(bad_it, price=100, qty=1)])
        ok = False
    except t.TtnError:
        ok = True
    chk(f"nan/inf/рядок/від'ємне/bool у габаритах чи вазі {list(bad_it.values())!r} → TtnError", ok)
sl = t.seats_for_items([{"price": 100, "qty": 1, "dims_cm": [28, 4, 20]}])[0]
chk("підписи: довжина = найбільший, ширина = середній, висота = найменший (28/20/4)", (sl["volumetricLength"], sl["volumetricWidth"], sl["volumetricHeight"]) == ("28.0", "20.0", "4.0"))
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
