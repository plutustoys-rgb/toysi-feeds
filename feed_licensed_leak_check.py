#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""feed_licensed_leak_check.py — МІРА ПРОТІКАННЯ ліцензійних марок в ОПУБЛІКОВАНИХ фідах Google/Meta/Bing (лише читання; очікуване 0).

ЧОМУ: гейт (generate_google_feed.build_feed_items + feed_licensed_marks.json) рахує ВИЛУЧЕНЕ («пропущено за переліком: 1 146»). Це число росте, коли гейт
працює добре, і НЕ показує дірку в самому переліку: 27.10.2026 SEO знайшла 46 позицій франшиз (Frozen «Крижане серце», Naruto, Transformers, Brawl Stars,
Squid Game, Hot Wheels…) у опублікованому фіді, бо цілих франшиз у переліку не було. Міра, що показує дірку, протилежна: збіги в ОПУБЛІКОВАНОМУ фіді.
ЩО РОБИТЬ: бере з feed-data (raw) google_merchant_feed.xml / meta_feed.xml / bing_feed.xml і шукає у `<title>` кожної позиції (1) форми гейта
(feed_licensed_marks.json, ті самі групи за `_контракт_для_коду.застосовувати`) і (2) незалежну пробу (feed_licensed_probe.json). Збіг гейта = фід
опублікований повз гейт (старий файл з restore-фолбека, FEED_EXCLUDE_LICENSED=0, збій). Збіг лише проби = франшиза відома, а в переліку гейта її нема (дірка).
Рядок на запуск у reports/feed_licensed_leak.csv. Збігів >0 → запис `## [Автомат → Код] Фід-протікання: … <дата> [відбиток]` угорі EXECUTOR_CHANNEL.md
(один запис на добу на ОДНАКОВИЙ набір позицій; Telegram власнику НЕ шле). Факти, не пороги: рішення про внесення форм — SEO/Консультант, про вилучення — власник.
Недоступний фід (мережа/не-XML) → рядок CSV з приміткою, у канал НЕ пишемо (збій мережі десктопа не є протіканням).
Запускається кроком у run_marketplace_actions.py (таск PlutusToys_MarketplaceActions, кожні 6 год) → відставання від публікації ≤ 6 год + кеш raw 5 хв."""
import csv
import hashlib
import io
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
FEED_BASE = os.environ.get("LEAK_CHECK_FEED_BASE", "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/")
FEEDS = ("google_merchant_feed", "meta_feed", "bing_feed")
MARKS_FILE = Path(os.environ.get("FEED_LICENSED_MARKS_FILE") or (BASE_DIR / "feed_licensed_marks.json"))
PROBE_FILE = Path(os.environ.get("FEED_LICENSED_PROBE_FILE") or (BASE_DIR / "feed_licensed_probe.json"))
DEFAULT_REPORT_DIR = r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya\reports"
REPORT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or DEFAULT_REPORT_DIR)
CSV_FILE = REPORT_DIR / "feed_licensed_leak.csv"
CHANNEL_FILE = Path(os.environ.get("FEED_LEAK_CHANNEL_FILE") or (REPORT_DIR.parent / "EXECUTOR_CHANNEL.md"))
MARK = "[Автомат → Код] Фід-протікання:"
SHOW = 10   # скільки назв показати в записі каналу (повний перелік — у reports/feed_licensed_leak_<дата>.txt)
_NS_G = "{http://base.google.com/ns/1.0}"


def load_gate_regex(path=None):
    """Те саме, що generate_google_feed.load_licensed_marks_regex (строго за `_контракт_для_коду.застосовувати`); дубль навмисний: той модуль тягне
    парсер Toysi, Prom-клієнт тощо, а перевірка йде на десктопі. Тотожність патернів стереже test_feed_licensed_leak_check.py."""
    data = json.loads(Path(path or MARKS_FILE).read_text(encoding="utf-8-sig"))
    contract = data.get("_контракт_для_коду")
    forms = []
    if isinstance(contract, dict) and contract.get("застосовувати"):
        for g in contract["застосовувати"]:
            if str(g).startswith("_") or not isinstance(data.get(g), list):
                raise ValueError(f"група {g!r} з контракту відсутня або не список")
            forms += [x for x in data[g] if isinstance(x, str) and x.strip()]
    else:
        for key, val in data.items():
            if not str(key).startswith("_") and isinstance(val, list):
                forms += [x for x in val if isinstance(x, str) and x.strip()]
    if not forms:
        raise ValueError("жодної форми")
    return re.compile("|".join(f"(?:{f})" for f in forms), re.IGNORECASE)


def load_probe_regex(path=None):
    data = json.loads(Path(path or PROBE_FILE).read_text(encoding="utf-8-sig"))
    forms = [x for x in data.get("проба", []) if isinstance(x, str) and x.strip()]
    if not forms:
        raise ValueError("проба порожня")
    return re.compile("|".join(f"(?:{f})" for f in forms), re.IGNORECASE)


def fetch_bytes(url: str, timeout: int = 120):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PlutusToys-leak-check/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read() if r.status == 200 else None
    except Exception:  # noqa: BLE001
        return None


def feed_titles(xml_bytes: bytes) -> dict:
    """{g:id: title} з RSS-фіда; не-XML → ET.ParseError."""
    out = {}
    for _, el in ET.iterparse(io.BytesIO(xml_bytes)):
        if el.tag == "item":
            out[(el.findtext(_NS_G + "id") or "").strip()] = el.findtext("title") or ""
            el.clear()
    return out


def run_check(fetch=fetch_bytes, gate_rx=None, probe_rx=None):
    """(row для CSV, hits {id: (назва, {фіди}, 'gate'|'probe')}, нотатки). hits порожній і note → перевірка неможлива."""
    gate_rx = gate_rx or load_gate_regex()
    probe_rx = probe_rx or load_probe_regex()
    row = {"at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    hits, notes = {}, []
    for name in FEEDS:
        raw = fetch(FEED_BASE + name + ".xml")
        try:
            titles = feed_titles(raw) if raw else None
        except ET.ParseError:
            titles = None
        if titles is None:
            row[name + "_items"], row[name + "_hits"] = "", ""
            notes.append(f"{name}: недоступний або не XML")
            continue
        n = 0
        for pid, title in titles.items():
            g, p = bool(gate_rx.search(title)), bool(probe_rx.search(title))
            if g or p:
                n += 1
                prev = hits.get(pid)
                kind = "gate" if (g or (prev and prev[2] == "gate")) else "probe"
                hits[pid] = (title, (prev[1] if prev else set()) | {name}, kind)
        row[name + "_items"], row[name + "_hits"] = len(titles), n
    row["unique_hits"] = len(hits)
    row["gate_hits"] = sum(1 for v in hits.values() if v[2] == "gate")
    row["probe_only"] = sum(1 for v in hits.values() if v[2] == "probe")
    row["note"] = "; ".join(notes)
    return row, hits, notes


def write_row(row: dict, path: Path | None = None) -> None:
    path = Path(path or CSV_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


def write_full_list(hits: dict, now: datetime, report_dir: Path | None = None) -> Path | None:
    p = Path(report_dir or REPORT_DIR) / f"feed_licensed_leak_{now:%Y-%m-%d}.txt"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{pid}\t{kind}\t{','.join(sorted(feeds))}\t{title}" for pid, (title, feeds, kind) in sorted(hits.items(), key=lambda kv: (kv[1][2], kv[0]))]
        p.write_text("id\tзбіг\tфіди\tназва\n" + "\n".join(lines) + "\n", encoding="utf-8")
        return p
    except OSError:
        return None


def post_to_channel(hits: dict, row: dict, now: datetime, channel: Path | None = None, full_list: Path | None = None) -> bool:
    """Запис угорі каналу (перед першим рядком, що ПОЧИНАЄТЬСЯ з «## »); однаковий набір позицій за добу — один запис; атомарно; канал недоступний → False."""
    channel = Path(channel or CHANNEL_FILE)
    try:
        with open(channel, encoding="utf-8", newline="") as _f:
            s = _f.read()
    except OSError:
        return False
    fp = hashlib.md5("|".join(sorted(hits)).encode("utf-8")).hexdigest()[:6]
    head = f"## {MARK} {len(hits)} позицій {now:%Y-%m-%d} [{fp}]"
    if head in s:
        return False
    gate = [(pid, v[0]) for pid, v in sorted(hits.items()) if v[2] == "gate"]
    probe = [(pid, v[0]) for pid, v in sorted(hits.items()) if v[2] == "probe"]
    body = [head,
            f"У ОПУБЛІКОВАНИХ фідах (feed-data) позицій із ліцензійною франшизою: **{len(hits)}**, очікуване 0. Google {row.get('google_merchant_feed_hits', '?')}, "
            f"Meta {row.get('meta_feed_hits', '?')}, Bing {row.get('bing_feed_hits', '?')} (з {row.get('google_merchant_feed_items', '?')} у Google)."]
    if probe:
        body.append(f"🔴 Лише проба ({len(probe)}): франшиза відома, а в feed_licensed_marks.json її форми нема → дірка в переліку (SEO вичитує форму, Код вносить).")
        body += [f"  - {pid} — {t}" for pid, t in probe[:SHOW]] + ([f"  - … ще {len(probe) - SHOW}"] if len(probe) > SHOW else [])
    if gate:
        body.append(f"🔴 Збіг гейта ({len(gate)}): форма в переліку Є, а позиція у фіді лишилась → фід опублікований повз гейт (старий файл із restore-фолбека, FEED_EXCLUDE_LICENSED=0 або збій генерації).")
        body += [f"  - {pid} — {t}" for pid, t in gate[:SHOW]] + ([f"  - … ще {len(gate) - SHOW}"] if len(gate) > SHOW else [])
    if full_list:
        body.append(f"Повний перелік: {full_list}")
    body.append("Міра, а не поріг: кожні 6 год (MarketplaceActions); Код розбирає, власнику не шле.")
    nl = "\r\n" if "\r\n" in s else "\n"
    entry = ("\n".join(body) + "\n\n").replace("\n", nl)
    m = re.search(r"(?m)^## ", s)
    new = (s[:m.start()] + entry + s[m.start():]) if m else (s + nl + entry)
    try:
        tmp = channel.with_name(channel.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as _f:
            _f.write(new)
        os.replace(tmp, channel)
    except OSError:
        return False
    return True


def main() -> int:
    now = datetime.now()
    row, hits, notes = run_check()
    try:
        write_row(row)
    except OSError as e:
        print(f"[feed_leak] CSV не записано: {type(e).__name__}", file=sys.stderr)
    print(f"[feed_leak] {row['at']}: позицій у фідах {row.get('google_merchant_feed_items', '?')}/{row.get('meta_feed_items', '?')}/{row.get('bing_feed_items', '?')}, "
          f"збігів {len(hits)} (гейт {row['gate_hits']}, лише проба {row['probe_only']}); {row['note'] or 'усі три фіди прочитано'}")
    if hits:
        full = write_full_list(hits, now)
        print("[feed_leak] запис у канал:", "додано" if post_to_channel(hits, row, now, full_list=full) else "за сьогодні вже є / канал недоступний")
    return 0


if __name__ == "__main__":
    sys.exit(main())
