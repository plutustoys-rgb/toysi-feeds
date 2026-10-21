#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_rt_titles.py — 21.10.2026 (формула SEO): title картки RoyalToys = назва постачальника, обрізана ПО МЕЖІ СЛОВА так, щоб разом із суфіксом «— купити | PlutusToys» було ≤ 65 символів,
БЕЗ «…» і БЕЗ внутрішнього коду rt-…; унікальність лише за потреби — до кінця назви додається артикул постачальника (vendorCode; без префікса rt-); код rt-… у title не друкується ніколи.
Не вийшло (нема артикула / артикули однакові) — картка у списку невдач, збірка не падає. Чиста логіка: _cut_title_words / rt_title / assign_rt_title_codes + product_title_for для rt."""
import os
import re
import sys
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


SUF = bs._PROD_SUFFIX
SUF_OOS = bs._PROD_SUFFIX_OOS

# (1) обрізання: по межі слова, без «…», ≤ 65 разом із суфіксом
long_name = "Дитячий конструктор Qman військовий транспорт ракетна установка 74-101 деталь додатковий опис"
t = bs.rt_title(long_name)
chk(f"довга назва: ≤ 65 символів із суфіксом ({len(t)}), без «…», суфікс «— купити | PlutusToys» на місці", len(t) <= 65 and "…" not in t and t.endswith(SUF))
body = t[: -len(SUF)]
chk(f"обрізано ПО МЕЖІ СЛОВА (останнє слово — повне слово назви): «{body}»", long_name.startswith(body) and (len(body) == len(long_name) or long_name[len(body)] == " "))
short = "Гра Мафія"
chk("коротка назва не міняється", bs.rt_title(short) == short + SUF)
# (2) висячі розділювачі, прийменники, незакриті лапки/дужки
chk("висячий прийменник прибрано (…для → …)", not bs._cut_title_words("Набір для малювання розмальовка для дітей від трьох років і старше", 30).endswith(" для"))
c = bs._cut_title_words('Дитяча іграшка "Інтерактивний робот-трансформер з функцією запису голосу', 40)
chk(f"незакриті лапки не лишаються: «{c}»", c.count('"') % 2 == 0 and "Інтерактивний" not in c.split('"')[-1][:0] or c.count('"') % 2 == 0)
c = bs._cut_title_words("Конструктор Qman (військовий транспорт з ракетною установкою) 1418Q", 40)
chk(f"незакрита дужка не лишається: «{c}»", c.count("(") == c.count(")"))
chk("ніколи не «…» і не завершується розділювачем", all(not bs._cut_title_words(n, r).endswith(("…", ",", "-", "—", ":", ";")) for n in (long_name, "А, Б, В, Г, Д, Е, Ж, З, И, К, Л", "Назва — продовження —") for r in range(8, 60) if len(n) > r))   # лише справді обрізані (назва, що вміщується, лишається як є)
# (3) унікальність: артикул у кінці; код rt- ніколи
A = {"id": "rt-1", "name": "Дерев'яна настільна гра для дітей і дорослих розвиваюча логіка", "vendor_code": "PSD178", "rt": 1}
B = dict(A, id="rt-2", vendor_code="PSF021")
C = dict(A, id="rt-3", vendor_code="")          # без артикула
U = {"id": "rt-4", "name": "Унікальна іграшка", "vendor_code": "X1", "rt": 1}
codes, fail = bs.assign_rt_title_codes([(A, True), (B, True), (U, True)])
tA, tB = bs.rt_title(A["name"], True, codes.get("rt-1")), bs.rt_title(B["name"], True, codes.get("rt-2"))
chk(f"дублікат після обрізання: обом додано артикул, title різні й ≤ 65 («{tA}» / «{tB}»)", codes == {"rt-1": "PSD178", "rt-2": "PSF021"} and tA != tB and len(tA) <= 65 and len(tB) <= 65 and tA.endswith("PSD178" + SUF) and "…" not in tA + tB)
chk("унікальна картка артикула НЕ отримує (код лише за потреби)", "rt-4" not in codes and fail == [])
codes2, fail2 = bs.assign_rt_title_codes([(A, True), (C, True)])
chk(f"один із дублів без артикула: інший отримує артикул, а ця лишається зі «своїм» базовим title — унікально, невдач нема ({fail2})", codes2 == {"rt-1": "PSD178"} and fail2 == [])
C2 = dict(C, id="rt-7")
codes2b, fail2b = bs.assign_rt_title_codes([(C, True), (C2, True)])
chk(f"обидва дублі без артикула: не падає, обидві в списку невдач ({fail2b})", set(fail2b) == {"rt-3", "rt-7"} and not codes2b)
D = dict(A, id="rt-5", vendor_code="PSD178")     # однаковий артикул
codes3, fail3 = bs.assign_rt_title_codes([(A, True), (D, True)])
chk(f"однакові артикули: title лишаються однаковими → обидві в невдачах ({fail3})", set(fail3) == {"rt-1", "rt-5"})
codes4, fail4 = bs.assign_rt_title_codes([(U, True)], taken_titles={bs.rt_title(U["name"], True)})
chk("збіг із title Toysi-картки → rt-картка отримує артикул", codes4 == {"rt-4": "X1"} and fail4 == [])
E = dict(A, id="rt-6", vendor_code="rt-777")     # артикул з префіксом rt- (не повинен траплятись) — не друкуємо
codes5, _ = bs.assign_rt_title_codes([(A, True), (E, True)])
chk("артикул з префіксом rt- ніколи не потрапляє в title", "rt-6" not in codes5 and not re.search(r"rt-", bs.rt_title(E["name"], True, codes5.get("rt-6")), re.I))
# (4) без залишку: довший суфікс — усе одно ≤ 65
t_oos = bs.rt_title(long_name, False, "ABC-12")
chk(f"без залишку: ≤ 65 із суфіксом «— немає в наявності | PlutusToys» ({len(t_oos)}), артикул у кінці назви", len(t_oos) <= 65 and t_oos.endswith("ABC-12" + SUF_OOS) and "…" not in t_oos)
# (5) product_title_for для rt: формула; код rt-… у title нема; Toysi — як було
bs.RT_TITLE_CODES.clear()
bs.RT_TITLE_CODES["rt-1"] = "PSD178"
chk("product_title_for(rt): артикул із реєстру, «№rt-…» нема", bs.product_title_for(A, True).endswith("PSD178" + SUF) and "№" not in bs.product_title_for(A, True))
toy = {"id": "555", "name": long_name + " Toysi", "rt": 0}
chk("Toysi-картка: формула НЕ застосовується (старий механізм, «…» за правилом SEO 18.10)", bs.product_title_for(toy, True) == bs.product_title(toy["name"], True))
bs.RT_TITLE_CODES.clear()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
