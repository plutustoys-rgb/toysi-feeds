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
Редиректимо ЛИШЕ на товари, що реально мають сторінку на сайті (в наявності — site/index.json; без залишку — сторінка OutOfStock,
product-<id>.html у site/) — решта: nginx-`default` (/catalog.html); в Apache (прод) — 410 Gone (рішення Консультанта 11.10.2026).
"""
import os
import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
CACHE_FILE = BASE / "own_product_links_cache.json"          # {toysi_id: {prom_id, url_text}}
INDEX_FILE = Path(os.environ.get("SITE_DIR", str(BASE / "site"))) / "index.json"  # згенеровані товари
OUT_FILE = Path(os.environ.get("PROM_REDIRECTS_MAP", str(BASE / "prom_redirects.map")))
# VPS працює на Apache (Webuzo), не на nginx: RewriteMap txt:<файл> читає "ключ значення" БЕЗ крапки з комою.
OUT_FILE_APACHE = Path(os.environ.get("PROM_REDIRECTS_MAP_APACHE", str(BASE / "prom_redirects_apache.map")))


def load_valid_ids() -> set:
    """toysi-id, що реально мають сторінку product-<id>.html: товари в наявності (site/index.json) + сторінки БЕЗ залишку
    (build_site.py з 11.10.2026 лишає їх живими з OutOfStock) — їх знаходимо за файлами product-<id>.html у теці сайту."""
    ids = set()
    try:
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        ids = {str(p["id"]) for p in data if "id" in p}
    except Exception as e:
        print(f"[prom_redirects] УВАГА: index.json недоступний ({e}) — мапа буде без гейту наявності",
              file=sys.stderr)
        return set()
    try:
        for fn in os.listdir(INDEX_FILE.parent):
            if fn.startswith("product-") and fn.endswith(".html"):
                ids.add(fn[len("product-"):-len(".html")])
    except OSError:
        pass
    return ids


def build_map() -> tuple[str, int, int]:
    """nginx-формат (include у map-блоці). Apache-формат — build_map_apache()."""
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
    build_map.apache_lines = [l.rstrip(";") for l in lines]   # той самий набір, формат "prom_id /product-X.html"
    header = ("# АВТО-ЗГЕНЕРОВАНО generate_prom_redirects.py — nginx map (prom_id -> наша картка).\n"
              "# Include у map-блоці nginx; default (/catalog.html) — для непокритих.\n")
    return header + "\n".join(lines) + "\n", len(lines), skipped


def _write_atomic(path: Path, text: str) -> None:
    """tmp + os.replace: Apache/nginx не прочитають напівзаписану мапу (інакше хибний 301 на /catalog.html);
    LF-кінці незалежно від ОС (мапа читається на Linux)."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.chmod(tmp, 0o644)    # новий inode не успадковує ACL/права старого файла — Apache-користувач має читати
    os.replace(tmp, path)


def build_map_apache() -> str:
    """Apache RewriteMap txt: один рядок = "prom_id /product-<toysi>.html". Викликати ПІСЛЯ build_map()."""
    lines = getattr(build_map, "apache_lines", [])
    header = "# АВТО-ЗГЕНЕРОВАНО generate_prom_redirects.py — Apache RewriteMap txt (prom_id -> наша картка)."
    return "\n".join([header] + lines) + "\n"


SHRINK_GUARD_MIN_PREV = 100      # попередня мапа менша за це — гард не діє (перший запуск/мала мапа)
SHRINK_GUARD_FRACTION = 0.5      # нова мапа менша за цю частку попередньої → не перезаписуємо


def _count_apache_lines(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line[:1].isdigit())
    except OSError:
        return 0


def main() -> None:
    content, n, skipped = build_map()
    # Мапа тепер керує і 410: прод-Apache віддає 410 Gone для КОЖНОГО prom_id, якого в мапі нема. Збій (порожній кеш, битий індекс, обрізана збірка)
    # дав би малу мапу → масове 410 замість 301 на картки → втрата видимості в Google/GMC. Тому різке скорочення мапи НЕ записуємо (стара лишається).
    prev = _count_apache_lines(OUT_FILE_APACHE)
    if prev >= SHRINK_GUARD_MIN_PREV and n < prev * SHRINK_GUARD_FRACTION and os.environ.get("PROM_REDIRECTS_ALLOW_SHRINK") != "1":
        print(f"[prom_redirects] 🚨 нова мапа має {n} редиректів проти {prev} (< {SHRINK_GUARD_FRACTION:.0%}) — НЕ перезаписую "
              "(збій кешу/індексу? якщо скорочення свідоме — PROM_REDIRECTS_ALLOW_SHRINK=1).", file=sys.stderr)
        sys.exit(2)
    _write_atomic(OUT_FILE, content)
    _write_atomic(OUT_FILE_APACHE, build_map_apache())
    print(f"[prom_redirects] {OUT_FILE}: {n} редиректів prom_id->/product-*.html "
          f"(пропущено {skipped} — нема сторінки на сайті; ці Prom-URL на проді віддають 410 Gone)")


if __name__ == "__main__":
    main()
