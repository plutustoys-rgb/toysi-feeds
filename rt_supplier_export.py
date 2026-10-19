# -*- coding: utf-8 -*-
"""rt_supplier_export.py — дані пілоту RoyalToys для сайту (рішення власника 19.10.2026): відібрані SEO картки `rt-<id>` → ПРИВАТНИЙ <корінь>/supplier_rt.json (ПОЗА DocumentRoot: там опт "cost"), фото на НАШОМУ сервері site/img/rt/<id>-N.jpg.

Контракт з Shopify-code (site/build_site.py `_load_supplier_rt`): {"updated": ISO UTC, "items": {"rt-<id>": {name, description, vendor, vendor_code, category (рос. назва RoyalToys), images[https://…], price, cost, stock}}}.
Правила: назва й опис ЛИШЕ українські (name_ua/description_ua; нема name_ua → картки нема); ціна сайту за ЗАКРИТОЮ формулою max(опт×1,5; (опт+15)×1,03), вгору до гривні; відбір — rt_pilot_ids.json (лише id, без грошей);
позиція без фото на диску, з нульовою/нечисловою ціною — не публікується. stock = 0, якщо offer не available. YML RoyalToys порожній/недоступний → попередній файл НЕ перезаписується (build_site сам вимкне картки, коли "updated" старший за 3 доби — fail-closed), сигнал раз на добу.
Файл пишеться атомарно, права 0600. Запуск: rt-supplier-export.timer щогодини (залишки в YML RoyalToys оновлюються щогодини). stock_snapshot.py додає в site/stock.json ключі rt-<id> з цього файлу."""
import html
import io
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
OUT_FILE = Path(os.environ.get("SUPPLIER_RT_FILE", str(HERE / "supplier_rt.json")))
IDS_FILE = HERE / "rt_pilot_ids.json"
IMG_DIR = Path(os.environ.get("SITE_DIR", str(HERE / "site"))) / "img" / "rt"
IMG_URL = "https://plutustoys.com.ua/img/rt/{name}"
MAX_IMAGES = 5
IMG_MAX_SIDE = 1200
IMG_TIMEOUT = 30
IMG_MAX_BYTES = 15 * 1024 * 1024
_ID_RE = re.compile(r"rt-[0-9A-Za-z_]+")      # як build_site._RT_ID_RX


def site_price(cost: float) -> int:
    """Закрита формула ціни сайту (власник 13.10.2026): max(опт×1,5; (опт+15)×1,03), вгору до гривні. НЕ змінювати без слова власника."""
    return int(math.ceil(max(cost * 1.5, (cost + 15) * 1.03)))


