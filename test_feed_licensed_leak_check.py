#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_feed_licensed_leak_check.py — міра протікання ліцензійних марок в ОПУБЛІКОВАНИХ фідах (feed_licensed_leak_check.py). Мережі нема (fetch підставлено).
ІНВАРІАНТИ: збіг гейта і збіг «лише проби» розрізняються (дірка в переліку ≠ фід повз гейт); чистий фід → 0 і тиша в каналі; недоступний/не-XML фід → примітка в CSV,
у канал НЕ пишемо; запис угорі каналу перед першим «## », дедуп за добу на однаковий набір; регулярка гейта тотожна generate_google_feed.load_licensed_marks_regex;
файл проби у репо валідний; приклади SEO (27.10) — «мусить упасти» / «мусить лишитись» — поводяться як описано. `python test_feed_licensed_leak_check.py` → exit 0/1."""
import csv
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "site"))

import feed_licensed_leak_check as lk  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def feed_xml(titles):
    items = "".join(f"<item><g:id>{i}</g:id><title>{t}</title></item>" for i, t in titles.items())
    return ('<?xml version="1.0" encoding="UTF-8"?><rss xmlns:g="http://base.google.com/ns/1.0" version="2.0"><channel>' + items + "</channel></rss>").encode("utf-8")


tmp = Path(tempfile.mkdtemp(prefix="leak_"))
marks = tmp / "marks.json"
marks.write_text(json.dumps({"_контракт_для_коду": {"застосовувати": ["Disney"]}, "Disney": [r"\bстич\b", r"крижане\s+серце"],
                             "Маркер": [r"\bне_застосовується\b"], "_автомарки_окремо": {"форми": [r"\bbmw\b"]}}, ensure_ascii=False), encoding="utf-8")
probe = tmp / "probe.json"
probe.write_text(json.dumps({"проба": [r"naruto|наруто", r"squid\s*game"]}, ensure_ascii=False), encoding="utf-8")
gate_rx, probe_rx = lk.load_gate_regex(marks), lk.load_probe_regex(probe)

clean = {"1": "Пазл «Увесь рік» укр, 100 елементів", "2": 'Іграшка антистрес "Кальмар" (РОЖЕВИЙ)', "3": "Машинка BMW X6"}
dirty = dict(clean, **{"10": 'Бомбочка «Стич»', "11": 'Подушка м\'яка "Аніме. Наруто" 28 см', "12": 'Конструктор "Pixel Heroes: Squid Game", 431 дет.'})

# 1. чистий фід → 0, у канал нічого
fetch_clean = lambda url: feed_xml(clean)
row, hits, notes = lk.run_check(fetch_clean, gate_rx, probe_rx)
chk("чистий фід у трьох варіантах: збігів 0, приміток нема, позицій 3 у кожному", not hits and not notes and row["unique_hits"] == 0 and row["google_merchant_feed_items"] == 3
    and row["meta_feed_items"] == 3 and row["bing_feed_items"] == 3)
chk("автомарки (службовий ключ), родове «Кальмар» і «Увесь рік» не рахуються протіканням", row["gate_hits"] == 0 and row["probe_only"] == 0)

# 2. бруд: збіг гейта (Стич) відрізняється від «лише проба» (Наруто, Squid Game)
row, hits, notes = lk.run_check(lambda url: feed_xml(dirty), gate_rx, probe_rx)
chk("3 різні позиції у фіді (id 10, 11, 12), а не 9 (три фіди те саме)", sorted(hits) == ["10", "11", "12"] and row["unique_hits"] == 3)
chk("«Стич» = збіг гейта (фід повз гейт); Наруто й Squid Game = лише проба (дірка в переліку гейта)",
    hits["10"][2] == "gate" and hits["11"][2] == "probe" and hits["12"][2] == "probe" and row["gate_hits"] == 1 and row["probe_only"] == 2)
chk("кожен із трьох фідів дав по 3 збіги", row["google_merchant_feed_hits"] == row["meta_feed_hits"] == row["bing_feed_hits"] == 3)
chk("група поза контрактом («Маркер») не збирається в гейт", not gate_rx.search("не_застосовується"))

# 3. фід, де марка лише в одному з трьох → у множині фідів рівно він; позиція в двох фідах лишається однією позицією
def partial(url):
    return feed_xml(dirty if "meta_feed" in url else clean)


row, hits, _ = lk.run_check(partial, gate_rx, probe_rx)
chk("марка лише в Meta: 3 позиції, фіди = {meta_feed}, Google/Bing — 0", len(hits) == 3 and all(v[1] == {"meta_feed"} for v in hits.values())
    and row["google_merchant_feed_hits"] == 0 and row["meta_feed_hits"] == 3)

# 4. недоступний / не-XML фід → примітка, збігів немає, у канал не пишемо
def broken(url):
    return None if "bing" in url else (b"<html>not xml" if "meta" in url else feed_xml(clean))


row, hits, notes = lk.run_check(broken, gate_rx, probe_rx)
chk("bing недоступний, meta не-XML → дві примітки, google прочитано (3 позиції), збігів 0",
    len(notes) == 2 and row["google_merchant_feed_items"] == 3 and row["meta_feed_items"] == "" and row["bing_feed_items"] == "" and not hits)

# 5. CSV
csv_path = tmp / "rep" / "leak.csv"
lk.write_row(row, csv_path)
lk.write_row(row, csv_path)
with open(csv_path, encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
chk("CSV: заголовок один, два рядки, колонки unique_hits/gate_hits/probe_only/note", len(rows) == 2 and {"unique_hits", "gate_hits", "probe_only", "note"} <= set(rows[0]) and rows[0]["note"])

# 6. канал: запис угорі перед першим «## », дедуп за добу, CRLF зберігається, повний перелік
row_d, hits_d, _ = lk.run_check(lambda url: feed_xml(dirty), gate_rx, probe_rx)
ch = tmp / "EXECUTOR_CHANNEL.md"
ch.write_bytes("# Канал\r\n\r\nправила\r\n\r\n## [Консультант → Код] старе\r\nтекст\r\n".encode("utf-8"))
now = datetime(2026, 10, 27, 18, 0)
full = lk.write_full_list(hits_d, now, tmp / "rep")
chk("повний перелік записано (3 рядки + заголовок)", full is not None and len(full.read_text(encoding="utf-8").strip().splitlines()) == 4)
chk("перший запис додано", lk.post_to_channel(hits_d, row_d, now, ch, full) is True)
text = ch.read_bytes().decode("utf-8")
chk("запис стоїть ПЕРЕД першим «## » (над старим), «# Канал» і правила збережені", text.index("## [Автомат → Код] Фід-протікання") < text.index("## [Консультант → Код] старе")
    and text.startswith("# Канал\r\n\r\nправила\r\n\r\n"))
chk("CRLF збережено (жодного голого \\n)", "\r\n" in text and "\n" not in text.replace("\r\n", ""))
chk("запис називає і збіг гейта, і «лише проба», з id і назвою", "Збіг гейта (1)" in text and "Лише проба (2)" in text and "10 — " in text and "Наруто" in text)
n_before = text.count("Фід-протікання")
chk("повторний запис тієї ж доби з тим самим набором — не дублюється", lk.post_to_channel(hits_d, row_d, now, ch, full) is False and ch.read_bytes().decode("utf-8").count("Фід-протікання") == n_before)
row_e, hits_e, _ = lk.run_check(lambda url: feed_xml(dict(dirty, **{"13": "Лялька Стич"})), gate_rx, probe_rx)
chk("інший набір позицій тієї ж доби — новий запис", lk.post_to_channel(hits_e, row_e, now, ch, None) is True)
chk("недоступний канал → False, без винятку", lk.post_to_channel(hits_d, row_d, now, tmp / "нема" / "x.md", None) is False)

# 7. регулярка гейта тотожна генераторній (дубль навмисний — не розійтись) + репо-файли
import generate_google_feed as gg  # noqa: E402
real_marks = Path(__file__).parent / "feed_licensed_marks.json"
chk("регулярка гейта в перевірці = generate_google_feed.load_licensed_marks_regex на файлі репо (однаковий патерн)",
    lk.load_gate_regex(real_marks).pattern == gg.load_licensed_marks_regex(str(real_marks)).pattern)
real_gate, real_probe = lk.load_gate_regex(real_marks), lk.load_probe_regex(Path(__file__).parent / "feed_licensed_probe.json")
doc = json.loads((Path(__file__).parent / "feed_licensed_probe.json").read_text(encoding="utf-8"))
chk("файл проби в репо валідний: ≥19 форм, кожна компілюється", len(doc["проба"]) >= 19)

# приклади SEO 27.10: мусить упасти (ловиться гейтом) / мусить лишитись
MUST_CATCH = ['Конструктор "Pixel Heroes: Squid Game", 431 дет.', 'Пазл "Хот віллс. Швидкісні машинки"', 'Пазли на 1000 елм "Преміум: Рік і Морті"',
              'Подушка м\'яка "Аніме. Наруто" 28 см', "М'яка іграшка Леон (Бравл Старс) 25см", 'Лялька "Крижане серце: Анна"',
              'Машинка на радіо-керуванні "Трюкач: Трансформери" (червоний)', "Герої \"Гра в кальмара\" (квадрат)",
              'Картина за номерами "L. O. L. Surprise! Tweens Skate Dance Ali Dance 2" 30х40 см', 'Пазли Махі "Спайдер мен та його друзі" (24 ел)',
              'Брелок 3d друк "ГубкаБоб (flexy)"', 'Брелок 3d друк "Вендздей"', 'Картина по номерах "Аніме. Ророноа Зоро. Ван Піс" 40x50 см',
              'Книжка-панорамка "Маша і ведмідь" укр']
MUST_STAY = ['Іграшка антистрес "Кальмар" (РОЖЕВИЙ)', 'Танцюючий кальмар (восьминіг) "КАЛЬМАРІНІ ДЕНСЕРІНІ"', "Пазл «Увесь рік» укр, 100 елементів",
             'Книга "Еґґі Мортон. Небіжчик у садку"', "Новий рік. Набір для творчості", "Пазл 2023 РІК", "Іграшка-трансформер Дихаючий заєць",
             "Робот-трансформер 2в1", "Вежа Хот-дог ігровий набір"]
chk("приклади SEO «мусить упасти»: гейт ловить усі " + str(len(MUST_CATCH)), all(real_gate.search(s) for s in MUST_CATCH))
chk("приклади SEO «мусить лишитись»: гейт не чіпає родові (кальмар, увесь рік, Еґґі Мортон, трансформер у однині, Новий рік)", not any(real_gate.search(s) for s in MUST_STAY))
chk("проба (окремі, простіші форми) ловить ті самі приклади «мусить упасти», що й гейт", all(real_probe.search(s) for s in MUST_CATCH))

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
