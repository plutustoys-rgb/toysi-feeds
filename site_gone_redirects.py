#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""site_gone_redirects.py — мапа 301 для ЗНЯТИХ карток із попитом → найближчий живий аналог (27.11.2026, завдання SEO: GSC 28 днів, 135 знятих карток мали 432 покази, а віддавали 410).

ЧОМУ ТАК. Загальне правило сайту (PR #2042): картка без файлу в site/ → 410 Gone. Для вузького списку карток, за якими ще шукають, є справжній аналог (той самий товар/модель/тип) —
для них 301 на аналог. 301 на невідповідну сторінку Google трактує як soft-404 (Консультант 11.10.2026), тому список КУРОВАНИЙ (site/gone_redirects.json), а не автоматичний.

ДЖЕРЕЛО: site/gone_redirects.json {"redirects": {"<id знятої>": "<id цілі>"}}; id — [0-9]+, ціль ще й rt-[0-9A-Za-z_]+ (публічна адреса rt-X — product-yX.html, якщо ворота SITE_PUBLIC_IDS_LIVE; беремо файл, який є).
ВИХІД: Apache RewriteMap txt `$APP/site_gone_redirect_apache.map`, рядки «product-<знята> product-<ціль>.html»; правило — deploy/activate_site_apache.sh (до загального 410).

ЗАПОБІЖНИКИ (пара пропускається з гучним попередженням, збірка не падає):
  • знята картка ЖИВА в поточній збірці (site/product-<id>.html є) → не редіректимо живу сторінку;
  • ціль НЕ існує в збірці (немає файлу) → не ведемо на 404/410;
  • ціль сама є знятою (ланцюжок) або id цілі = id знятої → пропущено;
  • id поза форматом (лише ASCII-цифри; ціль ще rt-…) → пропущено;
  • ВІДСУТНІЙ/зіпсований/нечитний JSON → наявна мапа НЕ перезаписується (exit 1); порожній валідний JSON при непорожній мапі → теж не чіпаємо;
  • мапи ще нема → пишемо порожню (RewriteMap з відсутнім файлом валить configtest); збій → тротлений Telegram-алерт (best-effort).
Тест: test_site_gone_redirects.py."""
import json
import os
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
SITE_DIR = Path(os.environ.get("SITE_DIR", str(BASE / "site")))
SOURCE_JSON = Path(os.environ.get("SITE_GONE_REDIRECTS_JSON", str(SITE_DIR / "gone_redirects.json")))
OUT_FILE = Path(os.environ.get("SITE_GONE_REDIRECT_MAP_APACHE", str(BASE / "site_gone_redirect_apache.map")))
SRC_ID_RE = re.compile(r"[0-9]{1,20}")                           # fullmatch; явний клас, а не \d (той приймає не-ASCII цифри)
DST_ID_RE = re.compile(r"(?:rt-[0-9A-Za-z_]{1,40}|[0-9]{1,20})")
LINE_RE = re.compile(r"product-[0-9]{1,20} product-(?:y[0-9A-Za-z_]{1,40}|rt-[0-9A-Za-z_]{1,40}|[0-9]{1,20})\.html")
HEADER = ("# АВТО-ЗГЕНЕРОВАНО site_gone_redirects.py з site/gone_redirects.json — Apache RewriteMap txt: знята картка з попитом → 301 на живий аналог.\n"
          "# Формат: «product-<знята> product-<ціль>.html». Знята сторінка не існує в збірці; ціль існує.\n")


def _warn(msg: str) -> None:
    print(f"[site_gone_redirects] УВАГА: {msg}", file=sys.stderr)
    _warn.log.append(msg)


_warn.log = []


def _alert(text: str) -> None:
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert("site_gone_redirects", f"🚨 site-rebuild/site_gone_redirects: {text[:900]}", cooldown_sec=6 * 3600)
    except Exception:  # noqa: BLE001 — алерт best-effort (у тестах/без токена мовчить)
        pass


def load_source(path: Path = None):
    """-> {src_id: dst_id} або None, якщо файл нечитний/поза форматом (тоді мапу не чіпаємо)."""
    path = path or SOURCE_JSON
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        _warn(f"{path} ВІДСУТНІЙ — мапу 301 не оновлюю, наявна лишається")
        return None
    except (OSError, ValueError, RecursionError) as e:
        _warn(f"{path} нечитний ({type(e).__name__}: {str(e)[:120]}) — мапу 301 не оновлюю")
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("redirects"), dict):
        _warn(f"{path}: очікую {{\"redirects\": {{\"<id>\": \"<id>\"}}}} — мапу 301 не оновлюю")
        return None
    return raw["redirects"]


def target_file(dst: str, site_dir: Path):
    """Ім'я файлу цілі, що ІСНУЄ в збірці (rt-X: публічний product-yX.html або старий product-rt-X.html), або None."""
    cands = [f"product-{dst}.html"]
    if dst.startswith("rt-"):
        cands.insert(0, f"product-y{dst[3:]}.html")
    for c in cands:
        if (site_dir / c).exists():
            return c
    return None


def build_lines(pairs: dict, site_dir: Path = None) -> list:
    site_dir = site_dir or SITE_DIR
    srcs = {str(k) for k in pairs}
    lines, seen = [], set()
    for src, dst in pairs.items():
        src, dst = str(src), str(dst)
        if not SRC_ID_RE.fullmatch(src) or not DST_ID_RE.fullmatch(dst):
            _warn(f"пара {src[:30]!r} → {dst[:30]!r} поза форматом — пропущено")
            continue
        if src == dst:
            _warn(f"{src} → сама на себе — пропущено")
            continue
        if src in seen:
            continue
        if (site_dir / f"product-{src}.html").exists():
            _warn(f"site/product-{src}.html ЖИВА в поточній збірці — 301 для неї НЕ ставлю (прибери пару з gone_redirects.json)")
            continue
        if dst in srcs:
            _warn(f"{src} → {dst}: ціль сама є знятою (ланцюжок) — пропущено")
            continue
        tf = target_file(dst, site_dir)
        if tf is None:
            _warn(f"{src} → {dst}: цілі нема в збірці (site/product-{dst}.html) — пропущено, лишається 410")
            continue
        line = f"product-{src} {tf}"
        if not LINE_RE.fullmatch(line):
            _warn(f"{src} → {dst}: рядок «{line[:60]}» поза форматом — пропущено")
            continue
        seen.add(src)
        lines.append(line)
    return lines


def _write_atomic(path: Path, text: str) -> None:
    """tmp + os.replace (Apache не прочитає напівзаписану мапу); LF незалежно від ОС; 0644."""
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
        _alert("мапу 301 НЕ оновлено: " + " | ".join(_warn.log))
        return 1
    if not src:
        try:
            prev = [l for l in OUT_FILE.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
        except FileNotFoundError:
            prev = []
        except (OSError, ValueError):
            prev = ["<нечитна наявна мапа>"]
        if prev:
            _warn(f"{SOURCE_JSON} ПОРОЖНІЙ, а наявна мапа має {len(prev)} адрес — мапу 301 не оновлюю (щоб прибрати всі 301 навмисно — видали мапу вручну)")
            _alert("мапу 301 НЕ оновлено: " + " | ".join(_warn.log))
            return 1
    lines = build_lines(src)
    if _warn.log:
        _alert("мапу 301 записано, але з пропусками: " + " | ".join(_warn.log[:5]) + (f" (+ ще {len(_warn.log) - 5})" if len(_warn.log) > 5 else ""))
    assert all(LINE_RE.fullmatch(l) for l in lines)
    _write_atomic(OUT_FILE, HEADER + "".join(l + "\n" for l in lines))
    print(f"[site_gone_redirects] {OUT_FILE.name}: {len(lines)} знятих карток → 301 на живий аналог")
    return 0


if __name__ == "__main__":
    sys.exit(main())
