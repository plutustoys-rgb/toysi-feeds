# -*- coding: utf-8 -*-
"""site_promo.py — знижки власного сайту (рішення власника 13.10.2026 «Даруй знижку за підписку» + Консультант): ПРОМОКОД PLUTUS5 (−5%) і автоматична знижка −10% при двох РІЗНИХ товарах у кошику.

ПРАВИЛА (Консультант, 13.10; усе рахується від ЗАКРИТОЇ формули ціни сайту, див. пам'ять site-price-formula-closed):
  • ціна одиниці після знижки = ЦІЛА гривня вгору: ceil(ціна × (1 − відсоток)) (цілі суми → без копійок у чеку, LiqPay і накладеному платежі);
  • рядок отримує знижку лише якщо після неї ЗАЛИШАЄТЬСЯ покриття обробки: нова_ціна × (1 − еквайринг 2,8%) − собівартість − витрати_на_замовлення ≥ 0; для PLUTUS5 витрати = повні SITE_FULFILL_PER_ORDER (17,81: діє вже від одного товару),
    для −10% = половина (8,905 на позицію: витрата замовлення ділиться на ≥2 позиції); для −10% ще й нова ціна ≥ 20 ₴ (поріг Консультанта; ТЛУМАЧЕННЯ «поріг 20 ₴» = ціна ПІСЛЯ знижки — уточнити, якщо мав на увазі інше);
  • знижки НЕ складаються: береться більша за сумою знижки (при рівності — промокод); промокод PLUTUS5: 1 використання на телефон (рахуються замовлення сайту з promo_code=PLUTUS5, не скасовані; передоплатні неоплачені не рахуються);
  • собівартість береться з ПРИВАТНОГО файлу promo_cost.json (пише stock_snapshot, не в DocumentRoot); нема файлу чи він застарів/нечитабельний → знижок НЕМАЄ (fail-closed: невідома собівартість = не даруємо), покупцю кажемо чесно.
Модуль без мережі й БД (чисті функції + читання приватного файлу); API-обробники в site_order_api.
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
COST_FILE = Path(os.environ.get("PROMO_COST_FILE", str(HERE / "promo_cost.json")))
COST_MAX_AGE = timedelta(hours=6)          # собівартість змінюється рідко; знімок пишеться кожні 15 хв — старший за 6 год означає «збій оновлювача» → знижок нема

PROMO_CODE = "PLUTUS5"
LABEL_CODE = "PLUTUS5"
LABEL_TWO = "TWO10"
ACQUIRING = 0.028                          # = competitor_pricing.PAYMENT_COMMISSION["site"] (звіряється тестом)
FULFILL_FULL = 17.81                       # = competitor_pricing.SITE_FULFILL_PER_ORDER (звіряється тестом)
FULFILL_HALF = FULFILL_FULL / 2            # 8,905 на позицію
MIN_PRICE_TWO = 20                         # ціна ПІСЛЯ знижки −10% не нижче 20 ₴

_cost_cache = {"key": None, "data": None}


def load_cost_map():
    """{id: собівартість(float)} або None (нема файлу / застарів / пошкоджений). Кеш за (mtime, size)."""
    try:
        st = os.stat(COST_FILE)
        key = (st.st_mtime, st.st_size)
        if key != _cost_cache["key"]:
            d = json.loads(Path(COST_FILE).read_text(encoding="utf-8"))
            _cost_cache["data"] = ({str(k): float(v) for k, v in d["c"].items()}, datetime.fromisoformat(d["ts"]))
            _cost_cache["key"] = key
        costs, ts = _cost_cache["data"]
        if datetime.now() - ts > COST_MAX_AGE:
            return None
        return costs
    except Exception:  # noqa: BLE001
        return None


def discounted_unit(price: int, pct: int) -> int:
    """Ціла гривня ВГОРУ: ceil(price × (100 − pct) / 100) цілочисельно (без плаваючої крапки)."""
    num = price * (100 - pct)
    return -(-num // 100)


def _scheme_lines(items: list, costs: dict, pct: int, fulfill: float, min_after: int = 0) -> dict:
    """{id: нова ціна одиниці} лише для рядків, що проходять умову покриття; решта не входить."""
    out = {}
    for it in items:
        pid, price = str(it["toysi_code"]), int(it["price"])
        cost = costs.get(pid)
        if cost is None:
            continue
        new = discounted_unit(price, pct)
        if new >= price or new < min_after:
            continue
        if new * (1 - ACQUIRING) - cost - fulfill >= 0:
            out[pid] = new
    return out


def _discount_total(items: list, lines: dict) -> int:
    return sum((int(it["price"]) - lines[str(it["toysi_code"])]) * int(it["qty"]) for it in items if str(it["toysi_code"]) in lines)


def compute(items: list, costs, code, code_allowed: bool = True, mixed: bool = False) -> dict:
    """items: [{toysi_code, qty, price}] за ціною каталогу. Повертає
    {"applied": "PLUTUS5"|"TWO10"|None, "lines": {id: нова ціна одиниці}, "discount_total": int,
     "code_status": "none" (код не вводили) | "ok" (код застосовано) | "unknown" (такого коду нема) | "used" (уже використано з цього телефону) |
                    "not_applicable" (код дійсний, але жоден рядок не проходить покриття АБО автоматична −10% більша) | "unavailable" (нема собівартості: знижок немає),
     "costs_available": bool}.
    code_allowed=False — промокод уже використано з цього телефону (лишається лише автоматична −10%).
    mixed=True — кошик ЗМІШАНИЙ (Toysi + RoyalToys → ДВА замовлення, ДВІ витрати SITE_FULFILL_PER_ORDER): автоматична −10% (TWO10) НЕ застосовується (рішення Консультанта 23.10.2026:
    її умова покриття рахує половину витрати замовлення на позицію, а тут витрат вдвічі більше); повернення — лише з запобіжником внеску кожної частини (поки не збудовано). Промокод PLUTUS5 (кожен рядок покриває ПОВНУ витрату) діє як раніше."""
    res = {"applied": None, "lines": {}, "discount_total": 0, "code_status": "none", "costs_available": costs is not None}
    raw = (code or "").strip().upper()
    code_valid = raw == PROMO_CODE
    if raw and not code_valid:
        res["code_status"] = "unknown"
    if costs is None:
        if code_valid:
            res["code_status"] = "unavailable"
        return res
    if code_valid and not code_allowed:
        res["code_status"] = "used"
    a = _scheme_lines(items, costs, 5, FULFILL_FULL) if (code_valid and code_allowed) else {}
    b = _scheme_lines(items, costs, 10, FULFILL_HALF, MIN_PRICE_TWO) if (len({str(it["toysi_code"]) for it in items}) >= 2 and not mixed) else {}
    da, db = _discount_total(items, a), _discount_total(items, b)
    if da > 0 and da >= db:
        res.update(applied=LABEL_CODE, lines=a, discount_total=da, code_status="ok")
    elif db > 0:
        res.update(applied=LABEL_TWO, lines=b, discount_total=db)
        if code_valid and code_allowed:
            res["code_status"] = "not_applicable"       # код дійсний, але більша автоматична знижка: код НЕ використано
    elif code_valid and code_allowed:
        res["code_status"] = "not_applicable"
    return res


def apply_to_items(items: list, result: dict) -> tuple:
    """Нові items із цінами після знижки (list_price зберігає каталожну) і нова сума. Вхід не мутується."""
    new_items = []
    for it in items:
        it2 = dict(it)
        it2["list_price"] = int(it["price"])
        new = result["lines"].get(str(it["toysi_code"]))
        if new is not None:
            it2["price"] = new
        new_items.append(it2)
    return new_items, sum(int(i["price"]) * int(i["qty"]) for i in new_items)
