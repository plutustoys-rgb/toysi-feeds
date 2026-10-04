# -*- coding: utf-8 -*-
"""Регрес zoobaza_parser.py на уривку РЕАЛЬНОГО фіду (структура/значення з basmati.com.ua/zoobaza_full.php,
2026-10-04). Мережа не потрібна."""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import zoobaza_parser as z

XML = """<?xml version="1.0" encoding="utf-8"?><yml_catalog><shop><name>x</name>
<categories><category id="105501">Одяг для тварин</category><category id="1059">Сумки та переноски</category>
<category id="1086">Корми для собак</category></categories><offers>
<offer id="3941" available="true"><price>966</price><quantity_in_stock>10</quantity_in_stock><currencyId>UAH</currencyId><categoryId>105501</categoryId><picture>http://p/1</picture><vendorCode>305457</vendorCode><vendor>ЗооБаза</vendor><name>Жилет Барт Zoobaza - 35х54, коричневий</name><param name="Цвет">коричневий</param></offer>
<offer id="3936" available="true"><price>1798</price><quantity_in_stock>10</quantity_in_stock><currencyId>UAH</currencyId><categoryId>105501</categoryId><vendorCode>305452</vendorCode><name>Жилет Барт Zoobaza - 72х116, коричневий</name></offer>
<offer id="3470" available="true"><price>966</price><quantity_in_stock>10</quantity_in_stock><categoryId>1059</categoryId><vendorCode>7559</vendorCode><name>Сумка-переноска Лежебока Zoobaza - 23х35х31, бірюзовий</name></offer>
<offer id="3952" available="false"><price>1798</price><quantity_in_stock>0</quantity_in_stock><categoryId>1059</categoryId><vendorCode>305468</vendorCode><name>Недоступна сумка</name></offer>
<offer id="9001" available="true"><price>250</price><quantity_in_stock>10</quantity_in_stock><categoryId>1086</categoryId><name>Корм</name></offer>
<offer id="9002" available="true"><price></price><categoryId>1059</categoryId><name>Без ціни</name></offer>
</offers></shop></yml_catalog>""".encode("utf-8")

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

c = z.parse_zoobaza_xml(XML)
chk("розібрано 5 (позиція без ціни пропущена)", len(c) == 5 and "9002" not in c)
it = c["3941"]
chk("поля: supplier/vendor_code/category/color/picture",
    it["supplier"] == "ZooBaza" and it["vendor_code"] == "305457" and it["category_name"] == "Одяг для тварин"
    and it["color"] == "коричневий" and it["pictures"] == ["http://p/1"])
chk("cost = price/1.4: 966 → 690.0", it["cost"] == 690.0)
chk("cost 1798 → 1284.29 (цілі ціни, похибка <1)", abs(c["3936"]["cost"] - 1284.0) < 1)
chk("available — з атрибута; stock_flag — прапорець, не залишок", c["3952"]["available"] is False and c["3470"]["stock_flag"] == "10")
f = z.filter_catalog(c, include_clothing=False)
chk("фільтр без одягу: лише доступні не-корми не-одяг (сумка 3470)", set(f) == {"3470"})
f2 = z.filter_catalog(c, include_clothing=True)
chk("фільтр з одягом: +2 жилети; корм і недоступна — НІКОЛИ", set(f2) == {"3470", "3941", "3936"})
chk("корми заборонені навіть з одягом", "9001" not in f2)
r = z.verify_cost_constant(c)
chk("контроль константи: 2 позиції Барт знайдені й ok", [x["ok"] for x in r] == [True, True])
z.ZOOBAZA_FEED_TO_OPT = 1.5
c15 = z.parse_zoobaza_xml(XML)
chk("якщо константа хибна (1.5) — контроль ловить (ok=False)", not all(x["ok"] for x in z.verify_cost_constant(c15)))
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ zoobaza_parser — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
