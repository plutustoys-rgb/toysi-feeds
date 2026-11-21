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
ПРИПУЩЕННЯ, ЯКІ ПЕРЕВІРИТЬ ПЕРШЕ ЖИВЕ (пробне власника) замовлення: `DeliveryCity`/`DeliveryWarehouse` = Ref міста й відділення НП (36 символів; в API не описано), `Shipment` = DeliveryServiceWarehouse, `deliveryService` = NewPost,
`delivery_payer` = recipient. Це лише тіло запиту; поки KV_ORDERS_LIVE≠1 жодного запиту не відбувається."""
import json
import re
import sys
from pathlib import Path

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
        try:
            qty = int(it.get("qty"))
        except (TypeError, ValueError):
            raise OrderBlocked(f"{code}: кількість некоректна") from None
        if qty < 1 or qty > MAX_QTY_PER_LINE:
            raise OrderBlocked(f"{code}: кількість {qty} поза 1…{MAX_QTY_PER_LINE} — перевірте вручну")
        try:
            cost = float(src.get("cost"))
        except (TypeError, ValueError):
            cost = 0.0
        wh = str(src.get("warehouse") or "").strip()
        if cost <= 0 or wh not in WAREHOUSES:
            raise OrderBlocked(f"{code}: у даних постачальника нема собівартості або склад «{wh}» не з {WAREHOUSES}")
        try:
            price = float(it.get("price"))
        except (TypeError, ValueError):
            raise OrderBlocked(f"{code}: ціна в замовленні некоректна") from None
        if price < cost:
            raise OrderBlocked(f"{code}: ціна клієнта {price:g} нижча за собівартість {cost:g} — збиток, потрібне рішення")
        if code in merged:
            merged[code]["qty"] += qty
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
    if order.get("cancelled_at") or str(order.get("status") or "") in ("cancelled", "sandbox_test"):
        raise OrderBlocked("замовлення скасоване/тестове — не передаємо")
    if not order.get("checkbox_receipt_id") and not order.get("checkbox_ettn_registered_at"):
        raise OrderBlocked("фіскальний чек ще не видано — передачу постачальнику не починаємо (спершу чек)")


def build_create_payload(order: dict, items: list, ids: dict, recip: tuple) -> dict:
    """Тіло POST orders/create. ids={'OrganizationID','ClientID','ContractID','shopID'} (з clients/list). Відділення НП — Ref з np_ref_id (авторитетний), місто — np_city_ref. Без `redelivery` (лише передоплата)."""
    for k in ("OrganizationID", "ClientID", "ContractID", "shopID"):
        if not str(ids.get(k) or "").strip():
            raise OrderBlocked(f"clients/list не дав {k} — оформлення неможливе")
    city, wh = str(order.get("np_city_ref") or "").strip(), str(order.get("np_ref_id") or "").strip()
    if not city or not wh:
        raise OrderBlocked("відділення НП не визначено (нема np_city_ref/np_ref_id) — уточніть у клієнта")
    return {
        "OrganizationID": ids["OrganizationID"], "ClientID": ids["ClientID"], "ContractID": ids["ContractID"],
        "Shipment": "DeliveryServiceWarehouse", "deliveryService": "NewPost", "shopID": ids["shopID"],
        "DeliveryCity": city, "DeliveryWarehouse": wh,
        "Comment": f"Сайт plutustoys.com.ua, замовлення {order.get('internal_order_id')}",
        "recipient": recip[0], "recipient_phone": recip[1], "delivery_payer": "recipient",
        "my_ttn": True,
        "Items": [{"Product": i["product_id"], "Warehouse": i["warehouse"], "Quantity": i["qty"]} for i in items],
    }


def dry_run(order: dict, supplier: dict, ids: dict) -> dict:
    """Звіт «що б зробили» БЕЗ запису: {'ok': bool, 'problems': [...], 'items': [...], 'payload': {...}|None, 'cost_total': float}. Ніколи не кидає OrderBlocked назовні."""
    rep = {"order": order.get("internal_order_id"), "ok": False, "problems": [], "items": [], "payload": None, "cost_total": 0.0}
    try:
        _kv_only(order)
        items = map_items(order, supplier)
        recip = recipient(order)
        payload = build_create_payload(order, items, ids, recip)
    except OrderBlocked as e:
        rep["problems"].append(str(e))
        return rep
    rep["items"] = [(i["code"], i["qty"], i["warehouse"]) for i in items]
    rep["cost_total"] = round(sum(i["cost"] * i["qty"] for i in items), 2)
    rep["payload"] = payload
    rep["ok"] = True
    return rep


def interpret_create(res: dict) -> dict:
    """Відповідь orders/create → {'number','id','reserved': bool,'comment'}. Номер і id обов'язкові (інакше OrderBlocked: результат неповний — ручна звірка). reserved = status_OK==1;
    0 → замовлення СТВОРЕНО, але не зарезервовано (Comment): конвеєр НЕ йде далі до ТТН, рішення людини."""
    if not isinstance(res, dict) or not str(res.get("number") or "").strip() or not str(res.get("id") or "").strip():
        raise OrderBlocked("відповідь orders/create без number/id — звірте замовлення в кабінеті KIDDISVIT вручну")
    return {"number": str(res["number"]).strip(), "id": str(res["id"]).strip(), "reserved": res.get("status_OK") in (1, True, "1"), "comment": str(res.get("Comment") or "")[:300]}
