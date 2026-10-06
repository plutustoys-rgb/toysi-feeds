# -*- coding: utf-8 -*-
"""zoobaza_prom_feed.py — Prom-фід постачальника ZooBaza (зоотовари), ПОВНІСТЮ окремо від Toysi-контуру.

СХЕМА: ZooBaza_схема_від_А_до_Я_2026-10-07.md (Cowork-тека), §2 етап 2, §3, §5. Рішення власника 06.10: «повністю окремі
скрипти під цього постачальника, ніякого перетину у коді». Тому цей модуль НЕ імпортує жодного Toysi-модуля
(parser/competitor_pricing/generate_prom_feed…): лише `zoobaza_parser` (власний) і stdlib. Константи комісій — власні (звірено
з `prom.md:677-691`), а не `competitor_pricing` (там дефолти іграшок і `real_toysi_cost` = знижка Toysi + «Збірка» — для ZooBaza хибні).

ЩО РОБИТЬ:
  1. Бере каталог постачальника (`zoobaza_parser.fetch_zoobaza_catalog`) і відбирає ЛИШЕ SKU з білого списку пілоту
     (`zoobaza_pilot_skus.txt` / ZB_PILOT_SKUS_FILE) — порожній список = нічого не публікуємо (fail-closed).
  2. Фільтри: available у фіді постачальника, категорія з білого списку (сумки/лежаки/будки; корми — ніколи), є фото й назва.
  3. ВСІ ідентифікатори з префіксом `zb-` (offer id, vendorCode): числові id Toysi і ZooBaza в одному просторі (5–6 цифр) —
     без префікса збіг дав би чужий товар у Toysi-роутері/чистильниках (карта прив'язок, пастка 3).
  4. Ціна = max(РРЦ постачальника опт×1,5, підлога маржі після комісії Prom і еквайрингу), ціла гривня (ceil). Наявність у фіді
     постачальника — ПРАПОРЕЦЬ (10/0), не залишок: у наш фід віддаємо `quantity_in_stock = ZB_STOCK_QTY` (дефолт 1).
  5. Prom-категорія: `<categoryId>` = id таксономії Prom (181201 переноски / 181206 лежаки / 181203 будки), НЕ «загальне» (25%).
  6. Пише XML ЛИШЕ коли є ≥1 offer; атомарно (tmp → replace); при збої/порожньому результаті стару версію НЕ чіпає (exit 2).

ПУБЛІКАЦІЇ ТУТ НЕМАЄ: файл лягає у `ZB_FEED_OUT` (дефолт feeds_zoobaza/zoobaza_prom_feed.xml). Окремий publisher (не `feed-data`!) — наступний
крок, після підтвердження власником підключення другого імпорту в Prom.

ЗАПУСК: python zoobaza_prom_feed.py [--out PATH] [--dry-run]
"""
import argparse
import math
import os
import struct
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import zoobaza_parser as zp

BASE = Path(__file__).resolve().parent
ID_PREFIX = "zb-"
SHOP_NAME = os.environ.get("ZB_SHOP_NAME", "PlutusToys")
SHOP_URL = os.environ.get("ZB_SHOP_URL", "https://plutustoys.com.ua")

# Категорія постачальника → категорія Prom (prom.md:677-691: усі плоскі 8%). Усе поза цією мапою — пропускається.
PROM_CATEGORY_MAP = {"1059": "181201", "1057": "181206", "1056": "181203"}
PROM_CATEGORY_NAME = {"181201": "Сумки та контейнери для перенесення домашніх тварин",
                      "181206": "Спальні місця для домашніх тварин",
                      "181203": "Будки та вольєри для тварин"}
PROM_COMMISSION_PET = 0.08        # prom.md:683-686 (плоска на категорію). ОНОВЛЮВАТИ при зміні таблиці Prom.
PAYMENT_COMMISSION = 0.037        # еквайринг/оплата Prom (консервативна ставка розстрочки), та сама, що в Toysi-контурі (competitor_pricing, "prom")
RRC_FACTOR = 1.5                  # РРЦ постачальника = опт × 1,5 (SELLER_CHANNEL 178/236)


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        raise ValueError(f"{name}='{raw}' — не число (десяткова ТОЧКА)")
    if not math.isfinite(v) or not (lo <= v <= hi):
        raise ValueError(f"{name}={raw} поза межами [{lo}; {hi}]")
    return v


# Мінімальна ЧИСТА маржа після комісії Prom і еквайрингу (від ціни). Рішення Консультанта — дефолт консервативний.
def min_margin() -> float:
    return _env_float("ZB_MIN_MARGIN", 0.15, 0.0, 0.9)


def stock_qty() -> int:
    return int(_env_float("ZB_STOCK_QTY", 1, 1, 100))


