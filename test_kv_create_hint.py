#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_kv_create_hint.py — діагностика невідомого результату orders/create (27.11.2026). `python test_kv_create_hint.py` -> exit 0/1."""
import sys
import kiddisvit_orders as ko
import kiddisvit_api as ka

fails = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: fails.append(n)

def msg(res):
    try:
        ko.interpret_create(res)
    except ka.KiddisvitUncertain as e:
        return str(e)
    return None

m = msg({"status_OK": 0, "Message": "x" * 300, "nested": {"a": 1}, "Error": "bad"})
chk("без number/id -> KiddisvitUncertain із ключами відповіді", m is not None and "Error=bad" in m and "status_OK=0" in m)
chk("довге значення обрізано (≤40), вкладене не розкрито", m is not None and "x" * 41 not in m and "nested=<структура>" in m)
chk("висновок лишається 'повторювати create заборонено'", m is not None and "повторювати create заборонено" in m)
chk("не dict -> без винятку, є тип", "list" in (msg([1, 2]) or ""))
chk("порожня відповідь {} -> Uncertain", msg({}) is not None)
ok = ko.interpret_create({"number": "КЗ-1", "id": "abc", "status_OK": 1})
chk("нормальна відповідь без змін", ok["number"] == "КЗ-1" and ok["id"] == "abc" and ok["reserved"] is True)
print("ALL OK" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
