# -*- coding: utf-8 -*-
"""np_ttn.py — створення ТТН Нової Пошти НАШИМ акаунтом (рішення власника 19.10.2026: для RoyalToys ТТН створюємо самі, ключ НП постачальнику не віддаємо; та сама здатність потрібна для безкоштовної доставки через власну ТТН).

СХЕМА RoyalToys (їхні відповіді в Telegram 19.10): відправлення з НП «с. Мила, відділення № 2» (обов'язково в ТТН), маркування 100×100, на кожен ТОВАР (позицію) окреме місце, замовлення за нашою ТТН оплачуються повністю наперед, накладений платіж у будь-якому разі повертається нам.
Для rt- товарів накладений платіж ВИМКНЕНО (передоплата), тож стандартна ТТН без зворотної доставки; модуль уміє й накладений (BackwardDeliveryData) для інших випадків.

БЕЗПЕКА (money/реальні дії): режим за замовчуванням — РОЗРАХУНОК (dry_run=True): будує payload і питає НП лише про вартість (InternetDocument.getDocumentPrice, нічого не створює). Реальне створення (Counterparty.save отримувача + InternetDocument.save) вимагає ОБОХ: параметр apply=True І змінну NP_TTN_APPLY=1. Видалення (delete_ttn) — так само під воротами. Ключ НП (NP_API_KEY) не друкується й не логується; URL маркування з ключем повертається лише всередині fetch_marking_pdf (байти, не рядок).

API: build_ttn_payload(order, sender, items_seats) → dict; create_ttn(order, apply=False) → {"dry_run", "payload", "price"|"number","ref"}; fetch_marking_pdf(ref) → bytes; delete_ttn(ref). Без залежності від мережі в build_* (тестується офлайн)."""
import os
import re
import sys
from datetime import datetime

import requests

import nova_poshta as np_

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Відправлення RoyalToys: Нова Пошта, с. Мила (Київська обл.), відділення № 2 (вул. Комарова, 38) — перевірено через НП 19.10.2026
RT_SENDER_CITY_REF = os.environ.get("NP_RT_SENDER_CITY_REF", "905c11b6-13d6-11e4-acce-0050568002cf")
RT_SENDER_WAREHOUSE_REF = os.environ.get("NP_RT_SENDER_WAREHOUSE_REF", "c80e75dd-05e3-11e9-ad0d-005056b24375")
DEFAULT_SEAT_WEIGHT = float(os.environ.get("NP_DEFAULT_SEAT_WEIGHT", "0.5"))      # кг, поки RoyalToys не дав вагу/габарити (запитано 19.10); НП перерахує за фактичною
DEFAULT_SEAT_DIMS = tuple(float(x) for x in os.environ.get("NP_DEFAULT_SEAT_DIMS", "25,20,10").split(","))   # довжина, ширина, висота, см
MIN_DECLARED_COST = 200      # оголошена вартість не нижче (як у вже створених ТТН)
APPLY_ENV = "NP_TTN_APPLY"
MARKING_URL = "https://my.novaposhta.ua/orders/printMarking100x100/orders[]/{ref}/type/pdf/apiKey/{key}"
_PHONE_RE = re.compile(r"\D+")


class TtnError(Exception):
    pass


def normalize_phone(phone: str) -> str:
    """380XXXXXXXXX із будь-якого запису (+38 (050) 123-45-67, 0501234567, 380501234567); інакше TtnError."""
    d = _PHONE_RE.sub("", phone or "")
    if len(d) == 10 and d.startswith("0"):
        d = "38" + d
    if len(d) == 12 and d.startswith("380"):
        return d
    raise TtnError(f"телефон не розпізнано: {phone!r}")


def split_fio(full_name: str) -> tuple:
    """«Прізвище Ім'я По батькові» → (прізвище, ім'я, по батькові); одне-два слова теж приймаємо (НП не вимагає по батькові); порожнє → TtnError.
    Порядок слів — як у формі сайту/площадки (Прізвище Ім'я…), НЕ вгадуємо."""
    parts = [p for p in re.split(r"\s+", (full_name or "").strip()) if p]
    if not parts:
        raise TtnError("порожнє ПІБ отримувача")
    if len(parts) == 1:
        return parts[0], parts[0], ""
    return parts[0], parts[1], " ".join(parts[2:])