def load_whitelist(path: Path = None) -> set:
    """ID позицій постачальника (offer/@id фіду ZooBaza), по одному в рядку; `#` — коментар. Файл відсутній/порожній → порожня множина."""
    p = Path(path or os.environ.get("ZB_PILOT_SKUS_FILE", "") or (BASE / "zoobaza_pilot_skus.txt"))
    if not p.exists():
        return set()
    out = set()
    for ln in p.read_text(encoding="utf-8-sig").splitlines():
        ln = ln.split("#", 1)[0].strip()
        if ln:
            out.add(ln.removeprefix(ID_PREFIX))   # допускаємо і «zb-123», і «123»
    return out


def compute_price(cost_opt: float, margin: float = None, feed_price: float = None) -> int:
    """Ціна для Prom, ціла гривня. РРЦ постачальника (опт×1,5) АБО підлога чистої маржі — що більше.
    `feed_price` (ціна у фіді постачальника) дає РРЦ без проміжного округлення cost (аудит L-2: round(price/1,5; 2) давав +1 грн)."""
    m = min_margin() if margin is None else margin
    denom = 1.0 - PROM_COMMISSION_PET - PAYMENT_COMMISSION - m
    if denom <= 0:
        raise ValueError(f"маржа {m} + комісії {PROM_COMMISSION_PET + PAYMENT_COMMISSION} ≥ 100%")
    floor = cost_opt / denom
    rrc = feed_price * (RRC_FACTOR / zp.ZOOBAZA_FEED_TO_OPT) if feed_price else cost_opt * RRC_FACTOR
    return int(math.ceil(max(rrc, floor) - 1e-9))


def select_offers(catalog: dict, whitelist: set) -> tuple:
    """Повертає (offers, skipped:{причина: [id…]}). offers — записи з додатковими полями zb_id/prom_category/price_ua."""
    offers, skipped = [], {}
    def skip(why, pid):
        skipped.setdefault(why, []).append(pid)
    for pid in sorted(whitelist):
        it = catalog.get(pid)
        if it is None:
            skip("нема в каталозі постачальника", pid); continue
        if not it.get("available"):
            skip("постачальник: недоступно", pid); continue
        cat = PROM_CATEGORY_MAP.get(str(it.get("category_id")))
        if not cat:
            skip("категорія поза білим списком Prom-мапи", pid); continue
        if not it.get("pictures"):
            skip("без фото", pid); continue
        if not (it.get("name") or "").strip():
            skip("без назви", pid); continue
        try:
            cost = float(it["cost"])
        except (KeyError, TypeError, ValueError):
            skip("нема собівартості", pid); continue
        if cost <= 0:
            skip("нульова собівартість", pid); continue
        offers.append({**it, "zb_id": ID_PREFIX + pid, "prom_category": cat, "price_ua": compute_price(cost, feed_price=it.get("price"))})
    return offers, skipped


def min_photo_px() -> int:
    return int(_env_float("ZB_MIN_PHOTO_PX", 500, 0, 5000))


