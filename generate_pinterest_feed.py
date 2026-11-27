# -*- coding: utf-8 -*-
"""generate_pinterest_feed.py — каталог товарів для Pinterest (запит SMM 27.11.2026, слово власника «п.2 згоден»).

Джерело: feeds/meta_feed.xml (уже живі, in stock, з коректним URL і фото). Відсів — той самий, що в соц-постері (`social_auto_poster._safe_and_rotated`:
зброя/BAD_NAME_RE/forbidden_products/FEED_EXCLUDED_PRODUCT_IDS, сезон 01.10–31.03 без літніх, ціна 80–1500 грн). Ціни/наявність беруться з Meta-фіда — власних цін
не вигадуємо. UTM у link: utm_source=pinterest&utm_medium=catalog&utm_campaign=pinterest_catalog.
Вихід: feeds/pinterest_feed.xml (RSS 2.0 у форматі Google/Meta — Pinterest приймає XML-каталоги; той самий XML-гейт публікації feed_publish_gate.sh), атомарно (tmp+replace).
Порожній/зламаний вхід → файл НЕ перезаписується (публікація лишає попередню версію). Запуск: python3 generate_pinterest_feed.py [--src шлях] [--out шлях]"""
import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

BASE = Path(__file__).resolve().parent
SRC = BASE / "feeds" / "meta_feed.xml"
OUT = BASE / "feeds" / "pinterest_feed.xml"
G = "http://base.google.com/ns/1.0"
NS = {"g": G}
G_TAGS = {"id", "image_link", "price", "availability", "condition", "brand"}
ET.register_namespace("g", G)
COLUMNS = ["id", "title", "description", "link", "image_link", "price", "availability", "condition", "brand"]
UTM = (("utm_source", "pinterest"), ("utm_medium", "catalog"), ("utm_campaign", "pinterest_catalog"))


def with_utm(url: str) -> str:
    try:
        p = urlsplit(url)
        q = parse_qsl(p.query, keep_blank_values=True)
        if not p.scheme or any(k == "utm_source" for k, _ in q):
            return url
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q + list(UTM)), p.fragment))
    except Exception:  # noqa: BLE001
        return url


def _cell(v) -> str:
    return " ".join(str(v or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").split())


def load_items(src: Path) -> list:
    root = ET.parse(src).getroot()
    out = []
    for it in root.iter("item"):
        g = lambda t: (it.findtext("g:" + t, namespaces=NS) or "").strip()
        sku = g("id")
        name = (it.findtext("title") or "").strip()
        url = (it.findtext("link") or "").strip()
        image = g("image_link")
        avail = g("availability").lower()
        if not (sku and name and url and image) or avail != "in stock":
            continue
        out.append({"sku": sku, "name": name, "url": url, "image": image, "price": g("price"),
                    "description": (it.findtext("description") or "").strip(), "brand": g("brand"), "condition": g("condition") or "new"})
    return out


# Рішення SMM 27.11 (аудит #2089, P1/P2): (1) фото на чужому нестабільному CDN images.prom.ua в каталог Pinterest НЕ беремо (зламане фото = відхилення/пониження каталогу);
# (2) військові набори «Солдати/Солдатики/Блок-пост/ЗСУ» — у відсів нарівні зі зброєю.
EXCLUDED_IMAGE_HOSTS = ("images.prom.ua",)
# Явно за id (рішення SMM 27.11: відсів за описом НЕ розширювати — ризик хибних спрацювань): y35922 — воєнна настільна гра «Прорив» (танки/ракетні установки/солдати), 279290 — набір A.C.I.D. з розділу «Солдатики».
EXCLUDED_PRODUCT_IDS = {"y35922", "279290"}
MILITARY_RE = re.compile(r"солдат|блок[-\s]?пост|(?<![а-яіїєґ])зсу(?![а-яіїєґ])", re.I)


def _keep(p: dict) -> bool:
    if str(p.get("sku")) in EXCLUDED_PRODUCT_IDS:
        return False
    if any(h in (p.get("image") or "") for h in EXCLUDED_IMAGE_HOSTS):
        return False
    return not MILITARY_RE.search(p.get("name") or "")


def build_rows(items: list, today=None) -> list:
    import social_auto_poster as sp
    kept = [p for p in sp._safe_and_rotated(items, today=today) if _keep(p)]   # відсів і порядок як у соц-постері + рішення SMM (див. вище)
    return [[_cell(p["sku"]), _cell(p["name"]), _cell(p["description"])[:500], with_utm(p["url"]), _cell(p["image"]),
             _cell(p["price"]), "in stock", _cell(p["condition"]), _cell(p["brand"])] for p in kept]


def write_xml(rows: list, out: Path) -> None:
    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = "PlutusToys — каталог для Pinterest"
    ET.SubElement(ch, "link").text = "https://plutustoys.com.ua"
    ET.SubElement(ch, "description").text = "Іграшки для дітей — PlutusToys"
    for r in rows:
        it = ET.SubElement(ch, "item")
        for tag, val in zip(COLUMNS, r):
            ET.SubElement(it, "{%s}%s" % (G, tag) if tag in G_TAGS else tag).text = val
    tmp = out.with_suffix(out.suffix + ".tmp")
    ET.ElementTree(rss).write(tmp, encoding="utf-8", xml_declaration=True)
    os.replace(tmp, out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(SRC))
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    src, out = Path(a.src), Path(a.out)
    try:
        items = load_items(src)
    except (OSError, ET.ParseError) as e:
        print(f"[pinterest_feed] джерело недоступне/биті XML ({type(e).__name__}) — файл НЕ чіпаю", file=sys.stderr)
        return 1
    rows = build_rows(items)
    if not rows:
        print("[pinterest_feed] 0 товарів після відсіву — файл НЕ чіпаю", file=sys.stderr)
        return 1
    write_xml(rows, out)
    print(f"[pinterest_feed] ОК: {len(rows)} товарів із {len(items)} in stock -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
