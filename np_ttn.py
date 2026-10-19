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
def _dims_from_env() -> tuple:
    try:
        d = tuple(float(x) for x in os.environ.get("NP_DEFAULT_SEAT_DIMS", "25,20,10").split(","))
        if len(d) == 3 and all(x > 0 for x in d):
            return d
    except ValueError:
        pass
    return (25.0, 20.0, 10.0)


DEFAULT_SEAT_DIMS = _dims_from_env()   # довжина, ширина, висота, см (некоректне значення env → дефолт, імпорт не ламаємо)
MIN_SEAT_VOLUME = 0.0004     # м³ (мінімум НП для місця)
MIN_DECLARED_COST = 200      # оголошена вартість не нижче (як у вже створених ТТН)
APPLY_ENV = "NP_TTN_APPLY"
MARKING_URL = "https://my.novaposhta.ua/orders/printMarking100x100/orders[]/{ref}/type/pdf/apiKey/{key}"
_PHONE_RE = re.compile(r"\D+")
_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


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
        raise TtnError("ПІБ отримувача: потрібні щонайменше прізвище й ім'я")
    return parts[0], parts[1], " ".join(parts[2:])


def _qty(it: dict) -> int:
    try:
        q = int(it.get("qty"))
    except (TypeError, ValueError):
        raise TtnError(f"кількість позиції некоректна: {it.get('qty')!r}") from None
    if q <= 0:
        raise TtnError(f"кількість позиції має бути > 0: {q}")
    return q


VOLUMETRIC_DIVISOR = 4000    # НП: об'ємна вага (кг) = Д×Ш×В (см) / 4000; перевірено 19.10 на ТТН Toysi (збіг точний у 4 з 5)


def item_seat(it: dict, qty: int, default_weight: float, default_dims: tuple, vol_default: float) -> tuple:
    """(dims_cm, weight_kg, volumetric_m3) одного місця для позиції. Є it['dims_cm'] (з картки постачальника): габарити беремо як є, а для qty>1 найменший розмір множимо на qty (штабелювання);
    вага = max(реальна×qty, об'ємна) — НП рахує за більшим (як і Toysi: вага в ТТН = Д×Ш×В/4000). Немає габаритів — запасні (дефолт), як раніше."""
    d = it.get("dims_cm")
    if d:
        try:
            dd = sorted(float(x) for x in d)
            if len(dd) != 3 or dd[0] <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise TtnError(f"габарити позиції некоректні: {d!r}") from None
        stacked = [dd[0] * qty, dd[1], dd[2]]
        vol_m3 = max(MIN_SEAT_VOLUME, stacked[0] * stacked[1] * stacked[2] / 1_000_000)
        vol_w = stacked[0] * stacked[1] * stacked[2] / VOLUMETRIC_DIVISOR
        try:
            real = float(it["weight_kg"]) * qty if it.get("weight_kg") else 0.0
        except (TypeError, ValueError):
            raise TtnError(f"вага позиції некоректна: {it.get('weight_kg')!r}") from None
        return tuple(round(x, 1) for x in (stacked[1], stacked[2], stacked[0])), round(max(real, vol_w, 0.1), 3), vol_m3
    try:
        w = float(it.get("weight") or default_weight) * qty
    except (TypeError, ValueError):
        raise TtnError(f"вага позиції некоректна: {it.get('weight')!r}") from None
    return default_dims, round(w, 3), vol_default