def image_size(data: bytes):
    """(w, h) із заголовка JPEG/PNG/GIF/WebP без сторонніх бібліотек; None — формат не розпізнано."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", data[6:10])
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            if data[12:16] == b"VP8X":
                return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
            if data[12:16] == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if data[12:16] == b"VP8L":
                b = int.from_bytes(data[21:25], "little")
                return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                m = data[i + 1]
                if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                    i += 2
                    continue
                if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except (struct.error, IndexError):
        pass
    return None


def _fetch_head(url: str, limit: int = 262144) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 PlutusToys-zb-feed", "Range": f"bytes=0-{limit - 1}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(limit)


def photo_gate(offers: list, skipped: dict, fetch=_fetch_head, min_px: int = None) -> list:
    """Лишає offers, чия ПЕРША фото має меншу сторону ≥ min_px (аудит M-2: 58% фото ZooBaza < 500 px, Toysi-фото були ≥ 500).
    Не вдалося завантажити/розпізнати → відсів (fail-closed: не публікуємо наосліп)."""
    mp = min_photo_px() if min_px is None else min_px
    if mp <= 0:
        return offers

    def side(o):
        try:
            sz = image_size(fetch(o["pictures"][0]))
        except Exception:  # noqa: BLE001 — мережа/HTTP
            return None
        return min(sz) if sz else None
    with ThreadPoolExecutor(max_workers=8) as ex:
        sides = list(ex.map(side, offers))
    ok = []
    for o, sd in zip(offers, sides):
        if sd is None:
            skipped.setdefault("фото недоступне/нерозпізнане", []).append(o["id"])
        elif sd < mp:
            skipped.setdefault(f"фото менше {mp} px", []).append(o["id"])
        else:
            ok.append(o)
    return ok


def build_xml(offers: list) -> ET.Element:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    yml = ET.Element("yml_catalog", date=now)
    shop = ET.SubElement(yml, "shop")
    ET.SubElement(shop, "name").text = SHOP_NAME
    ET.SubElement(shop, "company").text = SHOP_NAME
    ET.SubElement(shop, "url").text = SHOP_URL
    cur = ET.SubElement(shop, "currencies")
    ET.SubElement(cur, "currency", id="UAH", rate="1")
    cats = ET.SubElement(shop, "categories")
    for cid in sorted({o["prom_category"] for o in offers}):
        ET.SubElement(cats, "category", id=cid).text = PROM_CATEGORY_NAME.get(cid, cid)
    offers_el = ET.SubElement(shop, "offers")
    q = stock_qty()
    for o in offers:
        offer = ET.SubElement(offers_el, "offer", id=o["zb_id"], available="true")
        # vendorCode = УНІКАЛЬНИЙ id пропозиції (аудит H-1): код постачальника — EAN, спільний для розмірів/кольорів моделі
        # (45% offers ділять його), а Prom віддає vendorCode як sku → toysi_code не розрізняв би варіанти. Код постачальника — у param.
        ET.SubElement(offer, "vendorCode").text = o["zb_id"]
        ET.SubElement(offer, "name").text = o["name"]
        ET.SubElement(offer, "name_ua").text = o["name"]
        ET.SubElement(offer, "price").text = f"{o['price_ua']:.2f}"
        ET.SubElement(offer, "currencyId").text = "UAH"
        ET.SubElement(offer, "quantity_in_stock").text = str(q)
        ET.SubElement(offer, "categoryId").text = o["prom_category"]
        for pic in o["pictures"][:10]:
            ET.SubElement(offer, "picture").text = pic
        if o.get("vendor"):
            ET.SubElement(offer, "vendor").text = o["vendor"]
        desc = (o.get("description") or o["name"]).strip()
        ET.SubElement(offer, "description").text = desc
        ET.SubElement(offer, "description_ua").text = desc
        if o.get("color"):
            ET.SubElement(offer, "param", name="Колір").text = o["color"]
        if o.get("vendor_code"):
            ET.SubElement(offer, "param", name="Код постачальника").text = o["vendor_code"]
    return yml


def write_atomic(root: ET.Element, out: Path) -> None:
    ET.indent(root, space="  ")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(xml)
        os.replace(tmp, out)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def run(out: Path = None, dry_run: bool = False, catalog: dict = None, whitelist: set = None, photo_fetch=None) -> int:
    out = Path(out or os.environ.get("ZB_FEED_OUT", "") or (BASE / "feeds_zoobaza" / "zoobaza_prom_feed.xml"))
    wl = load_whitelist() if whitelist is None else whitelist
    if not wl:
        print("[ZB-feed] білий список пілоту порожній/відсутній — НІЧОГО не публікуємо (fail-closed), старий файл не чіпаю.", file=sys.stderr)
        return 2
    live = catalog is None            # живий запуск: перевіряємо роздільність фото (у тестах каталог підставляється)
    try:
        if catalog is None:
            catalog = zp.fetch_zoobaza_catalog()
            zp.assert_cost_constant(catalog)       # константа опт×1,5 (з 07.10) перевірена на контрольних SKU: інакше ціни хибні
    except Exception as e:  # noqa: BLE001 — мережа/формат/константа: стара версія лишається
        print(f"[ZB-feed] каталог постачальника недоступний/некоректний ({type(e).__name__}: {e}) — файл НЕ оновлено.", file=sys.stderr)
        return 2
    offers, skipped = select_offers(catalog, wl)
    if photo_fetch is not None or live:
        offers = photo_gate(offers, skipped, fetch=photo_fetch or _fetch_head)
    for why, ids in skipped.items():
        print(f"[ZB-feed] пропущено — {why}: {len(ids)} ({', '.join(ids[:8])}{'…' if len(ids) > 8 else ''})")
    if not offers:
        print("[ZB-feed] жодного придатного offer — файл НЕ оновлено (fail-closed).", file=sys.stderr)
        return 2
    print(f"[ZB-feed] offers: {len(offers)} з {len(wl)} у білому списку; ціни {min(o['price_ua'] for o in offers)}–{max(o['price_ua'] for o in offers)} ₴.")
    if dry_run:
        print("[ZB-feed] dry-run: файл не записано.")
        return 0
    write_atomic(build_xml(offers), out)
    print(f"[ZB-feed] записано: {out}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sys.exit(run(Path(a.out) if a.out else None, a.dry_run))
