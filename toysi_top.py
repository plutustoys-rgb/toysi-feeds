# -*- coding: utf-8 -*-
"""toysi_top.py — «ТОП» Toysi → site/toysi_top.json (запит Сайту/власника 16.10: «у Тойсі є ТОП відвантажень, куди ви ці товари ставите?»).

Публічні сторінки toysi.ua (як їх бачить будь-який відвідувач): /ua/top-prodazh («ТОП відвантажень»), /ua/top-prodazh-dnya («ТОП вчорашнього дня»), /ua/top-prodazh-nedeli («ТОП 7 днів»), /ua/top-prodazh-mesyatsa («ТОП 30 днів»).
Кожна картка має `data-photo2="/p/<n>/enl_<ID>.jpg"` — ID = НАШ toysi_id (збігається з каталогом/фідами), порядок як на Toysi, на сторінці 52 картки (параметр ?page= список не змінює: інші картки підвантажуються скриптом, їх не беремо).
ВИХІД: site/toysi_top.json = {"date","fetched_at","lists":{"shipments":[ids],"yesterday":[ids],"week":[ids],"month":[ids]},"counts":{"shipments":{"toysi":52,"on_site":N},…}}; ids — рядки, лише ті, що є в site/index.json (тобто реально продаємо й показуємо на сайті), порядок збережено.
Збій мережі/розмітки (<10 id на сторінці) НЕ перезаписує попередній файл (фронт бачить поле date і сам вирішує про свіжість). Лише читання з Toysi, 4 запити за запуск, чесний User-Agent. Запуск: `python toysi_top.py` (VPS-таймер 2 рази на добу)."""
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
SITE_DIR = Path(os.environ.get("SITE_DIR", str(HERE / "site")))
OUT_FILE = SITE_DIR / "toysi_top.json"
INDEX_FILE = SITE_DIR / "index.json"
BASE = "https://toysi.ua"
LISTS = {"shipments": "/ua/top-prodazh", "yesterday": "/ua/top-prodazh-dnya", "week": "/ua/top-prodazh-nedeli", "month": "/ua/top-prodazh-mesyatsa"}
UA = {"User-Agent": "PlutusToys-top/1.0 (+https://plutustoys.com.ua)"}
MIN_IDS = 10
_ID = re.compile(r'data-photo2="/p/\d+/enl_(\d+)\.jpg"')


def parse_ids(html: str) -> list:
    """ID товарів зі сторінки Toysi у порядку появи, без повторів."""
    seen, out = set(), []
    for i in _ID.findall(html or ""):
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def fetch_html(path: str, getter=None, retries: int = 2) -> str | None:
    import requests
    get = getter or (lambda u: requests.get(u, headers=UA, timeout=30))
    for k in range(retries + 1):
        try:
            r = get(BASE + path)
            if r.status_code == 200 and r.text:
                return r.text
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2 * (k + 1))
    return None


def build(pages: dict, site_ids: set, now: datetime) -> dict | None:
    """Збирає файл або None, якщо хоч одна сторінка дала <MIN_IDS (розмітка змінилась/збій) — тоді попередній файл не чіпаємо."""
    lists, counts = {}, {}
    for name, html in pages.items():
        ids = parse_ids(html)
        if len(ids) < MIN_IDS:
            return None
        lists[name] = [i for i in ids if i in site_ids]
        counts[name] = {"toysi": len(ids), "on_site": len(lists[name])}
    return {"date": now.strftime("%Y-%m-%d"), "fetched_at": now.isoformat(timespec="seconds"), "lists": lists, "counts": counts}


def main(getter=None, now: datetime | None = None, out_file: Path | None = None, index_file: Path | None = None) -> int:
    now = now or datetime.now()
    out_file = Path(out_file or OUT_FILE)
    try:
        index = json.loads(Path(index_file or INDEX_FILE).read_text(encoding="utf-8-sig"))
        site_ids = {str(x["id"]) for x in index if isinstance(x, dict) and x.get("id")}
    except (OSError, ValueError, TypeError):
        site_ids = set()
    if not site_ids:
        print("[toysi_top] site/index.json порожній/нечитабельний: файл не оновлено", file=sys.stderr)
        return 1
    pages = {}
    for name, path in LISTS.items():
        html = fetch_html(path, getter)
        if html is None:
            print(f"[toysi_top] {path}: не вдалось отримати: файл не оновлено", file=sys.stderr)
            return 1
        pages[name] = html
    data = build(pages, site_ids, now)
    if data is None:
        print("[toysi_top] на одній зі сторінок <10 карток (змінилась розмітка?): файл не оновлено", file=sys.stderr)
        return 1
    tmp = out_file.with_name(out_file.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, out_file)
    print("[toysi_top] " + ", ".join(f"{k}: {v['on_site']}/{v['toysi']} на сайті" for k, v in data["counts"].items()) + f" → {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
