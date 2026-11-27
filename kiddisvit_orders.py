# -*- coding: utf-8 -*-
"""kiddisvit_orders.py — замовлення САЙТУ з товарами KIDDISVIT (kv-/kvu-) → B2B API orders/create (етап А, 21.11.2026; рішення власника: «будувати модуль замовлень KIDDISVIT»).

ЦЕЙ ФАЙЛ — ЧИСТА ЛОГІКА БЕЗ МЕРЕЖІ: відображення позицій замовлення на товари постачальника, перевірки, побудова тіла orders/create, звіт «що б зробили» (dry-run). Запис у KIDDISVIT робить
лише `kiddisvit_api.KiddisvitClient.orders_create/orders_ttn_data/orders_assembly` і лише за воротами KV_ORDERS_LIVE=1 (типово вимкнено). Раннер (БД-стани, ТТН НП, документи) — наступний етап.

СХЕМА (довідник Продавця ДОВІДНИК_KIDDISVIT_API.md §5–8 + відповіді Т. Сіденко 17–18.11, скріни власника):
  • лише ПЕРЕДОПЛАТА (prepaid, payment_confirmed=1), `redelivery` НЕ передаємо (менеджер підтвердила); `my_ttn: true` — відвантаження за НАШОЮ ТТН НП з відділення «Дніпро №28, Березинська 58»;
  • номер замовлення KIDDISVIT («КЗ-…», приклад з довідника «З-00110331») вписується в накладну НП у «Внутрішній номер відправлення» і «Додаткова інформація» (InfoRegClientBarcodes / AdditionalInformation);
  • `orders/create` НЕ означає резерв: data.status_OK=1 — сформовано, 0 — створено без резерву (Comment з причиною) → ручне рішення;
  • одна позиція = `Product` (productID = частина id після kv-/kvu-), `Warehouse` (`Dnepr` або `NTV20`, як у supplier_kv.json), `Quantity`; поєднання складів Dnepr+NTV20 в одному замовленні менеджер НЕ підтвердила → розділ вручну;
  • NTV20 (kvu-): повернення не приймають, брак → обмін, на таку позицію знижка −20 % (як її застосовують — з'ясувати першим замовленням).
ПРИПУЩЕННЯ, ЯКІ ПЕРЕВІРИТЬ ПЕРШЕ ЖИВЕ (пробне власника) замовлення: `DeliveryCity`/`DeliveryWarehouse` = Ref міста й відділення НП (36 символів; в API не описано), `Product` = числовий productID (а не GUID), `recipient_phone` у форматі +380…,
`Shipment` = DeliveryServiceWarehouse, `deliveryService` = NewPost, `delivery_payer` = recipient. Це лише тіло запиту; поки KV_ORDERS_LIVE≠1 жодного запиту не відбувається.
РЕФ ВІДДІЛЕННЯ: сайтові замовлення пишуть np_city_ref і np_warehouse_number, але НЕ np_ref_id → Ref шукається за (місто, номер) через `lookup_np_ref` (довідник НП; передається як `lookup`); без lookup сайтове замовлення блокується («етап Б вмикає резолв»).
ДЛЯ ЕТАПУ Б (з аудиту #1933): атомарний claim у БД ДО orders_create і запис number/id ПІСЛЯ; KiddisvitUncertain → стан «невідомо» (НЕ повторювати create, спершу orders/search за Comment); свіжий статус LiqPay/повернення й живий залишок (`products/rests`) безпосередньо перед create; файл постачальника оновлюється щогодини."""
import json
import math
import re
import sys
from pathlib import Path

from kiddisvit_api import KiddisvitUncertain

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
KV_PREFIXES = ("kv-", "kvu-")
WAREHOUSES = ("Dnepr", "NTV20")           # NTV30 більше не існує (менеджер: «було давно»)
MAX_QTY_PER_LINE = 50                       # запобіжник: аномальна кількість у позиції — ручна перевірка


class OrderBlocked(Exception):
    """Замовлення не можна оформити автоматично: причина — людині (текст зрозумілий без контексту)."""


def load_supplier(path=None) -> dict:
    """items із приватного supplier_kv.json (cost, warehouse, stock, vendor_code, dims_cm, weight_kg…). Нема/битий → OrderBlocked (fail-closed)."""
    from kv_supplier_export import OUT_FILE
    try:
        d = json.loads(Path(path or OUT_FILE).read_text(encoding="utf-8-sig"))
        return d.get("items") or {}
    except (OSError, ValueError, AttributeError) as e:
        raise OrderBlocked(f"supplier_kv.json недоступний ({type(e).__name__})") from None


