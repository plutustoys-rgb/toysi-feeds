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

ГРУПИ PROM (12.10.2026, SEO-замовлення; GSC: 88 старих URL груп `/g{gid}-{slug}` дають ≈40% показів, а вони вели 301 на /catalog.html):
друга мапа `prom_group_redirects_apache.map` (Apache RewriteMap txt:, "gid /category-<slug>.html") з джерела правди
`prom_group_to_category.json` ({"<gid>": {"g_slug", "impressions_90d", "category": "<slug без category- і .html>" | null}}).
Дані SEO — НЕ довірений вхід: gid — лише цифри, slug — лише [a-z0-9-]+, а сторінка site/category-<slug>.html мусить існувати у зібраному
сайті; інакше запис пропускається з гучним попередженням у stderr (генератор не падає — gid лишається на /catalog.html, як було).
Перший рядок мапи — gid з найбільшою кількістю показів: його беруть проби activate_site_apache.sh і канарка.
"""
import os
import re
import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
CACHE_FILE = BASE / "own_product_links_cache.json"          # {toysi_id: {prom_id, url_text}}
INDEX_FILE = Path(os.environ.get("SITE_DIR", str(BASE / "site"))) / "index.json"  # згенеровані товари
OUT_FILE = Path(os.environ.get("PROM_REDIRECTS_MAP", str(BASE / "prom_redirects.map")))
# VPS працює на Apache (Webuzo), не на nginx: RewriteMap txt:<файл> читає "ключ значення" БЕЗ крапки з комою.
OUT_FILE_APACHE = Path(os.environ.get("PROM_REDIRECTS_MAP_APACHE", str(BASE / "prom_redirects_apache.map")))
# групи Prom (12.10.2026): джерело правди (SEO) → мапа Apache gid -> /category-<slug>.html
GROUP_JSON = Path(os.environ.get("PROM_GROUP_CATEGORY_JSON", str(BASE / "prom_group_to_category.json")))
OUT_FILE_GROUPS_APACHE = Path(os.environ.get("PROM_GROUP_REDIRECTS_MAP_APACHE", str(BASE / "prom_group_redirects_apache.map")))
# Ручні перекриття для старих Prom-карток БЕЗ сторінки на сайті (21.10.2026, рішення SEO/власника «захист старих адрес»): {"<prom_id>": {"target": "/category-<slug>.html", ...}}.
# Застосовуються ЛИШЕ до prom_id, яких немає в основній мапі (картка з реальною сторінкою ніколи не перебивається), і лише на існуючу сторінку категорії.
OVERRIDES_JSON = Path(os.environ.get("PROM_REDIRECT_OVERRIDES_JSON", str(BASE / "prom_redirect_overrides.json")))
PID_RE = re.compile(r"[0-9]{1,20}")
OVERRIDE_TARGET_RE = re.compile(r"/category-([a-z0-9-]{1,200})\.html")
GID_RE = re.compile(r"[0-9]{1,20}")                 # fullmatch; явний клас, а не \d (той приймає арабсько-індійські цифри)
SLUG_RE = re.compile(r"[a-z0-9-]{1,200}")
GROUP_LINE_RE = re.compile(r"[0-9]{1,20} /category-[a-z0-9-]{1,200}\.html")   # формат рядка мапи груп (перевіряється перед записом і в тесті)
GROUP_HEADER = "# АВТО-ЗГЕНЕРОВАНО generate_prom_redirects.py — Apache RewriteMap txt (gid групи Prom -> /category-<slug>.html; перший рядок = найбільше показів)."


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


def load_overrides(site_dir: Path, taken: set) -> tuple[dict, dict]:
    """Валідні ручні перекриття {prom_id: "/category-<slug>.html"} + статистика. Ніколи не падає: зіпсований файл/запис — гучне попередження й пропуск (prom_id лишається на 410, як було).
    Відхиляємо: не-цифровий id, ціль поза форматом, відсутня сторінка site/category-<slug>.html, prom_id що вже має картку в основній мапі (taken)."""
    stats = {"ok": 0, "invalid": 0, "no_page": 0, "has_card": 0}
    try:
        raw = json.loads(OVERRIDES_JSON.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}, stats
    except (OSError, ValueError) as e:
        print(f"[prom_redirects] УВАГА (перекриття): {OVERRIDES_JSON} нечитний ({type(e).__name__}: {str(e)[:120]}) — перекриття не застосовано", file=sys.stderr)
        return {}, stats
    if not isinstance(raw, dict):
        print("[prom_redirects] УВАГА (перекриття): очікую JSON-об'єкт {prom_id: {...}} — перекриття не застосовано", file=sys.stderr)
        return {}, stats
    out = {}
    for key, rec in raw.items():
        pid = key.strip() if isinstance(key, str) else ""
        tgt = (rec or {}).get("target") if isinstance(rec, dict) else None
        m = OVERRIDE_TARGET_RE.fullmatch(tgt) if isinstance(tgt, str) else None
        if not PID_RE.fullmatch(pid) or not m:
            print(f"[prom_redirects] УВАГА (перекриття): запис {str(key)[:30]!r} поза форматом — пропущено", file=sys.stderr)
            stats["invalid"] += 1
            continue
        if pid in taken:
            stats["has_card"] += 1               # цей prom_id має сторінку-картку: перекриття не потрібне і не діє
            continue
        if not (site_dir / f"category-{m.group(1)}.html").is_file():
            print(f"[prom_redirects] УВАГА (перекриття): prom_id {pid}: сторінки site/category-{m.group(1)}.html НЕМАЄ — лишається 410", file=sys.stderr)
            stats["no_page"] += 1
            continue
        out[pid] = tgt
        stats["ok"] += 1
    return out, stats


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

    ov, ov_stats = load_overrides(INDEX_FILE.parent, seen_prom)
    for pid, tgt in ov.items():
        lines.append(f"{pid} {tgt};")
    build_map.override_stats = ov_stats
    lines.sort()
    build_map.apache_lines = [l.rstrip(";") for l in lines]   # той самий набір, формат "prom_id /product-X.html" (+ перекриття "prom_id /category-<slug>.html")
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


_DUP = object()   # маркер дубльованого ключа в JSON (json.loads мовчки лишає останній)


def _warn(msg: str) -> None:
    print(f"[prom_redirects] УВАГА (групи Prom): {msg}", file=sys.stderr)


def _pairs_hook(pairs):
    d = {}
    for k, v in pairs:
        d[k] = _DUP if k in d else v
    return d


def load_group_source(path: Path = None):
    """JSON джерела груп -> dict або None (файл відсутній/зіпсований/не об'єкт — з гучним попередженням)."""
    path = path or GROUP_JSON
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_pairs_hook)
    except FileNotFoundError:
        _warn(f"{path} нема — мапу груп не оновлюю")
        return None
    except (OSError, ValueError) as e:     # ValueError ⊃ JSONDecodeError, UnicodeDecodeError
        _warn(f"{path} нечитний ({type(e).__name__}: {str(e)[:120]}) — мапу груп не оновлюю")
        return None
    if not isinstance(raw, dict):
        _warn(f"{path}: очікую JSON-об'єкт {{gid: {{...}}}}, отримано {type(raw).__name__} — мапу груп не оновлюю")
        return None
    return raw