def _alert(key: str, text: str) -> None:
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(key, text, cooldown_sec=24 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[rt_export] сигнал не надіслано: {type(e).__name__}", file=sys.stderr)


_TAG_RE = re.compile(r"<[^>]+>")


def plain_text(raw: str) -> str:
    """Опис RoyalToys інколи містить HTML-теги: лишаємо чистий текст (теги прибрано, сутності розкодовано, абзаци збережено)."""
    t = re.sub(r"(?i)<\s*(?:br\s*/?|/p|/li|/div)\s*>", "\n", raw or "")
    t = html.unescape(_TAG_RE.sub("", t))
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def load_pilot_ids(path: Path = IDS_FILE) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = [str(i) for i in data.get("ids") or [] if _ID_RE.fullmatch(str(i))]
    if not ids:
        raise ValueError("rt_pilot_ids.json: порожній список")
    return ids


def build_item(rid: str, v: dict, images: list) -> dict | None:
    """Елемент supplier_rt.json або None (не публікуємо). Чиста функція (без мережі)."""
    name = (v.get("name_ua") or "").strip()
    if not name or not images:
        return None
    try:
        cost = float(str(v.get("price") or "").replace(",", "."))
    except ValueError:
        return None
    if cost <= 0 or not math.isfinite(cost):
        return None
    try:
        stock = max(0, int(v.get("stock") or 0)) if v.get("available") else 0
    except (TypeError, ValueError):
        stock = 0
    dims = v.get("dims_cm")
    if not dims or len(dims) != 3 or not all(isinstance(x, (int, float)) and x > 0 for x in dims):
        return None      # без габаритів картку не публікуємо (ТТН потребує розмірів; рішення Коду 19.10, чекаємо підтвердження RoyalToys)
    wt = v.get("weight_kg")
    return {"name": name, "description": plain_text(v.get("description_ua")), "vendor": (v.get("vendor") or "").strip(), "vendor_code": (v.get("vendor_code") or "").strip(),
            "category": (v.get("category_name") or "").strip(), "images": images, "price": site_price(cost), "cost": round(cost, 2), "stock": stock,
            "dims_cm": [round(float(x), 1) for x in dims], "weight_kg": round(float(wt), 3) if isinstance(wt, (int, float)) and wt > 0 else None}


def fetch_image(url: str, dest: Path) -> bool:
    """Завантажує фото, стискає до JPEG ≤1200 px по довшій стороні. Є файл — не качаємо вдруге. Збій → False (картка піде без цього фото)."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    try:
        import requests
        from PIL import Image
        if not str(url).startswith(("http://", "https://")):
            return False
        r = requests.get(url, timeout=IMG_TIMEOUT, stream=True)
        r.raise_for_status()
        body = r.raw.read(IMG_MAX_BYTES + 1, decode_content=True)
        if len(body) > IMG_MAX_BYTES:
            raise ValueError("фото завелике")
        im = Image.open(io.BytesIO(body))
        if im.mode in ("RGBA", "LA", "P"):         # прозорі PNG — на білий фон (convert("RGB") дав би чорний)
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            bg.alpha_composite(im)
            im = bg
        im = im.convert("RGB")
        im.thumbnail((IMG_MAX_SIDE, IMG_MAX_SIDE))
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        im.save(tmp, "JPEG", quality=85, optimize=True)
        os.replace(tmp, dest)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[rt_export] фото не отримано {url[:80]}: {type(e).__name__}", file=sys.stderr)
        return False


def images_for(rid: str, pictures: list, fetch=fetch_image, img_dir: Path = IMG_DIR) -> list:
    out = []
    for n, url in enumerate((pictures or [])[:MAX_IMAGES], 1):
        name = f"{rid}-{n}.jpg"
        if fetch(url, img_dir / name):
            out.append(IMG_URL.format(name=name))
    return out


def run_once(catalog: dict | None = None, ids: list | None = None, out: Path | None = None, fetch=fetch_image, img_dir: Path = IMG_DIR, now: datetime | None = None) -> bool:
    """True — файл записано. Порожній каталог → попередній файл лишається, False."""
    out = Path(out or OUT_FILE)
    try:
        ids = ids or load_pilot_ids()
        if catalog is None:
            import royaltoys_parser
            catalog = royaltoys_parser.fetch_royaltoys_catalog()
        if not catalog:
            _alert("rt_export_empty", "⚠️ rt_supplier_export: YML RoyalToys порожній/недоступний (ROYALTOYS_YML_URL у .env на VPS?) — supplier_rt.json НЕ оновлено; через 3 доби картки rt- зникнуть із сайту (fail-closed).")
            return False
        items, missing, nophoto = {}, [], []
        for sid in ids:
            rid = sid[3:]
            v = catalog.get(rid)
            if not v:
                missing.append(sid)
                continue
            if build_item(sid, v, ["-"]) is None:      # назва/ціна невалідні — фото даремно не качаємо
                nophoto.append(sid)
                continue
            it = build_item(sid, v, images_for(sid, v.get("pictures") or [], fetch, img_dir))
            if it is None:
                nophoto.append(sid)
                continue
            items[sid] = it
        if not items:
            _alert("rt_export_none", f"⚠️ rt_supplier_export: жодної з {len(ids)} відібраних карток не зібрано (нема в YML: {len(missing)}, без фото/назви/ціни: {len(nophoto)}) — supplier_rt.json НЕ оновлено.")
            return False
        if missing or nophoto:
            print(f"[rt_export] пропущено: нема в YML {len(missing)} ({missing[:5]}), без фото/назви/ціни {len(nophoto)} ({nophoto[:5]})", file=sys.stderr)
        payload = {"updated": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "items": items}
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, out)
        print(f"[rt_export] ✅ {len(items)} з {len(ids)} карток, пропущено {len(missing) + len(nophoto)}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[rt_export] збій: {type(e).__name__}: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    sys.exit(0 if run_once() else 1)
