# -*- coding: utf-8 -*-
"""indexnow_ping.py — IndexNow для plutustoys.com.ua (запит SEO 17.11.2026, рішення власника «Усі чотири»): після збірки сайту повідомляє Bing/Yandex/інші пошуковики (api.indexnow.org) про НОВІ й ЗМІНЕНІ сторінки.
Навіщо: Bing-індекс живить Copilot/ChatGPT-пошук; без пінгу нові картки (kv-, brend-*, статті) знаходяться днями.
ЯК ВИЗНАЧАЄМО «ЗМІНЕНО» — не вигадуємо своє: збірка сайту (site/build_site.py) веде `.htlastmod_state.json` {файл: {"h": sha1 відрендереного HTML БЕЗ плаваючих полів, "t": епоха останньої РЕАЛЬНОЇ зміни}} (там не рахуються priceValidUntil, версії асетів, ротаційні блоки) — той самий стан дає lastmod у sitemap.
Беремо ЛИШЕ адреси з sitemap-ів (indexable: noindex-картки й приховані категорії в sitemap не потрапляють, їх не пінгуємо), у яких t > моменту попереднього УСПІШНОГО пінгу. Перший запуск (нема стану) — лише ЗАПАМ'ЯТОВУЄ «зараз», нічого не шле (не заливаємо 30 тис. сторінок).
Ключ IndexNow публічний за задумом протоколу (файл `site/<ключ>.txt`, який віддає сам сайт); перед пінгом скрипт перевіряє, що https://<host>/<ключ>.txt віддає ключ (інакше не пінгує й мовчить).
Ліміти: ≤MAX_PER_RUN адрес за запуск (нові — першими), ≤MAX_PER_DAY за добу; невідправлене (ліміт, 429/5xx, ключ не віддається) лишається в черзі backlog у стані й іде наступними запусками.
Запуск: indexnow-ping.timer (щодві години о :40, після site-rebuild о :20). Вимкнути: INDEXNOW=0 у .env. Лише GET/POST публічних адрес; жодних секретів, ПІБ, цін."""
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:                              # як parser.py: .env поруч із модулем (VPS: /opt/plutustoys/.env)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:               # pragma: no cover
    pass

HERE = Path(__file__).resolve().parent
HOST = "plutustoys.com.ua"
SITE_URL = f"https://{HOST}"
KEY = "7cbdafdc3b8d0d556892391eaba7b10b"          # публічний ключ IndexNow; файл site/<KEY>.txt з цим вмістом лежить у репо
ENDPOINT = "https://api.indexnow.org/IndexNow"
SITE_DIR = Path(os.environ.get("SITE_DIR", str(HERE / "site")))
STATE_FILE = Path(os.environ.get("INDEXNOW_STATE", str(HERE / "indexnow_state.json")))
LASTMOD_STATE = ".htlastmod_state.json"
MAX_PER_RUN = 1000
MAX_PER_DAY = 9000
MAX_BACKLOG = 50000
BATCH = 500
TIMEOUT = 20
USER_AGENT = "PlutusToys-indexnow/1.0"
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


def _http(method: str, url: str, body: bytes | None = None, headers: dict | None = None) -> tuple:
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):      # HTTPException (IncompleteRead, BadStatusLine) — не OSError (аудит #1698)
        return 0, ""


def read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def sitemap_urls(site_dir: Path) -> list:
    """[(url, файл)] з sitemap.xml-індексу й сегментів у каталозі сайту. Файл — ім'я HTML у site/ (корінь '/' → index.html). Нема sitemap → []."""
    out, seen = [], set()
    try:
        idx = (site_dir / "sitemap.xml").read_text(encoding="utf-8")
    except OSError:
        return []
    segs = [m for m in _LOC_RE.findall(idx) if m.endswith(".xml")] or []
    for loc in segs:
        name = loc.rsplit("/", 1)[-1]
        try:
            txt = (site_dir / name).read_text(encoding="utf-8")
        except OSError:
            continue
        for u in _LOC_RE.findall(txt):
            if u in seen or not u.startswith(SITE_URL + "/") and u != SITE_URL:
                continue
            seen.add(u)
            path = u[len(SITE_URL):].lstrip("/")
            out.append((u, path or "index.html"))
    return out


def changed_urls(urls: list, lastmod: dict, since: float) -> list:
    """[(url, t)] адрес зі стану збірки, де t > since; нові/змінені першими (за спаданням t). Адреса без запису у стані → не пінгуємо (невідомо, коли змінилась)."""
    res = []
    for u, fn in urls:
        ent = lastmod.get(fn)
        if isinstance(ent, dict) and isinstance(ent.get("t"), (int, float)) and ent["t"] > since:
            res.append((u, float(ent["t"])))
    res.sort(key=lambda x: -x[1])
    return res


def key_served(host_url: str = SITE_URL, key: str = KEY, http=_http) -> bool:
    """https://<host>/<key>.txt віддає рівно ключ (вимога IndexNow)."""
    status, body = http("GET", f"{host_url}/{key}.txt")
    return status == 200 and body.strip() == key


