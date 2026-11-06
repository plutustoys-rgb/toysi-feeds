#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""site_gone_pages.py — мапа 410 Gone для сторінок сайту, знятих НАЗАВЖДИ (06.11.2026, рішення власника «хімію прибирай»; завдання SEO).

ЧОМУ: build_site.py (крок 8) видаляє HTML сторінок, яких немає в поточній збірці, і Apache віддав би 404. Для товару, знятого назавжди
(політика forbidden_products, не тимчасова відсутність залишку), правильна відповідь — 410 Gone: 404 не припиняє переобхід, а 301 на
іграшкову категорію Google трактує як soft-404 (той самий принцип, що 410 для мертвих Prom-URL, Консультант 11.10.2026).

ДЖЕРЕЛО ПРАВДИ — site/removed_pages.json: {"products": {"<toysi_id>": {category, name}}, "categories": {"<slug>": "<назва Toysi>"}}.
Скрипт пише Apache RewriteMap txt `$APP/site_gone_apache.map` (не в git), рядки «product-<id> gone» / «category-<slug> gone»; правило
у deploy/activate_site_apache.sh (`RewriteMap sitegone`) віддає 410 для /product-<id>.html і /category-<slug>[_N].html з мапи.
Перший рядок product-… і перший category-… беруть проби активації.

ЗАПОБІЖНИКИ:
  • сторінка, що ЖИВА в поточній збірці (site/product-<id>.html або site/category-<slug>.html є на диску), у мапу НЕ йде — гучне
    попередження в stderr (410 ніколи не перекриє справжню сторінку, напр. якщо заборону колись знімуть, а запис лишився);
  • id — лише [0-9]+ або rt-[0-9]+, slug — лише [a-z0-9-]+ (символи, з якими RewriteRule збігається), інакше запис пропущено з попередженням;
  • ВІДСУТНІЙ/зіпсований/нечитний JSON → наявна мапа НЕ перезаписується (exit 1); мапи ще нема → пишемо порожню (RewriteMap з відсутнім файлом валить configtest);
  • збій або пропущені записи → тротлений Telegram-алерт (як generate_prom_redirects; у site-rebuild крок не-фатальний, тож без алерту збій тонув би в journal).
Тест: test_site_removed_pages.py.
"""
import json
import os
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
SITE_DIR = Path(os.environ.get("SITE_DIR", str(BASE / "site")))
SOURCE_JSON = Path(os.environ.get("SITE_REMOVED_PAGES_JSON", str(SITE_DIR / "removed_pages.json")))
OUT_FILE = Path(os.environ.get("SITE_GONE_MAP_APACHE", str(BASE / "site_gone_apache.map")))
PRODUCT_ID_RE = re.compile(r"(?:rt-)?[0-9]{1,20}")      # fullmatch; явний клас, а не \d (той приймає не-ASCII цифри)
SLUG_RE = re.compile(r"[a-z0-9-]{1,200}")
LINE_RE = re.compile(r"(?:product-(?:rt-)?[0-9]{1,20}|category-[a-z0-9-]{1,200}) gone")
HEADER = ("# АВТО-ЗГЕНЕРОВАНО site_gone_pages.py з site/removed_pages.json — Apache RewriteMap txt: сторінки, зняті назавжди → 410 Gone.\n"
          "# Формат: «product-<id> gone» / «category-<slug> gone». Живі сторінки поточної збірки сюди не потрапляють.\n")


def _warn(msg: str) -> None:
    print(f"[site_gone] УВАГА: {msg}", file=sys.stderr)
    _warn.log.append(msg)


_warn.log = []


def _alert(text: str) -> None:
    """Тротлений Telegram-алерт, як у сусіднього generate_prom_redirects (site-rebuild біжить кожні 2 год; збій не має тонути в journal)."""
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert("site_gone_map", f"🚨 site-rebuild/site_gone_pages: {text[:900]}", cooldown_sec=6 * 3600)
    except Exception:  # noqa: BLE001 — алерт best-effort (у тестах/без токена мовчить)
        pass


def load_source(path: Path = None):
    """-> (products: list[str], categories: list[str]) або None, якщо файл нечитний/поза форматом (тоді мапу не чіпаємо)."""
    path = path or SOURCE_JSON
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        _warn(f"{path} ВІДСУТНІЙ (не задеплоєно? видалено?) — мапу 410 не оновлюю, наявна лишається")
        return None
    except (OSError, ValueError, RecursionError) as e:
        _warn(f"{path} нечитний ({type(e).__name__}: {str(e)[:120]}) — мапу 410 не оновлюю")
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("products", {}), dict) or not isinstance(raw.get("categories", {}), dict):
        _warn(f"{path}: очікую {{\"products\": {{...}}, \"categories\": {{...}}}} — мапу 410 не оновлюю")
        return None
    prods, cats = [], []
    for pid in raw.get("products", {}):
        if isinstance(pid, str) and PRODUCT_ID_RE.fullmatch(pid):
            prods.append(pid)
        else:
            _warn(f"товар {str(pid)[:40]!r} поза форматом (лише цифри або rt-цифри) — пропущено")
    for slug in raw.get("categories", {}):
        if isinstance(slug, str) and SLUG_RE.fullmatch(slug):
            cats.append(slug)
        else:
            _warn(f"категорія {str(slug)[:40]!r} поза форматом [a-z0-9-]+ — пропущено")
    return prods, cats


def build_lines(prods, cats, site_dir: Path = None) -> list:
    """Рядки мапи: спершу товари, потім категорії (кожна група — у порядку джерела). Живі сторінки пропускаються з попередженням."""
    site_dir = site_dir or SITE_DIR
    lines = []
    for pid in dict.fromkeys(prods):
        if (site_dir / f"product-{pid}.html").exists():
            _warn(f"site/product-{pid}.html ЖИВА в поточній збірці — 410 для неї НЕ ставлю (заборону знято? прибери запис із removed_pages.json)")
            continue
        lines.append(f"product-{pid} gone")
    for slug in dict.fromkeys(cats):
        if (site_dir / f"category-{slug}.html").exists():
            _warn(f"site/category-{slug}.html ЖИВА в поточній збірці — 410 для неї НЕ ставлю (прибери запис із removed_pages.json)")
            continue
        lines.append(f"category-{slug} gone")
    return lines


def _write_atomic(path: Path, text: str) -> None:
    """tmp + os.replace (Apache не прочитає напівзаписану мапу); LF незалежно від ОС; 0644 — новий inode не успадковує ACL."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main() -> int:
    _warn.log.clear()
    src = load_source()
    if src is None:
        if not OUT_FILE.exists():
            _write_atomic(OUT_FILE, HEADER)
        _alert("мапу 410 НЕ оновлено: " + " | ".join(_warn.log) + " Зняті сторінки віддають те, що в наявній мапі (або 404, якщо її нема).")
        return 1
    lines = build_lines(*src)
    if _warn.log:
        _alert("мапу 410 записано, але з пропусками: " + " | ".join(_warn.log[:5]) + (f" (+ ще {len(_warn.log) - 5})" if len(_warn.log) > 5 else ""))
    assert all(LINE_RE.fullmatch(l) for l in lines)
    _write_atomic(OUT_FILE, HEADER + "".join(l + "\n" for l in lines))
    print(f"[site_gone] {OUT_FILE.name}: {len(lines)} адрес → 410 Gone "
          f"(товарів {sum(l.startswith('product-') for l in lines)}, категорій {sum(l.startswith('category-') for l in lines)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
