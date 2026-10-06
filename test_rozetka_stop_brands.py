# -*- coding: utf-8 -*-
"""Стоп-бренди Rozetka (Аудитор 2026-10-03, п.2): таблиця правил → виключення з rozetka_feed. Перевіряє розбір таблиці, ключ без пробілів
(«DreamMakers» у Toysi = «Dream Makers» у таблиці), правило «для всіх категорій», великий перелік (blanket), короткий перелік за категорією."""
import csv, sys, tempfile
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import rozetka_stop_brands as rs

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

# --- справжня таблиця з репо ---
rules = rs.load()
chk("таблиця завантажена: >500 брендів, є правила «для всіх» і «категорії»",
    len(rules) > 500 and any(v["mode"] == "all" for v in rules.values()) and any(v["mode"] == "cats" for v in rules.values()))
for vendor in ("DreamMakers", "Dream Makers", "dream makers", "Genio kids", "Fancy"):
    chk(f"реальний стоп-бренд «{vendor}» (blanket: великий перелік категорій) → виключено, категорія не потрібна", bool(rs.stop_reason(vendor, "Розважальні")))
chk("бренд, якого нема в таблиці, не виключається", rs.stop_reason("Toysi Kids Unknown Brand", "Конструктори") is None)
chk("порожній vendor → None (не падає)", rs.stop_reason("", None) is None and rs.stop_reason(None, None) is None)
chk("«Для всіх категорій»: ABBYY виключається в будь-якій категорії", bool(rs.stop_reason("ABBYY", "Конструктори")))

# --- синтетична таблиця: короткий перелік категорій + виняток-колонка ---
d = Path(tempfile.mkdtemp()); p = d / "t.csv"
with open(p, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Назва бренду / ТМ", "Правило (умови заборони)", "Виключення (дозволено продаж)"])
    w.writerow(["Acme-Toys", "Категорії: Настільні ігри, Пазли, Ліплення", ""])
    w.writerow(["Banned Co", "Для всіх категорій", "Наявність авторизаційного листа"])
rs._cache = None
r2 = rs.load(p)
chk("синтетика: ключ без дефісів/пробілів (acmetoys), короткий перелік = 3 категорії", "acmetoys" in r2 and r2["acmetoys"]["ncats"] == 3)
import types
orig = rs.load
rs.load = lambda path=p: orig(p)
chk("короткий перелік: товар у категорії «Пазли» → виключено", bool(rs.stop_reason("Acme Toys", "Пазли")))
chk("короткий перелік: інша категорія (Конструктори) → лишається", rs.stop_reason("Acme Toys", "Конструктори") is None)
chk("виняток-колонка не послаблює «для всіх» (немає авторизаційного листа)", bool(rs.stop_reason("Banned Co", "Пазли")))
rs.load = orig

# --- підключення у фід ---
src = (Path(__file__).parent / "generate_rozetka_feed.py").read_text(encoding="utf-8")
chk("generate_rozetka_feed: імпорт і 3 перевірки через rozetka_stop_brands.stop_reason", "import rozetka_stop_brands" in src and src.count("rozetka_stop_brands.stop_reason(") == 3)
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ стоп-бренди Rozetka — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