def submit(urls: list, http=_http) -> tuple:
    """Шле пачками. → (прийнято, спожито): «спожито» = прийнято + ВІДХИЛЕНІ як некоректні (400/422 і інші 4xx, крім 403/429: повтор тієї самої пачки нічого не дасть — інакше вона блокувала б чергу назавжди).
    Зупиняємось (пачка лишається в черзі) на 429, 5xx, мережевому збої (0) і 403 (ключ не прийнято — нехай спершу з'явиться файл ключа)."""
    accepted = consumed = 0
    for i in range(0, len(urls), BATCH):
        chunk = urls[i:i + BATCH]
        payload = json.dumps({"host": HOST, "key": KEY, "keyLocation": f"{SITE_URL}/{KEY}.txt", "urlList": chunk}).encode("utf-8")
        status, _ = http("POST", ENDPOINT, payload, {"Content-Type": "application/json; charset=utf-8"})
        if status in (200, 202):
            accepted += len(chunk)
            consumed += len(chunk)
            continue
        if status in (0, 403, 429) or status >= 500:
            print(f"[indexnow] пачка {i // BATCH + 1}: HTTP {status} — зупиняюсь, повтор наступним запуском", file=sys.stderr)
            break
        print(f"[indexnow] пачка {i // BATCH + 1}: HTTP {status} — пачку ({len(chunk)} адрес) відхилено як некоректну, пропускаю", file=sys.stderr)
        consumed += len(chunk)
    return accepted, consumed


def run(now: float | None = None, site_dir: Path = SITE_DIR, state_file: Path = STATE_FILE, http=_http) -> dict:
    """→ {"status": "...", "sent": N, "backlog": M}. Чиста за винятком http/диска; тестується з підміною.
    Стан {last_ts, day, day_count, backlog[]}: нові/змінені адреси (t > last_ts) додаються в чергу (нові першими), за запуск шлемо ≤MAX_PER_RUN (і ≤ денного залишку), НЕВІДПРАВЛЕНЕ лишається в backlog — нічого не губиться
    ні за лімітом, ні за збою (429/5xx), а last_ts просувається до «зараз» одразу, тож адреси не рахуються двічі."""
    now = time.time() if now is None else now
    if os.environ.get("INDEXNOW", "1").strip() == "0":
        return {"status": "disabled", "sent": 0, "backlog": 0}
    day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    state = read_json(state_file, None)
    if not isinstance(state, dict) or not isinstance(state.get("last_ts"), (int, float)):
        _write_state(state_file, {"last_ts": _safe_ts(now, site_dir), "day": day, "day_count": 0, "backlog": []})
        return {"status": "baseline", "sent": 0, "backlog": 0}           # перший запуск: запам'ятали «зараз», нічого не шлемо
    day_count = int(state.get("day_count") or 0) if state.get("day") == day else 0
    urls = sitemap_urls(site_dir)
    if not urls:
        return {"status": "no_sitemap", "sent": 0, "backlog": len(state.get("backlog") or [])}
    safe = _safe_ts(now, site_dir)         # ДО читання стану збірки: flush збірки в проміжку (HTTP-фаза пінгу) не посуне межу повз ще не прочитані зміни (аудит #1698, повторний)
    lastmod = read_json(site_dir / LASTMOD_STATE, {})
    if not isinstance(lastmod, dict) or not lastmod:
        return {"status": "no_lastmod_state", "sent": 0, "backlog": len(state.get("backlog") or [])}
    live = {u for u, _f in urls}
    old = [u for u in (state.get("backlog") or []) if isinstance(u, str) and u in live]       # адреси, що зникли з sitemap, з черги випадають
    old_set = set(old)
    fresh = [u for u, _t in changed_urls(urls, lastmod, float(state["last_ts"])) if u not in old_set]
    queue = (fresh + old)[:MAX_BACKLOG]                                                          # нові — першими
    room = max(0, MAX_PER_DAY - day_count)
    sent = 0
    status = "nothing"
    if queue and room:
        batch = queue[:min(MAX_PER_RUN, room)]
        if not key_served(http=http):
            print(f"[indexnow] {SITE_URL}/{KEY}.txt не віддає ключ — не пінгуємо (файл site/{KEY}.txt ще не на сайті?)", file=sys.stderr)
            status = "key_not_served"
        else:
            sent, consumed = submit(batch, http=http)
            status = "ok" if consumed == len(batch) else "partial"
            queue = queue[consumed:]
    elif queue:
        status = "daily_cap"
    _write_state(state_file, {"last_ts": safe, "day": day, "day_count": day_count + sent, "backlog": queue})
    return {"status": status, "sent": sent, "backlog": len(queue)}


def _safe_ts(now: float, site_dir: Path) -> float:
    """Межа «що вже оброблено» = min(зараз, mtime стану збірки .htlastmod_state.json). Збірка пише sitemap-и РАНІШЕ за стан (стан — у самому кінці): якщо пінг потрапив між цими записами, нові сторінки є в sitemap, але ще без запису у стані; з last_ts=now їхні t
    (< now) ніколи б не пройшли t > last_ts. Нові зміни мають t, пізніший за mtime ПОПЕРЕДНЬОЇ записаної версії стану, тож з цією межею нічого не губиться (аудит #1698)."""
    try:
        return min(float(now), float((Path(site_dir) / LASTMOD_STATE).stat().st_mtime))
    except OSError:
        return float(now)


def _write_state(path: Path, data: dict) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


if __name__ == "__main__":
    res = run()
    print(f"[indexnow] {res['status']}: відправлено {res['sent']}, у черзі {res['backlog']}")
    sys.exit(0)
