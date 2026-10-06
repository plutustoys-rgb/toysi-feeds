# -*- coding: utf-8 -*-
"""akty_zvirka.py: парсери актів (фрагменти реальних PDF Вчасно, без реквізитів), період, класифікація «в книзі», збір потоків і
рядки звіту. Суми звірено з журналом КОДВ (142): NovaPay 5,89/10,38; RozetkaPay 1,14/105,43; EVA 103,78+69,24; Rozetka
178,97+119,30×1,2=322,13 і 51,00×1,2=61,20; Prom 276,72×1,2=332,06, (241,43+29,17)×1,2=324,72."""
import os, sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import akty_zvirka as az
import kandydaty_registry as kr

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

NOVA = "4. Сума винагороди Платіжної установи за надані послуги 10,38 грн., без ПДВ. Сума винагороди за надані\nпослуги зазначається без ПДВ."
NOVA_BIG = "2. ... прийняла від Платників 1 175,00 грн.\n4. Сума винагороди Платіжної установи за надані послуги 5,89 грн., без ПДВ."
RPAY = ("1 Точки видачі (cash) 276,00 4,14\n2 Точки видачі (pos) 83,00 1,25\n3 Інтернет еквайринг 4 610,35 76,73\n4 Payparts iban 630,00 23,31\nРазом 5 599,35 105,43\n* Винагорода")
EVA_ROY = ("АКТ ПРО ВИКОРИСТАННЯ ТОРГОВЕЛЬНИХ МАРОК\nВсього 11,00 1153,61 - 103,78\n"
           "2. Загальна сума роялті за Серпень 2026 р., сплачена за цим Актом, склала 103,78 грн (Сто три) без урахування")
EVA_ACC = ("АКТ НАДАННЯ ПОСЛУГИ ДОСТУПУ ДО ОНЛАЙН-ПЛАТФОРМИ\nВсього 11,00 1153,61 - 69,24\n"
           "Загальна сума за послуги складає 69,24 грн (Шістдесят дев'ять), в тому числі ПДВ у розмірі 11,54 грн")
ROZ_ROY = "1 Роялті за користування торгової марки Rozetka 1 послуга 178,97 178,97\nВсього без ПДВ: 178,97\n"
ROZ_ACC = ("1 Організація видачі відправлень (Спеціальні умови) 6 шт 8,50 51,00\n"
           "Безумовна щомісячна плата за Доступ до інтернет-сайту\n2 1 послуга 100,00 100,00\n"
           "www.rozetka.com.ua та онлайн-сервісів на ньому\n"
           "Доступ до інтернет-сайту www.rozetka.com.ua та онлайн-сервісів на\n3 1 послуга 119,30 119,30\n"
           "ньому в зв’язку з обсягом продажу\nВсього: 270,30\n")
PROM_AUG = ("1 Надання доступу до онлайн-сервісу Онлайн Каталогу ProSale 1 грн 241,43 241,43\nелектронного маркетплейсу Prom.ua\n"
            "2 Компенсація вартості послуги з організації перевезення відправлень 1 грн 29,17 29,17\nНовою Поштою\n"
            "3 Надання доступу до програмної продукції у вигляді 1 грн 611,97 611,97\nонлайн-сервісу (електронного маркетплейсу) Prom.ua,\n"
            "3.1 Надання доступу до програмної продукції у з 01.08.2026 по 1 грн 611,97 611,97\nВсього: 882,57\nПДВ: 176,52\n")
PROM_AUG = "\n" + PROM_AUG
PROM_JUL = ("1 Надання доступу до онлайн-сервісу Онлайн Каталогу ProSale 1 грн 276,72 276,72\n"
            "2 Надання доступу до програмної продукції у вигляді 1 грн 404,50 404,50\n"
            "2.1 Надання доступу до програмної продукції у з 24.07.2026 по 1 грн 157,93 157,93\nВсього: 681,22\n")
PROM_JUL = "\n" + PROM_JUL

chk("NovaPay: 10,38", az.parse_novapay(NOVA)["total"] == 10.38)
chk("NovaPay: 5,89 (не плутає з 1 175,00)", az.parse_novapay(NOVA_BIG)["total"] == 5.89)
chk("NovaPay: нема рядка → None", az.parse_novapay("нічого")["total"] is None)
chk("RozetkaPay: Разом винагорода 105,43", az.parse_rozetkapay(RPAY)["total"] == 105.43)
chk("EVA роялті 103,78", az.parse_eva(EVA_ROY)["part"] == "royalty" and az.parse_eva(EVA_ROY)["total"] == 103.78)
chk("EVA доступ 69,24", az.parse_eva(EVA_ACC)["part"] == "access" and az.parse_eva(EVA_ACC)["total"] == 69.24)
r = az.parse_rozetka(ROZ_ROY); chk("Rozetka роялті-акт 178,97", r["doc"] == "royalty" and r["royalty_novat"] == 178.97)
r = az.parse_rozetka(ROZ_ACC)
chk("Rozetka «доступ»: логістика 51,00, щомісячна 100,00, обсяг 119,30",
    (r["doc"], r["logistics_novat"], r["monthly_novat"], r["volume_novat"]) == ("access", 51.00, 100.00, 119.30))