def seats_for_items(items: list, default_weight: float = DEFAULT_SEAT_WEIGHT, dims: tuple = DEFAULT_SEAT_DIMS) -> list:
    """Одне місце на КОЖНУ ПОЗИЦІЮ (товар) замовлення (вимога кошика RoyalToys: «якщо декілька товарів — декілька місць (на кожен товар по одному місцю)»);
    кілька штук одного товару = одне місце, вага = вага одиниці × кількість. Жива перевірка НП 19.10: 3 окремі місця Мила→Київ коштують втричі дорожче за одне, тож «по місцю на штуку» було б хибним."""
    seats = []
    vol = max(MIN_SEAT_VOLUME, dims[0] * dims[1] * dims[2] / 1_000_000)   # м³ (НП: volumetricVolume у м³), габарити місця від кількості не залежать
    for it in items or []:
        qty = _qty(it)
        d, w, v = item_seat(it, qty, default_weight, dims, vol)
        seats.append({"volumetricLength": str(d[0]), "volumetricWidth": str(d[1]), "volumetricHeight": str(d[2]),
                      "volumetricVolume": str(round(v, 4)), "weight": str(round(w, 3))})
    if not seats:
        raise TtnError("у замовленні нема позицій для ТТН")
    return seats


def declared_cost(items: list) -> int:
    total = 0.0
    for it in items or []:
        try:
            price = float(it.get("price"))
        except (TypeError, ValueError):
            raise TtnError(f"ціна позиції некоректна: {it.get('price')!r}") from None
        if price <= 0:
            raise TtnError(f"ціна позиції має бути > 0: {price}")
        total += price * _qty(it)
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
    keys = ("CitySender", "CityRecipient", "Weight", "ServiceType", "Cost", "CargoType", "SeatsAmount", "OptionsSeat")
    out = {k: props[k] for k in keys if k in props}
    out["PayerType"] = props["PayerType"]
    if "BackwardDeliveryData" in props:
        out["RedeliveryCalculate"] = {"CargoType": "Money", "Amount": props["BackwardDeliveryData"][0]["RedeliveryString"]}
    return out


def _apply_allowed(apply: bool) -> bool:
    return apply is True and os.environ.get(APPLY_ENV) == "1"


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


def create_recipient(fio: str, phone: str, apply: bool = False) -> dict:
    """РЕАЛЬНА дія (створює контрагента-отримувача в НП): власні ворота (apply=True і NP_TTN_APPLY=1), без них TtnError."""
    if not _apply_allowed(apply):
        raise TtnError("створення отримувача заборонено: потрібні apply=True і NP_TTN_APPLY=1")
    last, first, middle = split_fio(fio)
    data = np_._call("Counterparty", "save", {"FirstName": first, "MiddleName": middle, "LastName": last, "Phone": normalize_phone(phone), "CounterpartyType": "PrivatePerson", "CounterpartyProperty": "Recipient"})
    rec = data[0] if data else {}
    contact = ((rec.get("ContactPerson") or {}).get("data") or [{}])[0]
    if not rec.get("Ref") or not contact.get("Ref"):
        raise TtnError("НП не повернула Ref отримувача/контакту")
    return {"ref": rec["Ref"], "contact_ref": contact["Ref"]}


def with_supplier_dims(order: dict, supplier_file=None) -> dict:
    """Копія замовлення, де rt-позиції отримали dims_cm/weight_kg із приватного supplier_rt.json (rt_supplier_export). rt-позиція без габаритів → TtnError (fail-closed: на запасних розмірах ТТН не створюємо)."""
    items = []
    sup = None
    for it in order.get("items") or []:
        it = dict(it)
        if str(it.get("toysi_code") or "").strip().lower().startswith("rt-") and not it.get("dims_cm"):
            if sup is None:
                import json
                from pathlib import Path
                if supplier_file is None:
                    from rt_supplier_export import OUT_FILE as supplier_file
                try:
                    sup = (json.loads(Path(supplier_file).read_text(encoding="utf-8-sig")).get("items") or {})
                except (OSError, ValueError):
                    raise TtnError("supplier_rt.json недоступний: габарити rt-позицій невідомі") from None
            src = sup.get(it["toysi_code"].strip().lower()) or sup.get(it["toysi_code"]) or {}
            if not src.get("dims_cm"):
                raise TtnError(f"нема габаритів для {it.get('toysi_code')}: ТТН не створюємо")
            it["dims_cm"], it["weight_kg"] = src["dims_cm"], src.get("weight_kg")
        items.append(it)
    return {**order, "items": items}


