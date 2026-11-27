#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_social_utm.py — UTM у посиланні підпису FB-автопосту (запит SMM 27.11.2026). IG-підпис посилання не містить (не клікабельне). `python test_social_utm.py` -> exit 0/1."""
import sys
import social_auto_poster as sp

fails = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: fails.append(n)

U = "https://plutustoys.com.ua/product-y49155.html"
r = sp.utm_url(U, "facebook")
chk("голий URL -> три UTM", r == U + "?utm_source=facebook&utm_medium=social&utm_campaign=product_post")
chk("є інші параметри -> зберігаються, UTM дописано", sp.utm_url(U + "?a=1", "facebook").startswith(U + "?a=1&utm_source=facebook"))
chk("utm_source уже є -> без змін", sp.utm_url(U + "?utm_source=x", "facebook") == U + "?utm_source=x")
chk("фрагмент зберігається", sp.utm_url(U + "#r", "facebook").endswith("#r"))
chk("порожній/не URL -> як є, без винятку", sp.utm_url("", "facebook") == "" and sp.utm_url("abc", "facebook") == "abc")
p = {"sku": "49155", "name": "Тест", "url": U, "price": "100 UAH"}
cap_fb = sp.build_caption(p, "fb")
cap_ig = sp.build_caption(p, "ig")
chk("FB-підпис містить URL з UTM", "utm_source=facebook" in cap_fb and U in cap_fb)
chk("IG-підпис без посилання/UTM", "utm_source" not in cap_ig and "product-y49155" not in cap_ig)
chk("p['url'] не змінено (перевірка живості/журнал)", p["url"] == U)
print("ALL OK" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
