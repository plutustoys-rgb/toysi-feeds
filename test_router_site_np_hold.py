#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_router_site_np_hold.py — fail-closed для НП-замовлень САЙТУ + захист парсера відділення (11.10.2026, аудит PR #671 Shopify-code; реальний інцидент
PT-20261010-142322: «369» руками → Toysi «адресна доставка»). Інваріанти: site+НП без валідного np_warehouse_number → НЕ в Toysi, алерт, замовлення лишається;
інший перевізник/самовивіз у тексті → утримання; автокомпліт/валідний номер → пропускається; Prom/Rozetka/EVA без змін; «відділення166» → 166 (було 6);
стіна цифр і сміття не стають shipping_warehouse_id. Самодостатній (мережа/Toysi замоковані)."""
import sys

import order_router as orr

orr.count_cities_named = lambda *a, **k: 1   # мережа НП не потрібна: місто вважається унікальним (перевірку тезок тестує test_site_name_city_hold.py)

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def order(**kw):
    base = dict(internal_order_id="site_T1", order_id="T1", platform="site", carrier="nova_poshta", customer_name="Іван Петренко", phone="380671112233",
                payment_method="cod", np_branch="м. Київ, Відділення №369", np_warehouse_number="369", np_city_ref=None,
                items=[{"toysi_code": "1", "name": "x", "qty": 1, "price": 100}])
    base.update(kw)
    return base


# ── утримання
chk("site + валідний np_warehouse_number → не утримуємо", orr.site_np_hold_reason(order(), None) == "")
chk("site + «369» руками, np_warehouse_number відсутній (як інцидент) → утримуємо", "нема валідного номера" in orr.site_np_hold_reason(order(np_branch="м. Київ, 369", np_warehouse_number=None), None))
chk("site + порожній номер → утримуємо", orr.site_np_hold_reason(order(np_warehouse_number=""), None) != "")
chk("site + номер-сміття/стіна цифр → утримуємо", all(orr.site_np_hold_reason(order(np_warehouse_number=x), None) for x in ("abc", "1" * 4301, "0x1F", "12 13", "1234567")))
for txt in ("Київ, Укрпошта №9", "Київ, Justin №733", "Київ, Meest №473", "Київ, Самовивіз відд 157", "Київ, відділення 118 Укрпошта"):
    chk(f"site + інший перевізник/самовивіз → утримуємо: {txt}", "іншого перевізника" in orr.site_np_hold_reason(order(np_branch=txt), None))
chk("site + поштомат (номер 55902) → пропускаємо", orr.site_np_hold_reason(order(np_branch="м. Київ, Поштомат №55902", np_warehouse_number="55902"), None) == "")
chk("НЕ site (prom/rozetka/eva) без номера → поведінка не змінена (не утримуємо)", all(orr.site_np_hold_reason(order(platform=p, np_warehouse_number=None, np_branch="м. Київ, 369"), None) == "" for p in ("prom", "rozetka", "eva")))
chk("site через Укрпошту (carrier ukrposhta) → не наша логіка, не утримуємо", orr.site_np_hold_reason(order(carrier="ukrposhta", np_warehouse_number=None), None) == "")

# ── парсер
chk("«відділення166» (склеєні цифри) → 166, не 6", orr.parse_np_branch("Київ, відділення166")[1] == "166")
chk("«відділення №369» → 369", orr.parse_np_branch("Київ, Відділення №369")[1] == "369")
chk("«відд. 12» → 12; «№7» → 7", orr.parse_np_branch("Львів, відд. 12")[1] == "12" and orr.parse_np_branch("Львів, №7")[1] == "7")
chk("Prom-текст «№253 (до 30 кг на одне місце)» → 253", orr.parse_np_branch("м. Київ (Київська обл.), №253 (до 30 кг на одне місце): вул. ...")[1] == "253")

# «кв./оф./буд./під'їзд №N» — не відділення (аудит #671); справжній маркер ПІСЛЯ адреси все одно знаходиться
for txt in ("м. Київ, вул. Лесі Українки 5, кв. №162", "м. Харків, просп. Науки, 14, оф. №301", "Київ, буд. №7", "Львів, кімн. №12"):
    chk(f"«{txt}» → номер відділення порожній (це квартира/офіс)", orr.parse_np_branch(txt)[1] == "")
chk("«кв. №12, відділення №15» → 15", orr.parse_np_branch("Київ, вул. Шевченка 4, кв. №12, відділення №15")[1] == "15")
chk("Поштомат №7921: …, під'їзд №1 → 7921 (перший збіг — справжній)", orr.parse_np_branch("Бровари, Поштомат №7921: вул. Мудрого, 28а, під'їзд №1 (ТІЛЬКИ ДЛЯ МЕШКАНЦІВ)")[1] == "7921")
chk("«№47 (до 10 кг): вул. Чукаріна, оф. 3» → 47", orr.parse_np_branch("Львів, №47 (до 10 кг): вул. Чукаріна В., 14, оф. 3")[1] == "47")

chk("ліва межа слова: «пров. Лісобуд №7» — «буд» усередині слова НЕ контекст будинку (номер лишається)", orr.parse_np_branch("Київ, пров. Лісобуд №7")[1] == "7")
chk("юнікодні цифри (٣٦٩) не вважаються валідним номером відділення (hold)", orr.site_np_hold_reason(order(np_warehouse_number="٣٦٩"), None) != "")
# ── wh_number: стіна цифр не стає shipping_warehouse_id
orr.settlement_raion = lambda *a, **k: ""
big = orr.build_toysi_order(order(platform="prom", np_warehouse_number=None, np_branch="м. Київ, відділення " + "1" * 4301))
chk("стіна цифр не йде в shipping_warehouse_id", big is not None and not big.get("shipping_warehouse_id"))
ok = orr.build_toysi_order(order(platform="prom", np_warehouse_number="369"))
chk("валідний номер 369 іде як shipping_warehouse_id", ok is not None and str(ok.get("shipping_warehouse_id")) == "369")

# ── route_order: утримане НЕ доходить до submit_order, є алерт
calls = {"submit": 0, "alert": []}
orr.submit_order = lambda *a, **k: calls.__setitem__("submit", calls["submit"] + 1) or {"accepted": True}
orr.send_throttled_alert = lambda key, text, cooldown_sec=0: calls["alert"].append((key, text, cooldown_sec)) or True
orr._check_prom_not_cancelled = lambda *a, **k: True
orr._check_eva_not_cancelled = lambda *a, **k: True
orr._check_rozetka_not_cancelled = lambda *a, **k: True
orr.route_order(None, order(np_branch="м. Київ, 369", np_warehouse_number=None), toysi_catalog={"1": {"stock": 99}})
chk("route_order: утримане site-замовлення НЕ викликає submit_order", calls["submit"] == 0)
chk("route_order: алерт один, ключ site_np_hold:<id>, cooldown 6 год, текст називає замовлення й що робити", len(calls["alert"]) == 1 and calls["alert"][0][0] == "site_np_hold:site_T1" and calls["alert"][0][2] == 6 * 3600 and "site_T1" in calls["alert"][0][1] and "виправіть" in calls["alert"][0][1])
print()
if F:
    print("FAILED:", F)
    sys.exit(1)
print("ALL OK")