def create_ttn(order: dict, apply: bool = False, cod_amount: int = 0, now: datetime | None = None) -> dict:
    """dry_run (за замовчуванням): payload із ПЛЕЙСХОЛДЕРАМИ отримувача + вартість доставки з НП (getDocumentPrice); нічого не створює.
    apply=True і NP_TTN_APPLY=1: створює отримувача й накладну; повертає {'dry_run': False, 'number', 'ref', 'cost'}.
    ІДЕМПОТЕНТНІСТЬ — на стороні виклику: зберегти number/ref у замовленні одразу після створення і передавати їх як order['ttn_ref'|'ttn_number'] (тоді live-виклик відмовить); повторний виклик без цього створить другу платну ТТН."""
    order = with_supplier_dims(order)    # rt-позиції: габарити з картки постачальника (fail-closed без них)
    live = _apply_allowed(apply)
    if live and (order.get("ttn_ref") or order.get("ttn_number")):
        raise TtnError("у замовлення вже є ТТН (ttn_ref/ttn_number): повторне створення коштувало б грошей — ідемпотентність на стороні виклику")
    sender = resolve_sender()
    city_ref, wh_ref = resolve_recipient_address(order)
    recipient = {"ref": "DRY-RUN", "contact_ref": "DRY-RUN", "phone": order.get("phone"), "city_ref": city_ref, "address_ref": wh_ref}
    props = build_ttn_payload(order, sender, recipient, now=now, cod_amount=cod_amount)   # уся валідація ДО будь-якої реальної дії (нема сиріт-контрагентів)
    if live:
        split_fio(order.get("customer_name"))
        rcp = create_recipient(order.get("customer_name"), order.get("phone"), apply=True)
        recipient.update(rcp)
        props = build_ttn_payload(order, sender, recipient, now=now, cod_amount=cod_amount)
    if not live:
        pr = np_._call("InternetDocument", "getDocumentPrice", price_payload(props))
        price = pr[0] if pr else {}
        return {"dry_run": True, "payload": props, "price": {"cost": price.get("Cost"), "cost_redelivery": price.get("CostRedelivery")}}
    try:
        data = np_._call("InternetDocument", "save", props)
    except Exception as e:  # noqa: BLE001
        # отримувач уже створений у НП: не губимо його Ref (повтор не має плодити дублі контрагента)
        raise TtnError(f"ТТН не створено ({type(e).__name__}); отримувач уже створений у НП: Ref={props.get('Recipient')}") from None
    rec = data[0] if data else {}
    if not rec.get("IntDocNumber"):
        raise TtnError("НП не повернула номер ТТН")
    return {"dry_run": False, "number": rec["IntDocNumber"], "ref": rec.get("Ref"), "cost": rec.get("CostOnSite")}


def fetch_marking_pdf(ref: str, timeout: int = 30) -> bytes:
    """Файл маркування 100×100 (PDF) для ТТН — те, що RoyalToys просить прикріпити. Читання; ключ не логуємо й не повертаємо."""
    if not np_.NP_API_KEY:
        raise TtnError("не заданий NP_API_KEY")
    if not _GUID_RE.match(ref or ""):
        raise TtnError("маркування: Ref ТТН має бути GUID")
    try:
        r = requests.get(MARKING_URL.format(ref=ref, key=np_.NP_API_KEY), timeout=timeout)
    except requests.RequestException:
        raise TtnError("маркування: помилка з'єднання з НП") from None   # без тексту винятку: він містить URL з ключем
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        raise TtnError(f"маркування не отримано (HTTP {r.status_code}, не PDF)")
    return r.content


def delete_ttn(ref: str, apply: bool = False) -> bool:
    """Видалення НЕвідправленої ТТН (для тестів/помилок). Реальна дія → ворота apply+NP_TTN_APPLY=1; інакше False."""
    if not _apply_allowed(apply):
        return False
    np_._call("InternetDocument", "delete", {"DocumentRefs": ref})
    return True