def build_group_lines(raw: dict, site_dir: Path) -> tuple[list[str], dict]:
    """Рядки мапи груп "gid /category-<slug>.html" (відсортовані: найбільше показів першим) + статистика.
    Ніколи не падає на поганих даних: кожен відхилений запис — рядок у stderr. Вихід валідний за GROUP_LINE_RE."""
    stats = {"null": 0, "invalid": 0, "no_page": 0, "conflict": 0}
    cand = {}          # gid -> (impressions, slug)
    conflict = set()
    for key, rec in raw.items():
        gid = key.strip() if isinstance(key, str) else ""
        if not GID_RE.fullmatch(gid):
            _warn(f"ключ {str(key)[:40]!r} — не gid (лише цифри); пропущено")
            stats["invalid"] += 1
            continue
        if rec is _DUP:
            conflict.add(gid)
            continue
        if not isinstance(rec, dict):
            _warn(f"gid {gid}: значення не об'єкт; пропущено")
            stats["invalid"] += 1
            continue
        cat = rec.get("category")
        if cat is None:
            stats["null"] += 1                 # свідомо без категорії (SEO) — лишається /catalog.html, без шуму
            continue
        slug = cat.strip() if isinstance(cat, str) else ""
        if not SLUG_RE.fullmatch(slug):
            _warn(f"gid {gid}: category {str(cat)[:60]!r} не відповідає [a-z0-9-]+; пропущено")
            stats["invalid"] += 1
            continue
        impr = rec.get("impressions_90d")
        impr = impr if (type(impr) is int and impr > 0) else 0
        if gid in cand:                        # " 123" і "123" — той самий gid: однакова категорія = дубль, різна = неоднозначність
            if cand[gid][1] != slug:
                conflict.add(gid)
            continue
        cand[gid] = (impr, slug)
    for gid in sorted(conflict):
        cand.pop(gid, None)
        _warn(f"gid {gid}: суперечливі записи (дубль ключа в JSON або різні категорії) — неоднозначно, пропущено (лишається /catalog.html)")
        stats["conflict"] += 1
    rows = []
    for gid, (impr, slug) in cand.items():
        if not (site_dir / f"category-{slug}.html").is_file():
            _warn(f"gid {gid}: сторінки site/category-{slug}.html НЕМАЄ у зібраному сайті — пропущено (gid лишається на /catalog.html)")
            stats["no_page"] += 1
            continue
        line = f"{gid} /category-{slug}.html"
        if not GROUP_LINE_RE.fullmatch(line):   # останній рубіж: у мапу Apache не потрапить нічого поза форматом
            _warn(f"gid {gid}: рядок {line!r} поза форматом; пропущено")
            stats["invalid"] += 1
            continue
        rows.append((-impr, len(gid), gid, line))
    rows.sort()
    if cand and not rows:
        _warn(f"жоден із {len(cand)} gid не має сторінки категорії в {site_dir} — сайт ще не зібрано? Мапа груп порожня (усе -> /catalog.html)")
    return [r[3] for r in rows], stats