p = az.parse_prom(PROM_AUG)
chk("Prom серпень: ProSale+доставка 270,60 без ПДВ → 324,72; пакет 611,97 виключено",
    p["commission_novat"] == 270.60 and p["total"] == 324.72 and p["package_novat"] == 611.97)
chk("Prom липень: 276,72×1,2 = 332,06 (пакет і 2.1 виключено)", az.parse_prom(PROM_JUL)["total"] == 332.06)

# --- контрольні суми: нерозпізнана позиція НЕ дає хибного числа, а None (аудит #625 M-3) ---
bad = (PROM_AUG.replace("3 Надання доступу", "3 Оплата частинами 12 шт 5,00 60,00\n3 Надання доступу")   # рядок із «шт» не розпізнається
       .replace("Всього: 882,57", "Всього: 942,57"))   # а в «Всього:» акта ця позиція є
chk("Prom: позиція, яку не розпізнано (шт замість грн) → None, а не 289,72", az.parse_prom(bad)["total"] is None and az.parse_prom(bad).get("note"))
nopack = PROM_AUG.replace("програмної продукції у вигляді", "Пакет Prom мікс 6000 у вигляді")   # пакет без слів «програмної продукції»
chk("Prom: пакет під іншою назвою не маскується під правильну суму 324,72", az.parse_prom(nopack)["total"] != 324.72)
rpay3 = RPAY.replace("Разом 5 599,35 105,43", "Разом 5 599,35 99,00")
chk("RozetkaPay: сума рядків ≠ «Разом» → None", az.parse_rozetkapay(rpay3)["total"] is None)
roz_bad = ROZ_ACC.replace("Всього: 270,30", "Всього: 300,00")
chk("Rozetka «доступ»: позиції ≠ «Всього» → усе None", az.parse_rozetka(roz_bad)["logistics_novat"] is None and az.parse_rozetka(roz_bad).get("note"))
eva_bad = EVA_ROY.replace("- 103,78", "- 99,00")
chk("EVA: «Всього» таблиці ≠ сума в тексті → None", az.parse_eva(eva_bad)["total"] is None)

# --- період із тексту акта (аудит #625 L-2) ---
chk("період з тексту: «за Серпень 2026 р.»", az.period_from_text("№ 154242 за Серпень 2026 р.") == "2026-08")
chk("період з тексту: «від 31 серпня 2026»", az.period_from_text("№ TA00482473 від 31 серпня 2026 р.") == "2026-08")
chk("період з тексту: «по 31.08.2026р.»", az.period_from_text("з 01.08.2026р. по 31.08.2026р.") == "2026-08")
chk("період з тексту: нема → None", az.period_from_text("нічого") is None)

chk("період: 31.07 → 2026-07", az.act_period("2026-07-31") == "2026-07")
chk("період: 16.09 (EVA, акт за серпень) → 2026-08", az.act_period("2026-09-16") == "2026-08")
chk("період: 05.01 → попередній рік", az.act_period("2027-01-05") == "2026-12")

chk("month_ua: dd.mm.yyyy і ISO", kr.month_ua("26.09.2026") == "вересень 2026" and kr.month_ua("2026-10-01") == "жовтень 2026")

cell_decl = "ТОВ «НоваПей» (ЄДРПОУ 38324133), Акт прийому-передачі наданих послуг за липень 2026 від 31.07.2026 (Вчасно BO0000489750), Договір"
cell_ment = "Prom.ua №416114712, накладений платіж. " + "x" * 300 + " див. акт BO0000489750"
chk("класифікація: власний рядок акта (Вчасно …) → declared", az.classify_cell("BO0000489750", cell_decl) == "declared")
chk("класифікація: згадка далеко в тексті → mentioned", az.classify_cell("BO0000489750", cell_ment) == "mentioned")
chk("класифікація: нема → None", az.classify_cell("BO0000489750", "щось інше") is None)
cell_note = ("Prom.ua №419818854, накладений платіж.\n🔵 i9 ВИПРАВЛЕНО 2026-10-05: комісію Prom 15.17 прибрано — вона вже в Акті Prom №11865348 "
             "за серпень, рядком 80.")
chk("класифікація: приміткова згадка «в Акті Prom №…» у рядку продажу НЕ declared (аудит #625 M-1)",
    az.classify_cell("UA-00011865348", cell_note) == "mentioned")

def act(vendor, doc, date, parsed):
    return {"vendor": vendor, "doc_id": doc, "date": date, "period": az.act_period(date), "path": "x", "parsed": parsed, "error": None}
acts = [act("novapay", "BO1", "2026-08-31", az.parse_novapay(NOVA)),
        act("eva", "RU1", "2026-09-16", az.parse_eva(EVA_ROY)), act("eva", "RU2", "2026-09-16", az.parse_eva(EVA_ACC)),
        act("rozetka", "TA1", "2026-08-31", az.parse_rozetka(ROZ_ACC)), act("rozetka", "TA2", "2026-08-31", az.parse_rozetka(ROZ_ROY)),
        act("prom", "UA1", "2026-08-31", az.parse_prom(PROM_AUG))]
