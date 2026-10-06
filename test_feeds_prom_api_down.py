# -*- coding: utf-8 -*-
"""Збій Prom API (ReadTimeout my.prom.ua 06.10.2026 07:08) не має валити Google/Meta/Bing-фід:
посилання/ціни фіда — рідні картки сайту; Prom потрібен лише для чистих фото."""
import os, sys, tempfile, json
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import generate_google_feed as gg
F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

import requests
def boom(): raise requests.exceptions.ReadTimeout("Read timed out")
gg.fetch_prom_products = boom
sent = []
import telegram_notify
telegram_notify.send_throttled_alert = lambda k, m, *a, **kw: sent.append(k)
d = Path(tempfile.mkdtemp()); gg.PROM_PRODUCTS_CACHE_FILE = d / "cache.json"

chk("нема кешу + Prom впав → {}", gg.fetch_prom_products_resilient() == {})
stale = {"1": {"external_id": "1", "main_image": "https://images.prom.ua/x.jpg"}}
gg.PROM_PRODUCTS_CACHE_FILE.write_text(json.dumps(stale), encoding="utf-8")
old = os.path.getmtime(gg.PROM_PRODUCTS_CACHE_FILE) - 7200
os.utime(gg.PROM_PRODUCTS_CACHE_FILE, (old, old))   # кеш старший за TTL (1 год)
chk("штатний читач ігнорує застарілий кеш", gg.load_prom_products_cache() is None)
chk("Prom впав + застарілий кеш → кеш (фото Prom лишаються)", gg.fetch_prom_products_resilient() == stale)
chk("деградація → Telegram-сигнал і прапорець", sent == ["feed_prom_degraded", "feed_prom_degraded"] and gg.PROM_FETCH_DEGRADED)
# кеш старший за 14 діб → не використовується
very_old = os.path.getmtime(gg.PROM_PRODUCTS_CACHE_FILE) - 15 * 86400
os.utime(gg.PROM_PRODUCTS_CACHE_FILE, (very_old, very_old))
chk("кеш >14 діб не береться → {}", gg.fetch_prom_products_resilient() == {})
# порожня відповідь БЕЗ винятку → теж деградація
os.utime(gg.PROM_PRODUCTS_CACHE_FILE, (old, old))
gg.fetch_prom_products = lambda: {}
chk("порожній dict без винятку → стейл-кеш", gg.fetch_prom_products_resilient() == stale)
# 401 (відкликаний ключ) і баги коду падають гучно
def e401():
    r = requests.Response(); r.status_code = 401
    raise requests.exceptions.HTTPError("401", response=r)
gg.fetch_prom_products = e401
try: gg.fetch_prom_products_resilient(); loud = False
except requests.exceptions.HTTPError: loud = True
chk("401 не глушиться", loud)
def bug(): raise KeyError("x")
gg.fetch_prom_products = bug
try: gg.fetch_prom_products_resilient(); loud = False
except KeyError: loud = True
chk("KeyError (баг коду) не глушиться", loud)
gg.fetch_prom_products = lambda: {"2": {"external_id": "2"}}
chk("Prom живий → живі дані, без прапорця", gg.fetch_prom_products_resilient() == {"2": {"external_id": "2"}} and not gg.PROM_FETCH_DEGRADED)
import generate_meta_feed, generate_bing_feed
chk("Meta/Bing імпортують стійку функцію", hasattr(generate_meta_feed, "fetch_prom_products_resilient")
    and hasattr(generate_bing_feed, "fetch_prom_products_resilient"))
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ збій Prom API не валить фіди — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