def product_id(code: str) -> str:
    """kv-748287 / kvu-748287 → 748287 (productID KIDDISVIT); інше → ''."""
    c = str(code or "").strip().lower()
    for p in KV_PREFIXES:
        if c.startswith(p):
            pid = c[len(p):]
            return pid if re.fullmatch(r"[0-9a-z-]{1,40}", pid) else ""
    return ""


def map_items(order: dict, supplier: dict) -> list:
    """Позиції замовлення → [{code, product_id, warehouse, qty, price_paid, cost, name, condition}]. Усі позиції мають бути kv-/kvu-, у файлі постачальника, з собівартістю, на ОДНОМУ складі, залишок ≥ кількості,
    ціна, яку сплатив клієнт (за одиницю), не нижча за собівартість (інакше збиток — ручне рішення). Однакові коди зливаються. Інакше OrderBlocked з причиною."""
    merged = {}
    for it in order.get("items") or []:
        code = str((it or {}).get("toysi_code") or "").strip().lower()
        pid = product_id(code)
        if not pid:
            raise OrderBlocked(f"змішане замовлення (позиція {code or '?'} не KIDDISVIT) — потрібен ручний розділ")
        src = supplier.get(code)
        if not src:
            raise OrderBlocked(f"{code}: нема в supplier_kv.json (знята з публікації/зникла в постачальника)")
        raw_qty = it.get("qty")
        try:
            if isinstance(raw_qty, bool) or (isinstance(raw_qty, float) and raw_qty != int(raw_qty)):
                raise ValueError("qty")
            qty = int(raw_qty)
        except (TypeError, ValueError, OverflowError):
            raise OrderBlocked(f"{code}: кількість некоректна") from None
        if qty < 1 or qty > MAX_QTY_PER_LINE:
            raise OrderBlocked(f"{code}: кількість {qty} поза 1…{MAX_QTY_PER_LINE} — перевірте вручну")
        try:
            cost = float(src.get("cost"))
        except (TypeError, ValueError):
            cost = 0.0
        if not math.isfinite(cost):
            cost = 0.0
        wh = str(src.get("warehouse") or "").strip()
        if cost <= 0 or wh not in WAREHOUSES:
            raise OrderBlocked(f"{code}: у даних постачальника нема собівартості або склад «{wh}» не з {WAREHOUSES}")
        try:
            price = float(it.get("price"))
        except (TypeError, ValueError):
            raise OrderBlocked(f"{code}: ціна в замовленні некоректна") from None
        if not math.isfinite(price) or price <= 0:
            raise OrderBlocked(f"{code}: ціна в замовленні некоректна")
        if price < cost:
            raise OrderBlocked(f"{code}: ціна клієнта {price:g} нижча за собівартість {cost:g} — збиток, потрібне рішення")
        if code in merged:
            merged[code]["qty"] += qty
            if merged[code]["qty"] > MAX_QTY_PER_LINE:
                raise OrderBlocked(f"{code}: сумарна кількість {merged[code]['qty']} поза 1…{MAX_QTY_PER_LINE} — перевірте вручну")
        else:
            merged[code] = {"code": code, "product_id": pid, "warehouse": wh, "qty": qty, "price_paid": price, "cost": cost, "name": str(it.get("name") or src.get("name") or ""),
                            "condition": str(src.get("condition") or ""), "stock": src.get("stock")}
    if not merged:
        raise OrderBlocked("у замовленні нема позицій")
    for m in merged.values():
        try:
            stock = float(m["stock"])
        except (TypeError, ValueError):
            raise OrderBlocked(f"{m['code']}: залишок у файлі постачальника невідомий") from None
        if not math.isfinite(stock):
            raise OrderBlocked(f"{m['code']}: залишок у файлі постачальника невідомий")
        if stock < m["qty"]:
            raise OrderBlocked(f"{m['code']}: залишок {stock:g} менший за замовлене {m['qty']}")
    whs = {m["warehouse"] for m in merged.values()}
    if len(whs) > 1:
        raise OrderBlocked(f"позиції зі складів {sorted(whs)} в одному замовленні — менеджер KIDDISVIT не підтвердила таке поєднання, розділіть вручну")
    return list(merged.values())