def write_group_map() -> int:
    """Будує й атомарно пише мапу груп. Повертає кількість рядків (-1 — джерело непридатне, мапа не оновлена).
    Файл мапи мусить існувати завжди (Apache RewriteMap з відсутнім файлом валить configtest) — якщо його нема, пишемо порожню."""
    raw = load_group_source()
    if raw is None:
        if not OUT_FILE_GROUPS_APACHE.exists():
            _write_atomic(OUT_FILE_GROUPS_APACHE, GROUP_HEADER + "\n")
        return -1
    lines, stats = build_group_lines(raw, INDEX_FILE.parent)
    _write_atomic(OUT_FILE_GROUPS_APACHE, "\n".join([GROUP_HEADER] + lines) + "\n")
    print(f"[prom_redirects] {OUT_FILE_GROUPS_APACHE}: {len(lines)} редиректів gid->/category-*.html "
          f"(null-категорія {stats['null']}, без сторінки на сайті {stats['no_page']}, суперечливих {stats['conflict']}, невалідних {stats['invalid']}; решта gid -> /catalog.html)")
    return len(lines)


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
        msg = (f"нова мапа має {n} редиректів проти {prev} (< {SHRINK_GUARD_FRACTION:.0%}) — НЕ перезаписую "
               "(збій кешу/індексу? якщо скорочення свідоме — PROM_REDIRECTS_ALLOW_SHRINK=1).")
        print(f"[prom_redirects] 🚨 {msg}", file=sys.stderr)
        try:
            from telegram_notify import send_throttled_alert
            send_throttled_alert("prom_redirects_shrink", f"🚨 site-rebuild/prom_redirects: {msg} Стара мапа лишилась; Prom-URL поза нею віддають 410.", cooldown_sec=6 * 3600)
        except Exception:  # noqa: BLE001 — алерт best-effort (у тестах/без токена мовчить)
            pass
        sys.exit(2)
    _write_atomic(OUT_FILE, content)
    _write_atomic(OUT_FILE_APACHE, build_map_apache())
    print(f"[prom_redirects] {OUT_FILE}: {n} редиректів prom_id->/product-*.html "
          f"(пропущено {skipped} — нема сторінки на сайті; ці Prom-URL на проді віддають 410 Gone; ручні перекриття на категорії: {getattr(build_map, 'override_stats', {})})")
    try:
        write_group_map()       # окрема мапа; її збій не чіпає мапу товарів вище (вона вже записана)
    except Exception as e:  # noqa: BLE001
        _warn(f"мапу груп не записано ({type(e).__name__}: {str(e)[:150]})")


if __name__ == "__main__":
    main()