def seats_for_items(items: list, default_weight: float = DEFAULT_SEAT_WEIGHT, dims: tuple = DEFAULT_SEAT_DIMS) -> list:
    """Одне місце на КОЖНУ ПОЗИЦІЮ (товар) замовлення (вимога кошика RoyalToys: «якщо декілька товарів — декілька місць (на кожен товар по одному місцю)»);
    кілька штук одного товару = одне місце, вага = вага одиниці × кількість. Жива перевірка НП 19.10: 3 окремі місця Мила→Київ коштують втричі дорожче за одне, тож «по місцю на штуку» було б хибним."""
    seats = []
    for it in items or []:
        qty = max(1, int(it.get("qty") or 1))
        w = float(it.get("weight") or default_weight) * qty
        seats.append({"volumetricLength": str(dims[0]), "volumetricWidth": str(dims[1]), "volumetricHeight": str(dims[2]),
                      "volumetricVolume": str(round(dims[0] * dims[1] * dims[2] / 4000 * qty, 3)), "weight": str(round(w, 3))})
    if not seats:
        raise TtnError("у замовленні нема позицій для ТТН")
    return seats


def declared_cost(items: list) -> int:
    total = sum(float(it.get("price") or 0) * max(1, int(it.get("qty") or 1)) for it in items or [])
    return int(max(MIN_DECLARED_COST, round(total)))


def build_ttn_payload(order: dict, sender: dict, recipient: dict, now: datetime | None = None, cod_amount: int = 0) -> dict:
    """InternetDocument.save methodProperties (чиста функція). sender={'ref','contact_ref','phone','city_ref','address_ref'};
    recipient={'ref','contact_ref','phone','city_ref','address_ref'}. cod_amount>0 → зворотна доставка (накладений платіж) цілими гривнями на нас; 0 → без неї."""
    now = now or datetime.now()
    items = order.get("items") or []
    seats = seats_for_items(items)
    props = {
        "NewAddress": "1",
        "PayerType": "Recipient",
        "PaymentMethod": "Cash",
        "CargoType": "Parcel",
        "ServiceType": "WarehouseWarehouse",
        "DateTime": now.strftime("%d.%m.%Y"),
        "SeatsAmount": str(len(seats)),
        "Weight": str(round(sum(float(s["weight"]) for s in seats), 3)),
        "VolumeGeneral": str(round(sum(float(s["volumetricVolume"]) for s in seats), 3)),
        "OptionsSeat": seats,
        "Description": "Дитячі іграшки",
        "Cost": str(declared_cost(items)),
        "CitySender": sender["city_ref"],
        "Sender": sender["ref"],
        "SenderAddress": sender["address_ref"],
        "ContactSender": sender["contact_ref"],
        "SendersPhone": normalize_phone(sender["phone"]),
        "CityRecipient": recipient["city_ref"],
        "Recipient": recipient["ref"],
        "RecipientAddress": recipient["address_ref"],
        "ContactRecipient": recipient["contact_ref"],
        "RecipientsPhone": normalize_phone(recipient["phone"]),
        "InfoRegClientBarcodes": str(order.get("internal_order_id") or "")[:36],
    }
    if cod_amount and int(cod_amount) > 0:
        props["BackwardDeliveryData"] = [{"PayerType": "Recipient", "CargoType": "Money", "RedeliveryString": str(int(cod_amount))}]
    return props


def price_payload(props: dict) -> dict:
    """Підмножина для InternetDocument.getDocumentPrice (розрахунок вартості доставки; нічого не створює)."""
    keys = ("CitySender", "CityRecipient", "Weight", "ServiceType", "Cost", "CargoType", "SeatsAmount", "OptionsSeat", "VolumeGeneral")
    out = {k: props[k] for k in keys if k in props}
    out["PayerType"] = props["PayerType"]
    if "BackwardDeliveryData" in props:
        out["RedeliveryCalculate"] = {"CargoType": "Money", "Amount": props["BackwardDeliveryData"][0]["RedeliveryString"]}
    return out


def _apply_allowed(apply: bool) -> bool:
    return bool(apply) and os.environ.get(APPLY_ENV) == "1"


_SENDER_CACHE: dict = {}


def resolve_sender() -> dict:
    """Відправник = НАШ контрагент у НП (перший «Sender») + його перша контактна особа; адреса відправлення — RoyalToys (с. Мила № 2). Кеш у пам'яті процесу."""
    if _SENDER_CACHE:
        return dict(_SENDER_CACHE)
    cps = np_._call("Counterparty", "getCounterparties", {"CounterpartyProperty": "Sender", "Page": "1"})
    if not cps:
        raise TtnError("у НП нема контрагента-відправника")
    cp = cps[0]
    contacts = np_._call("Counterparty", "getCounterpartyContactPersons", {"Ref": cp["Ref"], "Page": "1"})
    if not contacts:
        raise TtnError("у контрагента-відправника нема контактної особи")
    phone = os.environ.get("NP_SENDER_PHONE") or (contacts[0].get("Phones") or "")
    _SENDER_CACHE.update({"ref": cp["Ref"], "contact_ref": contacts[0]["Ref"], "phone": phone, "city_ref": RT_SENDER_CITY_REF, "address_ref": RT_SENDER_WAREHOUSE_REF})
    return dict(_SENDER_CACHE)


