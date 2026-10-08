# -*- coding: utf-8 -*-
"""cod_amount.py — сума накладеного платежу Нової Пошти = ЦІЛА гривня, і чек Checkbox на ту саму суму.

ПРОБЛЕМА (головний бухгалтер, 06–07.10.2026, підтверджено Аудитором і NP API `TrackingDocument.getStatusDocuments`): Нова Пошта приймає накладений
платіж ЦІЛИМИ гривнями (ТТН 20451489778386: Toysi-запис «Післяплата: 175.68», у НП `AfterpaymentOnGoodsCost: 175`), тобто копійки відкидаються (підлога).
А ми видавали фіскальний чек на `sum(price*qty)` з копійками (Prom №416114712: чек 39,23, НП прийняла 39,00). Чек показував більше, ніж ФОП реально отримав:
за III кв. 13,41 ₴ (29 замовлень). Перевірено на orders.db: сума дробових частин COD-замовлень із чеком у III кв. = 13,41 — ТОЧНО ПІДЛОГА (а не «до найближчого»:
для того сума знакових різниць була б −1,59).

РІШЕННЯ (норма — ДПС, Положення №13: у рядку чека «СУМА» стоїть сума після заокруглення/знижки, і саме вона є сумою розрахунку; Наказ бухгалтера 07.10):
  1. `collected_cod_amount(total)` — сума, яку НП реально збере: підлога до цілих гривень. У Toysi (`moneyback`) і в чек іде ОДНЕ й те саме число.
  2. `fit_goods_to_total(goods, target)` — рядки чека підганяються під цю суму: ціна ОДНІЄЇ одиниці зменшується на копійки (рядок з qty>1 розщеплюється на (qty−1)
     за початковою ціною + 1 за зниженою). Сума рядків = сума оплати до копійки. Нових полів Checkbox API не потрібно (лише goods/price/quantity), тож форма запиту
     та сама, що й для всіх попередніх чеків.
Лише carrier=nova_poshta (Укрпошта, Rozetka Delivery, передоплата — без змін). Уже видані чеки НЕ чіпаються (що з ними — рішення бухгалтера).
"""
from decimal import Decimal, ROUND_FLOOR


def order_total(items) -> float:
    """sum(price*qty) у гривнях, 2 знаки."""
    return round(sum((i.get("price") or 0) * (i.get("qty") or 1) for i in (items or [])), 2)


def collected_cod_amount(total) -> float:
    """Сума накладеного платежу, яку НП реально збере: підлога до цілої гривні (39,23 → 39; 175,68 → 175; 121,00 → 121)."""
    d = Decimal(str(round(float(total or 0), 2)))
    return float(d.to_integral_value(rounding=ROUND_FLOOR))


def fit_goods_to_total(goods: list, target_total: float) -> list:
    """Рядки чека → той самий список, але з сумою рівно `target_total` (<= початкової суми, різниця < 1 ₴).
    Різниця (копійки) віднімається від ціни ОДНІЄЇ одиниці найдорожчого рядка; якщо в рядку qty>1 — він розщеплюється на два.
    Вхід не мутується. Якщо сума вже збігається — повертає копію без змін. Розбіжність ≥ 1 ₴ (або цільова > початкової) — ValueError:
    це не заокруглення, а помилка даних, мовчки підганяти не можна."""
    lines = [dict(g) for g in goods]
    cur_kop = sum(round(g["price"] * 100) * int(g.get("qty", 1)) for g in lines)
    tgt_kop = round(target_total * 100)
    diff = cur_kop - tgt_kop
    if diff == 0:
        return lines
    if diff < 0 or diff >= 100:
        raise ValueError(f"розбіжність суми чека {diff} коп. виходить за межі заокруглення (0 < diff < 100)")
    idx = max(range(len(lines)), key=lambda i: round(lines[i]["price"] * 100))
    line = lines[idx]
    price_kop = round(line["price"] * 100)
    if price_kop - diff < 100:
        raise ValueError("ціна одиниці після заокруглення була б нижчою за 1 ₴")
    new_price = (price_kop - diff) / 100
    qty = int(line.get("qty", 1))
    if qty <= 1:
        line["price"] = new_price
        lines[idx] = line
    else:
        rest = dict(line)
        rest["qty"] = qty - 1
        one = dict(line)
        one["qty"] = 1
        one["price"] = new_price
        lines[idx:idx + 1] = [rest, one]
    assert sum(round(g["price"] * 100) * int(g.get("qty", 1)) for g in lines) == tgt_kop
    return lines