def recipient(order: dict) -> tuple:
    """(ПІБ, телефон +380XXXXXXXXX) отримувача: обов'язкові поля orders/create. Некоректні → OrderBlocked (до будь-якої дії)."""
    name = " ".join(str(order.get("customer_name") or "").split())
    digits = re.sub(r"\D", "", str(order.get("phone") or ""))
    if len(digits) == 10 and digits.startswith("0"):
        digits = "38" + digits
    if len(name.split()) < 2 or not re.fullmatch(r"380\d{9}", digits):
        raise OrderBlocked("ПІБ (прізвище й ім'я) і телефон отримувача обов'язкові для KIDDISVIT, а в замовленні вони некоректні/неповні — уточніть у клієнта")
    return name, "+" + digits


def _kv_only(order: dict) -> None:
    if order.get("platform") != "site":
        raise OrderBlocked(f"платформа «{order.get('platform')}»: замовлення KIDDISVIT оформлюємо лише з сайту")
    if order.get("payment_method") != "prepaid" or order.get("payment_confirmed") != 1:
        raise OrderBlocked("оплата не підтверджена передоплатою (prepaid + payment_confirmed=1) — постачальнику не передаємо")
    if order.get("cancelled_at") or str(order.get("status") or "") in ("cancelled", "sandbox_test", "eva_cancelled_before_forward"):
        raise OrderBlocked("замовлення скасоване/тестове — не передаємо")
    if order.get("refund_receipt_at") or order.get("refund_receipt_claim_at") or str(order.get("delivery_status") or "") in ("cancelled", "returned"):
        raise OrderBlocked("замовлення повернене/повертається (чек повернення або статус cancelled/returned) — постачальнику не передаємо (ті самі умови, що в get_kv_orders_needing_receipt)")
    if not order.get("checkbox_ettn_registered_at"):
        raise OrderBlocked("фіскальний чек ще не видано (checkbox_ettn_registered_at порожнє) — передачу постачальнику не починаємо (спершу чек)")


def np_warehouse_ref(order: dict, lookup=None) -> str:
    """Ref відділення НП одержувача. Сайтові замовлення пишуть лише np_city_ref і np_warehouse_number (номер) — np_ref_id у них НЕМАЄ (аудит #1933), тож Ref шукаємо за (city_ref, номер) через `lookup(city_ref, number) -> ref|None`
    (справжній — `lookup_np_ref` на nova_poshta; у тестах підміна). Є np_ref_id (Rozetka/Prom/EVA) — він авторитетний. Нема ні Ref, ні lookup/результату → OrderBlocked («резолв Ref відділення — етап Б»)."""
    ref = str(order.get("np_ref_id") or "").strip()
    if ref:
        return ref
    city, num = str(order.get("np_city_ref") or "").strip(), str(order.get("np_warehouse_number") or "").strip()
    if not city or not num:
        raise OrderBlocked("відділення НП не визначено (нема np_city_ref і номера відділення) — уточніть у клієнта")
    if lookup is None:
        raise OrderBlocked("Ref відділення НП не визначено: сайтове замовлення має лише номер відділення, резолв Ref за (місто, номер) вмикається на етапі Б")
    try:
        got = str(lookup(city, num) or "").strip()
    except Exception as e:  # noqa: BLE001
        raise OrderBlocked(f"довідник НП недоступний ({type(e).__name__}) — Ref відділення не визначено") from None
    if not got:
        raise OrderBlocked(f"відділення №{num} у місті {city} не знайдено в довіднику НП")
    return got


def lookup_np_ref(city_ref: str, number: str):
    """Справжній резолв Ref відділення за (CityRef, номер) із довідника НП (читання; nova_poshta._city_warehouses). Нічого не знайдено → None."""
    import nova_poshta as np_
    city_ref = str(city_ref or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F-]{10,40}", city_ref):
        return None               # як guard у nova_poshta.search_warehouses: довільний рядок у getWarehouses не передаємо
    for w in np_._city_warehouses(city_ref):
        if str(w.get("number") or w.get("Number") or "").strip() == str(number).strip():
            return w.get("ref") or w.get("Ref")
    return None