def resolve_recipient_address(order: dict) -> tuple:
    """(city_ref, warehouse_ref) відділення отримувача: np_city_ref + np_warehouse_number із замовлення → Ref через довідник НП; не однозначно → TtnError (fail-closed)."""
    city = (order.get("np_city_ref") or "").strip()
    num = (order.get("np_warehouse_number") or "").strip()
    if not city or not num:
        raise TtnError("у замовленні нема np_city_ref/np_warehouse_number: відділення не визначено")
    whs = [w for w in np_.search_warehouses(city, num, limit=50) if (w.get("number") or "") == num]
    if len(whs) != 1:
        raise TtnError(f"відділення №{num} у місті {city}: знайдено {len(whs)} (потрібно рівно 1)")
    return city, whs[0]["ref"]


def create_recipient(fio: str, phone: str) -> dict:
    """РЕАЛЬНА дія (створює контрагента-отримувача в НП): викликається лише під воротами apply."""
    last, first, middle = split_fio(fio)
    data = np_._call("Counterparty", "save", {"FirstName": first, "MiddleName": middle, "LastName": last, "Phone": normalize_phone(phone), "CounterpartyType": "PrivatePerson", "CounterpartyProperty": "Recipient"})
    rec = data[0] if data else {}
    contact = ((rec.get("ContactPerson") or {}).get("data") or [{}])[0]
    if not rec.get("Ref") or not contact.get("Ref"):
        raise TtnError("НП не повернула Ref отримувача/контакту")
    return {"ref": rec["Ref"], "contact_ref": contact["Ref"]}


def create_ttn(order: dict, apply: bool = False, cod_amount: int = 0, now: datetime | None = None) -> dict:
    """dry_run (за замовчуванням): payload із ПЛЕЙСХОЛДЕРАМИ отримувача + вартість доставки з НП (getDocumentPrice); нічого не створює.
    apply=True і NP_TTN_APPLY=1: створює отримувача й накладну; повертає {'dry_run': False, 'number', 'ref', 'cost'}."""
    sender = resolve_sender()
    city_ref, wh_ref = resolve_recipient_address(order)
    live = _apply_allowed(apply)
    if live:
        rcp = create_recipient(order.get("customer_name"), order.get("phone"))
    else:
        rcp = {"ref": "DRY-RUN", "contact_ref": "DRY-RUN"}
    recipient = {**rcp, "phone": order.get("phone"), "city_ref": city_ref, "address_ref": wh_ref}
    props = build_ttn_payload(order, sender, recipient, now=now, cod_amount=cod_amount)
    if not live:
        pr = np_._call("InternetDocument", "getDocumentPrice", price_payload(props))
        price = pr[0] if pr else {}
        return {"dry_run": True, "payload": props, "price": {"cost": price.get("Cost"), "cost_redelivery": price.get("CostRedelivery")}}
    data = np_._call("InternetDocument", "save", props)
    rec = data[0] if data else {}
    if not rec.get("IntDocNumber"):
        raise TtnError("НП не повернула номер ТТН")
    return {"dry_run": False, "number": rec["IntDocNumber"], "ref": rec.get("Ref"), "cost": rec.get("CostOnSite")}


def fetch_marking_pdf(ref: str, timeout: int = 30) -> bytes:
    """Файл маркування 100×100 (PDF) для ТТН — те, що RoyalToys просить прикріпити. Читання; ключ не логуємо й не повертаємо."""
    if not np_.NP_API_KEY:
        raise TtnError("не заданий NP_API_KEY")
    r = requests.get(MARKING_URL.format(ref=ref, key=np_.NP_API_KEY), timeout=timeout)
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        raise TtnError(f"маркування не отримано (HTTP {r.status_code}, не PDF)")
    return r.content


def delete_ttn(ref: str, apply: bool = False) -> bool:
    """Видалення НЕвідправленої ТТН (для тестів/помилок). Реальна дія → ворота apply+NP_TTN_APPLY=1; інакше False."""
    if not _apply_allowed(apply):
        return False
    np_._call("InternetDocument", "delete", {"DocumentRefs": ref})
    return True
