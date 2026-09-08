#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_prom_redirects.py — 301-редирект-мапа для nginx, щоб при переносі домену
plutustoys.com.ua з Prom на власний сайт НЕ пропала видимість у Google Merchant Center.

Проблема: GMC-фід (generate_google_feed.py) віддає посилання за Prom-структурою URL
  https://plutustoys.com.ua/ua/p{prom_id}-{url_text}.html
(зараз домен веде на Prom, там ці сторінки існують). Наш власний сайт віддає товари за
  https://plutustoys.com.ua/product-{toysi_id}.html
Після перенесення DNS на VPS старі Prom-URL впадуть у 404 → Google задисаппрувить товари.

Рішення: nginx `location ~ ^/ua/p(\\d+)-` → 301 на нашу картку. Цей скрипт генерує мапу
`prom_id → /product-{toysi_id}.html`, інвертуючи `own_product_links_cache.json`
({toysi_id: {prom_id, url_text}}, який наповнює generate_google_feed/full_catalog_scan).
Редиректимо ЛИШЕ на товари, що реально згенеровані на сайті (є в site/index.json) — решта
падає в nginx-`default` (/catalog.html), тобто без жорсткого 404.
"""
import os
import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
CACHE_FILE = BASE / "own_product_links_cache.json"          # {toysi_id: {prom_id, url_text}}
INDEX_FILE = Path(os.environ.get("SITE_DIR", str(BASE / "site"))) / "index.json"  # згенеровані товари
OUT_FILE = Path(os.environ.get("PROM_REDIRECTS_MAP", str(BASE / "prom_redirects.map")))


def load_valid_ids() -> set:
    """toysi-id, що реально мають сторінку product-<id>.html (із site/index.json)."""
    try:
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        return {str(p["id"]) for p in data if "id" in p}
    except Exception as e:
        print(f"[prom_redirects] УВАГА: index.json недоступний ({e}) — мапа буде без гейту наявності",
              file=sys.stderr)
        return set()


def build_map() -> tuple[str, int, int]:
    try:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[prom_redirects] {CACHE_FILE.name} нема — мапа порожня (усі старі URL → default)",
              file=sys.stderr)
        cache = {}
    valid = load_valid_ids()

    lines, seen_prom, skipped = [], set(), 0
    for toysi_id, info in cache.items():
        prom_id = (info or {}).get("prom_id")
        if not prom_id:
            continue
        prom_id = str(prom_id)
        if prom_id in seen_prom:      # той самий Prom-лістинг двічі — беремо перший
            continue
        # редиректимо лише на наявну сторінку; інакше лишаємо default (/catalog.html)
        if valid and str(toysi_id) not in valid:
            skipped += 1
            continue
        seen_prom.add(prom_id)
        lines.append(f"{prom_id} /product-{toysi_id}.html;")

    lines.sort()
    header = ("# АВТО-ЗГЕНЕРОВАНО generate_prom_redirects.py — nginx map (prom_id -> наша картка).\n"
              "# Include у map-блоці nginx; default (/catalog.html) — для непокритих.\n")
    return header + "\n".join(lines) + "\n", len(lines), skipped


def main() -> None:
    content, n, skipped = build_map()
    OUT_FILE.write_text(content, encoding="utf-8")
    print(f"[prom_redirects] {OUT_FILE}: {n} редиректів prom_id->/product-*.html "
          f"(пропущено {skipped} — нема сторінки на сайті)")


if __name__ == "__main__":
    main()
