#!/bin/bash
# feed_publish_gate.sh — гейт фіду ПЕРЕД публікацією (source-ується з publish_feed_pipeline_vps.sh; окремо тестується test_feed_publish_gate.py).
#
# НАВІЩО (12.10.2026): публікація робила `cp` без перевірки. Порожній чи обрізаний прогін (збій Toysi/мережі/диска) перезаписував робочий фід; для Prom це катастрофа:
# налаштування імпорту «Статус товарів, яких немає в файлі» = «Видалені» ВИДАЛЯЄ з кабінету все, чого нема у файлі.
#
# РІШЕННЯ ВЛАСНИКА 13.10.2026: НІЯКИХ довільних числових порогів (були «мінімум 3000 позицій» і «просідання >30%»; «ми на них часто обпікалися»). Гейт перевіряє ЛИШЕ ФАКТИ про сам файл:
#   (а) файл є й не порожній;  (б) це валідний XML (обрізаний чи пошкоджений файл не розбереться: нема закриваючих тегів);  (в) у ньому є хоча б одна позиція (<item> або <offer>).
# Фід, який просто МЕНШИЙ (бо менше товарів у наявності), публікується: це правда про каталог, а не поломка.
# Заблокований фід НЕ копіюється: лишається попередня опублікована версія (її вже підтягнув `git checkout origin/feed-data -- .`), причина пишеться в BLOCKED_FILE → прогін degraded.
# Свідоме одноразове рішення: FEED_GATE_OVERRIDE=1 у середовищі прогону.

# кількість позицій у фіді (для мітки генерації feed_meta і журналу): <item> (Google/Meta/Bing, RSS) або <offer (EVA/ALLO/Prom, YML). Порожній/відсутній файл → 0.
feed_offer_count() {
    [ -s "$1" ] || { echo 0; return; }
    grep -o -E '<item>|<offer[ >]' "$1" | wc -l | tr -d ' '
}

# feed_gate_ok <новий-файл> [ігнорується] [ігнорується]  →  0 = можна публікувати, 1 = заблоковано (причина в stdout).
# Додаткові аргументи лишені для сумісності зі старими викликами (опублікований-раніше, мінімум): тепер НЕ використовуються.
feed_gate_ok() {
    local new="$1" msg rc py="${PLUTUS_PY:-}"
    if [ -z "$py" ]; then if [ -x /opt/plutustoys/venv/bin/python3 ]; then py=/opt/plutustoys/venv/bin/python3; else py=python3; fi; fi   # абсолютний venv-python на VPS (як вимагає CLAUDE.md)
    if [ "${FEED_GATE_OVERRIDE:-0}" = "1" ]; then
        echo "override: гейт вимкнено свідомо"
        return 0
    fi
    [ -s "$new" ] || { echo "файл відсутній або порожній"; return 1; }
    msg=$("$py" - "$new" <<'PY'
import sys
import xml.etree.ElementTree as ET
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")      # причина блокування — українською в UTF-8 на будь-якій ОС
n = 0
try:
    for _ev, el in ET.iterparse(sys.argv[1], events=("end",)):
        if el.tag in ("item", "offer"):
            n += 1
            el.clear()
except ET.ParseError as e:
    print("XML пошкоджений або обрізаний: %s" % e)
    sys.exit(2)
except Exception as e:
    print("файл не вдалось прочитати: %s: %s" % (type(e).__name__, e))
    sys.exit(2)
if n == 0:
    print("у файлі немає жодної позиції")
    sys.exit(3)
print("ok: %d позицій" % n)
PY
)
    rc=$?
    echo "$msg"
    [ "$rc" -eq 0 ]
}