st = az.act_streams(acts)
chk("потік EVA серпня = 103,78 + 69,24 = 173,02", st[("eva", "2026-08")]["total"] == 173.02)
chk("потік Rozetka роялті = 178,97 + 119,30×1,2 = 322,13", st[("rozetka_royalty", "2026-08")]["total"] == 322.13)
chk("потік Rozetka логістика = 51,00×1,2 = 61,20", st[("rozetka_logistics", "2026-08")]["total"] == 61.20)
only_roy = az.act_streams([act("rozetka", "TA2", "2026-08-31", az.parse_rozetka(ROZ_ROY))])
chk("Rozetka без акта «доступ» → сума None і пояснення (не вгадує)",
    only_roy[("rozetka_royalty", "2026-08")]["total"] is None and only_roy[("rozetka_royalty", "2026-08")]["missing"])
only_eva = az.act_streams([act("eva", "RU1", "2026-09-16", az.parse_eva(EVA_ROY))])
chk("EVA з одним актом → None, а не часткова сума", only_eva[("eva", "2026-08")]["total"] is None)

cands = {"novapay": {"2026-08": {"sum": 10.38, "n": 8, "orders": [("a", 10.38)]}},
         "prom": {"2026-08": {"sum": 606.81, "n": 20, "orders": [("o1", 100.0)]}},
         "eva": {"2026-09": {"sum": 5.0, "n": 1, "orders": []}}}
book = {"BO1": {"declared": [190], "mentioned": [25]}, "RU1": {"declared": [192], "mentioned": []}, "RU2": {"declared": [], "mentioned": []}}
rows = {(r["stream"], r["period"]): r for r in az.build_rows(acts, cands, book)}
chk("NovaPay серпня: ✅ збіг, рядок 190, згадки окремо", rows[("novapay", "2026-08")]["status"] == "✅ збіг"
    and rows[("novapay", "2026-08")]["book_rows"] == [190] and rows[("novapay", "2026-08")]["book_mentions"] == [25])
chk("Prom серпня: ⚠️ різниця +282,09 (неповні дані)", rows[("prom", "2026-08")]["status"].startswith("⚠️ різниця")
    and rows[("prom", "2026-08")]["diff"] == 282.09 and not rows[("prom", "2026-08")]["complete"])
chk("EVA серпня: акт є, кандидатів нема; другий акт НЕ в книзі → видно (аудит #625 H-1)",
    rows[("eva", "2026-08")]["status"].startswith("кандидатів нема") and "RU2" in rows[("eva", "2026-08")]["not_in_book"]
    and "RU2: НЕМАЄ" in az._book_cell(rows[("eva", "2026-08")]) and "RU1: р.192" in az._book_cell(rows[("eva", "2026-08")]))
rows_nb = {(r["stream"], r["period"]): r for r in az.build_rows(acts, cands, None)}
chk("книгу не прочитано → «невідомо», а не «нема»", "невідомо" in az._book_cell(rows_nb[("novapay", "2026-08")]))
chk("EVA вересня: кандидати є, акта нема", rows[("eva", "2026-09")]["status"] == "акта нема")
md = az.render(list(rows.values()), "2026-10-06")
chk("звіт: заголовок таблиці, ⚠️-розділ із замовленнями", "| Місяць |" in md and "Замовлення потоків із різницею" in md and "o1 100.00" in md)

import kandydaty_backfill_fee as bf
reg = {"novapay_registry:T1": {"source": "novapay_registry", "key": "T1", "status": "open", "fee": None, "summary": "x"},
       "novapay_registry:T2": {"source": "novapay_registry", "key": "T2", "status": "open", "fee": 1.0, "summary": "x"},
       "rozetkapay_registry:9:acquiring": {"source": "rozetkapay_registry", "key": "9:acquiring", "status": "open", "fee": None},
       "novapay_registry:T3": {"source": "novapay_registry", "key": "T3", "status": "resolved", "fee": None}}
ch = bf.plan(reg, {"T1": {"commission": 0.84, "amount_received": 168, "amount_net": 167.16, "internal_order_id": "rozetka_9", "date": "26.09.2026"},
                   "T3": {"commission": 9.0}}, {"9:acquiring": 5.48})
d = {c[0]: c for c in ch}
chk("backfill: NovaPay open без fee заповнено (0.84) і summary з копійками",
    d["novapay_registry:T1"][1] == 0.84 and "винагорода НП 0.84" in d["novapay_registry:T1"][2])
chk("backfill: RozetkaPay open → fee 5.48 без перебудови summary", d["rozetkapay_registry:9:acquiring"][1:] == (5.48, None))
chk("backfill: запис з fee і resolved не чіпаються", "novapay_registry:T2" not in d and "novapay_registry:T3" not in d)
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ akty_zvirka — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
