#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_novapay_statement_prefilter.py — регрес-тест _registry_candidate_uids (novapay_statement.py).

Живий інцидент 22.10: у INBOX 1524 непрочитаних, скрипт тягнув КОЖЕН через RFC822 (довго, усе
позначалось прочитаним), реєстри з 25.08 лишились необробленими. Префільтр дивиться лише BODYSTRUCTURE.

ІНВАРІАНТИ:
  - лишається лист, у якого ім'я .xlsx-вкладення містить «реєстр» (у т.ч. MIME encoded-word);
  - інші xlsx (Специфікація, close_lisa_request) відсіюються;
  - лист без розбірливих імен / відповідь не розібрано → кандидат (не втрачаємо реєстр);
  - збій FETCH → не звужуємо.
Мережа замокана. `python test_novapay_statement_prefilter.py` → exit 0/1.
"""
import sys
from email.header import Header

import novapay_statement as n

_FAILS = []


def _check(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


def _enc(s):
    return Header(s, "utf-8").encode().encode()


def _bs(uid, name):
    nm = b'("NAME" "' + name + b'")' if name else b"NIL"
    return (f"{uid} (BODYSTRUCTURE ((\"TEXT\" \"PLAIN\" NIL NIL NIL \"7BIT\" 0 0)"
            f"(\"APPLICATION\" \"OCTET-STREAM\" ").encode() + nm + b' NIL NIL "BASE64" 10) "MIXED"))'


class _Fake:
    def __init__(self, resp, status="OK"):
        self.resp, self.status = resp, status

    def fetch(self, ids, what):
        return self.status, self.resp


uids = [b"1", b"2", b"3", b"4"]
resp = [
    _bs(1, _enc("Реєстр переказів контрагента ФОП № 123 від 05.10.2026.XLSX")),
    _bs(2, _enc("Специфікація до акту № 5.xlsx")),
    _bs(3, b"6acf_close_lisa_request_6acf.xlsx"),
    _bs(4, None),
]
got = n._registry_candidate_uids(_Fake(resp), uids)
_check("реєстр (encoded-word) лишається", b"1" in got)
_check("Специфікація / close_lisa_request відсіяні", b"2" not in got and b"3" not in got)
_check("лист без імен — кандидат", b"4" in got)
_check("відповідь не розібрано → кандидат", n._registry_candidate_uids(_Fake([]), [b"7"]) == [b"7"])
_check("збій FETCH → не звужуємо", n._registry_candidate_uids(_Fake([], status="NO"), uids) == uids)

sys.exit(1 if _FAILS else 0)
