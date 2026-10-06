# -*- coding: utf-8 -*-
"""Prom [П2 п.4.2.25]: слова «зброя»/«оружие» у назві/описі/ключових → автовидалення (Аудитор 2026-10-03, п.3).
Перевіряє санітайзер і що Prom-фід (generate_prom_feed._build_xml) не містить слова в offer; категорії <categories> і інші площадки не чіпаємо."""
import re, sys
import xml.etree.ElementTree as ET
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import prom_text_sanitize as ps

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

chk("«Водна зброя 28 см» → «Водні іграшки 28 см»", ps.sanitize("Водна зброя 28 см") == "Водні іграшки 28 см")
chk("«Іграшкова зброя Бластер ДІНО» → «Іграшка Бластер ДІНО»", ps.sanitize("Іграшкова зброя Бластер ДІНО") == "Іграшка Бластер ДІНО")
chk("«Категорія: Водна зброя.» в описі", ps.sanitize("Категорія: Водна зброя. Іграшка") == "Категорія: Водні іграшки. Іграшка")
chk("ключові слова: голе «зброя» → «іграшка», дублі прибрано", ps.dedupe_keywords(ps.sanitize("іграшка, дитяча, зброя, арбалет")) == "іграшка, дитяча, арбалет")
chk("рос.: «водное оружие», «оружие»", ps.sanitize("Водное оружие и оружие") == "Водные игрушки и игрушка")
chk("текст без слова не змінюється; None/порожнє — як є", ps.sanitize("Бластер") == "Бластер" and ps.sanitize(None) is None and ps.sanitize("") == "")
chk("has_weapon_word після санітизації = False", not ps.has_weapon_word(ps.sanitize("Зброя перемоги, водна зброя, іграшкової зброї")))
# --- відмінки/ланцюжки прикметників (аудит #627) ---
for src, want in [("Набори зі зброєю", "Набори з іграшкою"), ("Набір зброї з повʼязкою", "Набір іграшок з повʼязкою"),
                  ("Іграшкова дитяча зброя \"Арбалет\"", "Іграшка \"Арбалет\""), ("для іграшкової зброї", "для іграшки"),
                  ("водної зброї", "водних іграшок"), ("розмір зброі", "розмір іграшок"), ("в зброях", "в іграшках"),
                  ("с оружием", "с игрушкой"), ("Размер оружия", "Размер игрушки"), ("для пневматического оружия", "для пневматической игрушки"),
                  ("огнестрельное оружие", "огнестрельная игрушка"), ("Игрушечное детское оружие \"Арбалет\"", "Игрушка \"Арбалет\""),
                  ("водного оружия", "водных игрушек")]:
    chk(f"«{src}» → «{want}»", ps.sanitize(src) == want)

import generate_prom_feed as g
item = {"id": "1", "name": "Водна зброя «Торнадо» синій", "price": 100.0, "stock": 5, "category_id": "98887", "category_name": "Водна зброя",
        "vendor": "X", "vendor_code": "1", "pictures": ["https://toysi.ua/p/1.jpg"],
        "description": "Категорія: Водна зброя. Іграшкова зброя для літа.", "params": [("Тип", "Іграшкова зброя")], "barcode": ""}
try:
    root, _ = g._build_xml({"1": item}, russian_text={"1": {"name": "Водное оружие «Торнадо»", "description": "Игрушечное оружие для лета"}})
    xml = ET.tostring(root, encoding="unicode")
    offers = root.find("shop").find("offers")
    otext = ET.tostring(offers, encoding="unicode")
    chk("Prom-фід: у <offers> нема «зброя/оружие» (name, description, keywords, param)", not ps.has_weapon_word(otext))
    chk("Prom-фід: назва замінена, offer лишився", offers.find("offer") is not None and "Водні іграшки" in otext)
    chk("Prom-фід: <categories> не чіпаємо (мапа категорій Prom)", "Водна зброя" in ET.tostring(root.find("shop").find("categories"), encoding="unicode"))
except Exception as e:  # noqa: BLE001
    import traceback; traceback.print_exc()
    chk(f"Prom-фід зібрано без винятку ({type(e).__name__})", False)
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ слово «зброя» не потрапляє в Prom-фід — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
