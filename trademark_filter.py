# -*- coding: utf-8 -*-
"""trademark_filter.py — товари, які не можна продавати на ВЛАСНОМУ сайті та в Google/Meta/Bing-фідах через претензію
правовласника. Зараз: ТМ «UNO» (Mattel; Rozetka-тікет #30227955). Правило те саме, що в Rozetka-фіді
(generate_rozetka_feed._is_uno_trademark_blocked, PR #504): назва містить «uno»/«уно» ЦІЛИМ словом І vendor не Mattel.
Цілим словом — свідомо не чіпає «КапиУНО», «УНА». SEO-замовлення 2026-10-06: 37400, 11590, 49289 віддавались на сайті й у фіді."""
import re

UNO_RE = re.compile(r"\b(?:uno|уно)\b", re.IGNORECASE)
UNO_ALLOWED_VENDORS = {"mattel"}


def is_uno_trademark_blocked(name: str, vendor: str) -> bool:
    if not UNO_RE.search(name or ""):
        return False
    return (vendor or "").strip().lower() not in UNO_ALLOWED_VENDORS
