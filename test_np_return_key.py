#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_np_return_key.py — ключ для повернень НП (17.11.2026). Контекст: ТТН Toysi лежать у кабінеті ФОП власника, NP_API_KEY на VPS — інший акаунт («Приватна особа») → повернення по ТТН Toysi давали
«Документ не належить даному користувачу». Інваріанти: (1) check_return_possibility і create_return_order шлють NP_FOP_API_KEY, якщо він заданий; (2) нема NP_FOP_API_KEY → NP_API_KEY (зворотна сумісність);
(3) решта викликів (довідники, трекінг) лишаються на NP_API_KEY; (4) ключ не потрапляє в текст помилки; (5) нема жодного ключа → помилка без запиту."""
import sys
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import nova_poshta as np_  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


class R:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p

    text = ""


sent = []


def fake_post(url, json=None, timeout=None):
    sent.append(json)
    if json["calledMethod"] == "CheckPossibilityCreateReturn" and json["apiKey"] == "OTHER-KEY":
        return R({"success": False, "errors": ["Документ не належить даному користувачу"]})
    return R({"success": True, "data": [{"Number": "N1", "Ref": "R1"}]})


np_.requests.post = fake_post
np_.NP_API_KEY = "OTHER-KEY"
np_.NP_FOP_API_KEY = ""
r0 = np_.check_return_possibility("20451588716656")
chk("без NP_FOP_API_KEY повернення йдуть на NP_API_KEY (як раніше) → 'не належить'", sent[-1]["apiKey"] == "OTHER-KEY" and r0["possible"] is False and "не належить" in r0["error"])
chk("ключ не потрапляє в текст помилки", "OTHER-KEY" not in r0["error"])

np_.NP_FOP_API_KEY = "FOP-KEY"
r1 = np_.check_return_possibility("20451588716656")
chk("з NP_FOP_API_KEY перевірка повернення йде ключем ФОП → можливе", sent[-1]["apiKey"] == "FOP-KEY" and r1["possible"] is True)
res = np_.create_return_order("20451588716656", note="тест")
chk("create_return_order теж ключем ФОП; повертає номер зворотної ТТН", sent[-1]["apiKey"] == "FOP-KEY" and sent[-1]["calledMethod"] == "save" and res == {"number": "N1", "ref": "R1"})
np_._call("Address", "getCities", {"FindByString": "Київ"})
chk("довідники/трекінг (інші виклики) лишаються на NP_API_KEY", sent[-1]["apiKey"] == "OTHER-KEY" and sent[-1]["modelName"] == "Address")

np_.NP_API_KEY = ""
np_.NP_FOP_API_KEY = ""
n_before = len(sent)
try:
    np_._call("Address", "getCities", {})
    ok = False
except np_.NovaPoshtaAPIError:
    ok = True
chk("нема жодного ключа → NovaPoshtaAPIError, запиту нема", ok and len(sent) == n_before)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