def build_create_payload(order: dict, items: list, ids: dict, recip: tuple, lookup=None) -> dict:
    """Тіло POST orders/create. ids={'OrganizationID','ClientID','ContractID','shopID'} (з clients/list). Відділення НП — Ref (np_ref_id або резолв за містом і номером), місто — np_city_ref. Без `redelivery` (лише передоплата)."""
    for k in ("OrganizationID", "ClientID", "ContractID", "shopID"):
        if not str((ids or {}).get(k) or "").strip():
            raise OrderBlocked(f"clients/list не дав {k} — оформлення неможливе")
    city = str(order.get("np_city_ref") or "").strip()
    wh = np_warehouse_ref(order, lookup)
    if not city:
        raise OrderBlocked("відділення НП не визначено (нема np_city_ref) — уточніть у клієнта")
    return {
        "OrganizationID": ids["OrganizationID"], "ClientID": ids["ClientID"], "ContractID": ids["ContractID"],
        "Shipment": "DeliveryServiceWarehouse", "deliveryService": "NewPost", "shopID": ids["shopID"],
        "DeliveryCity": city, "DeliveryWarehouse": wh,
        "Comment": f"Сайт plutustoys.com.ua, замовлення {order.get('internal_order_id')}",
        "recipient": recip[0], "recipient_phone": recip[1], "delivery_payer": "recipient",
        "my_ttn": True,
        "Items": [{"Product": i["product_id"], "Warehouse": i["warehouse"], "Quantity": i["qty"]} for i in items],
    }


def dry_run(order: dict, supplier: dict, ids: dict, lookup=None) -> dict:
    """Звіт «що б зробили» БЕЗ запису: {'ok': bool, 'problems': [...], 'items': [...], 'payload': {...}|None, 'cost_total': float}. Ніколи не кидає назовні: OrderBlocked і будь-який збій даних → problems (ok=False)."""
    rep = {"order": (order or {}).get("internal_order_id") if isinstance(order, dict) else None, "ok": False, "problems": [], "items": [], "payload": None, "cost_total": 0.0}
    try:
        _kv_only(order)
        items = map_items(order, supplier)
        recip = recipient(order)
        payload = build_create_payload(order, items, ids, recip, lookup)
    except OrderBlocked as e:
        rep["problems"].append(str(e))
        return rep
    except Exception as e:  # noqa: BLE001 — дані замовлення/постачальника несподіваної форми: fail-closed, не падіння
        rep["problems"].append(f"некоректні дані замовлення ({type(e).__name__}) — ручна перевірка")
        return rep
    rep["items"] = [(i["code"], i["qty"], i["warehouse"]) for i in items]
    rep["cost_total"] = round(sum(i["cost"] * i["qty"] for i in items), 2)
    rep["payload"] = payload
    rep["ok"] = True
    return rep


_SENSITIVE_KEY = re.compile(r"token|key|pass|secret|phone|tel|mail|name|fio|address|адрес|телефон|піб|прізв|ім'я", re.I)


def _response_hint(res) -> str:
    """Діагностика невідомого результату create (27.11.2026: тест-замовлення дало kv_unknown, а що саме відповів KIDDISVIT — не збереглось).
    Лише ключі верхнього рівня й короткі (≤40 симв.) значення скалярів, без вкладених структур і довгих рядків: у відповіді нема секретів,
    але журнал/БД не мають тягнути зайве. Збій формування підказки НЕ має міняти висновок «результат невідомий»."""
    try:
        if isinstance(res, dict):
            parts = []
            for k in sorted(res)[:12]:
                v = res[k]
                if _SENSITIVE_KEY.search(str(k)):
                    shown = "***"
                else:
                    shown = "<структура>" if isinstance(v, (dict, list)) else str(v)[:40]
                parts.append(f"{str(k)[:24]}={shown}")
            return " [відповідь: " + "; ".join(parts) + "]"
        return f" [відповідь: {type(res).__name__} {str(res)[:80]}]"
    except Exception:  # noqa: BLE001
        return ""


def interpret_create(res: dict) -> dict:
    """Відповідь orders/create → {'number','id','reserved': bool,'comment'}. Номер і id обов'язкові: інакше KiddisvitUncertain (НЕ OrderBlocked!): запит ДІЙШОВ, замовлення могло бути створене — повторювати create заборонено,
    лише звірка в кабінеті/orders/search (за Comment з internal_order_id). reserved = status_OK==1; 0 → замовлення СТВОРЕНО, але не зарезервовано (Comment): конвеєр НЕ йде далі до ТТН, рішення людини."""
    if not isinstance(res, dict) or not str(res.get("number") or "").strip() or not str(res.get("id") or "").strip():
        # Підказка ПЕРШОЮ: kv_error[:300] і RunnerError[:160] у раннері обрізають хвіст (аудит #2086, Z1)
        raise KiddisvitUncertain(_response_hint(res).strip() + " відповідь orders/create без number/id — замовлення могло бути створене; повторювати create заборонено, звірте в кабінеті KIDDISVIT")
    return {"number": str(res["number"]).strip(), "id": str(res["id"]).strip(), "reserved": res.get("status_OK") in (1, True, "1"), "comment": str(res.get("Comment") or "")[:300]}
