# -*- coding: utf-8 -*-
"""system_map_driftcheck: перевірка бачить СТАН таска (Disabled), не лише ім'я.

Корінь (архітектурний аудит 09.10.2026): `PlutusToys_AgentWatch` вимкнено 22.09, а SYSTEM_MAP
досі описував його робочим — і перевірка казала «збіг», бо знімала лише імена. Тест фіксує, що
кожен вид розбіжності ловиться, і що сам реєстр у SYSTEM_MAP узгоджений (нема подвійних імен).
Запуск: python test_system_map_driftcheck.py → exit 0/1. Нічого не чіпає в системі."""
import contextlib
import io
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import system_map_driftcheck as d

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

def diff(active, disabled, states):
    with contextlib.redirect_stdout(io.StringIO()):
        return d._diff_local_tasks(set(active), set(disabled), states)

# --- розбір виводу PowerShell ---
st = d.parse_task_states("PlutusToys_A|Ready\r\n\r\nPlutusToys-B|Disabled\nсміття без роздільника\n|Ready\nPlutusToys_C | Running \n")
chk("parse: імена й стани, порожні/зламані рядки пропущено",
    st == {"PlutusToys_A": "Ready", "PlutusToys-B": "Disabled", "PlutusToys_C": "Running"})

# --- збіг ---
drift, msgs = diff(["A", "B"], ["W"], {"A": "Ready", "B": "Running", "W": "Disabled"})
chk("збіг: діють Ready/Running + вимкнений Disabled → без дрейфу", not drift and not msgs)

# --- той самий сценарій, що пропустила стара перевірка ---
drift, msgs = diff(["A", "W"], [], {"A": "Ready", "W": "Disabled"})
chk("мапа «діє», таск Disabled → ДРЕЙФ і ім'я названо",
    drift and len(msgs) == 1 and "ВИМКНЕНО" in msgs[0] and "'W'" in msgs[0])

drift, msgs = diff(["A"], ["W"], {"A": "Ready", "W": "Ready"})
chk("мапа «вимкнено», таск діє → ДРЕЙФ",
    drift and len(msgs) == 1 and "ДІЄ" in msgs[0] and "'W'" in msgs[0])

# --- імена ---
drift, msgs = diff(["A", "GONE"], [], {"A": "Ready"})
chk("в мапі «діє», у системі нема → ДРЕЙФ «ЖИВОГО НЕМА»", drift and "'GONE'" in msgs[0] and "ЖИВОГО НЕМА" in msgs[0])

drift, msgs = diff(["A"], ["GONE2"], {"A": "Ready"})
chk("в мапі «вимкнено», у системі нема → теж ДРЕЙФ", drift and "'GONE2'" in msgs[0])

drift, msgs = diff(["A"], [], {"A": "Ready", "NEW": "Ready"})
chk("є в системі, нема в мапі → ДРЕЙФ «У МАПІ НЕМА»", drift and "'NEW'" in msgs[0] and "У МАПІ НЕМА" in msgs[0])

drift, msgs = diff(["A"], [], {"A": "Ready", "NEWOFF": "Disabled"})
chk("вимкнений таск, якого нема в мапі → теж ДРЕЙФ (не ховається за «вимкнено»)", drift and "'NEWOFF'" in msgs[0])

# --- реєстр ---
drift, msgs = diff(["A"], ["A"], {"A": "Ready"})
chk("ім'я в обох списках реєстру → ДРЕЙФ", drift and any("ОБОХ" in m for m in msgs))

# --- кілька проблем одразу: усі названо ---
drift, msgs = diff(["A", "B"], ["W"], {"A": "Disabled", "W": "Ready", "NEW": "Ready"})
chk("кілька розбіжностей → кожна окремим повідомленням", drift and len(msgs) == 4)

# --- справжній реєстр у SYSTEM_MAP ---
reg = d.load_registry()
act, dis = set(reg["local_tasks"]), set(reg.get("local_tasks_disabled", []))
chk("реєстр: списки «діє» і «вимкнено» не перетинаються", not (act & dis))
chk("реєстр: вимкнені 22.09 вотчери записані як вимкнені, не як діючі",
    {"PlutusToys_AgentWatch", "PlutusToys_SellerWatchdog"} <= dis and not ({"PlutusToys_AgentWatch", "PlutusToys_SellerWatchdog"} & act))

print("\nРЕЗУЛЬТАТ:", "УСЕ ОК" if not F else f"{len(F)} FAIL: {F}")
sys.exit(0 if not F else 1)
