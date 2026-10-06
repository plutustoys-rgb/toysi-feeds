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

def boom(): raise TimeoutError("Read timed out")
gg.fetch_prom_products = boom
d = Path(tempfile.mkdtemp()); gg.PROM_PRODUCTS_CACHE_FILE = d / "cache.json"

chk("нема кешу + Prom впав → {}", gg.fetch_prom_products_resilient() == {})
stale = {"1": {"external_id": "1", "main_image": "https://images.prom.ua/x.jpg"}}
gg.PROM_PRODUCTS_CACHE_FILE.write_text(json.dumps(stale), encoding="utf-8")
old = os.path.getmtime(gg.PROM_PRODUCTS_CACHE_FILE) - 7200
os.utime(gg.PROM_PRODUCTS_CACHE_FILE, (old, old))   # кеш старший за TTL (1 год)
chk("штатний читач ігнорує застарілий кеш", gg.load_prom_products_cache() is None)
chk("Prom впав + застарілий кеш → кеш (фото Prom лишаються)", gg.fetch_prom_products_resilient() == stale)
gg.fetch_prom_products = lambda: {"2": {"external_id": "2"}}
chk("Prom живий → живі дані", gg.fetch_prom_products_resilient() == {"2": {"external_id": "2"}})
import generate_meta_feed, generate_bing_feed
chk("Meta/Bing імпортують стійку функцію", hasattr(generate_meta_feed, "fetch_prom_products_resilient")
    and hasattr(generate_bing_feed, "fetch_prom_products_resilient"))
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ збій Prom API не валить фіди — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
