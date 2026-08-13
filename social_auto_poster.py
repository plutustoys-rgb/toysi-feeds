"""social_auto_poster.py — автопостинг товарів на Facebook-Сторінку (Graph API).

Задача Cowork (STATUS.md найновіше-21/22, 2026-08-13). Власник обрав: касуальний соцмережевий
тон (як пілот Cowork), СПЕРШУ лише Facebook-Сторінка (Instagram — окремим PR, коли Meta схвалить
`instagram_content_publish`; тут не чіпаємо).

ДЖЕРЕЛО ТОВАРІВ — `feeds/meta_feed.xml` (уже згенерований Facebook/Meta-каталог): 123 ЖИВИХ
товари з коректними `<g:link>` (справжній URL сторінки зі slug — не вгадуємо), `<g:image_link>`
на `images.prom.ua` (Cowork з'ясувала: БЕЗ хотлінк-блокування, на відміну від toysi.ua — можна
брати фото прямо), свіжою ціною й `availability=in stock`. Тобто ні застарілих цін, ні мертвих
товарів, ні вгадування посилань — усе вже відфільтроване тим самим пайплайном, що годує Meta.

ПУБЛІКАЦІЯ — Graph API `POST /{page-id}/photos` (фото + підпис одним викликом; дозвіл
`pages_manage_posts`). СЕКРЕТ: `FB_PAGE_ID` + `FB_PAGE_ACCESS_TOKEN` — ЛИШЕ з оточення
(.env на VPS / .local_secrets), НІКОЛИ з коду/чату/Cowork-папки (той самий принцип, що CAPI/
NovaPay). Без токена — тихий no-op (dry-run усе одно готує пости). Тобто цей файл можна змержити
й деплоїти зараз; жива публікація оживе МОМЕНТ, щойно власник створить Meta-застосунок і покладе
Page Access Token у `.env` — без правок коду.

ГРАМАТИЧНА БЕЗПЕКА (урок «якісну тварини»): назва вживається як факт (не відмінюється); користь
— заздалегідь написані повні речення в називному. БЕЗ вигаданих фактів (вік/безпека/склад) — щоб
не повторити помилку, яку Cowork зловила в ручному пілоті (хибний вік/кількість).

DRY-RUN за замовчуванням: складає готові пости у `social_posts/auto/{sku}/` (caption.txt + фото)
на перегляд власнику. `--publish` реально постить. Ledger не дає постити той самий товар двічі.
"""
import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from dotenv import load_dotenv

from seo_content_generator import CATEGORY_BENEFIT, GENERIC_BENEFIT, _pick

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "").strip()
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "").strip()
GRAPH_VERSION = "v21.0"
REQUEST_TIMEOUT = 30

BASE = Path(__file__).parent
META_FEED = BASE / "feeds" / "meta_feed.xml"
OUT_DIR = BASE / "social_posts" / "auto"
LEDGER = BASE / "social_posted_ledger.json"
NS = {"g": "http://base.google.com/ns/1.0"}

# Касуальні хуки (без змінних → без відмінкових помилок), обираються за hash(sku).
HOOKS = [
    "Нова іграшка вже в наявності! 🎉",
    "Гарний вибір для дитячого дозвілля! 🧸",
    "Час для нових ігор! 🎮",
    "Улюблена іграшка чекає на свого малюка! ✨",
    "Чудова ідея для подарунка! 🎁",
    "Яскрава новинка для дітей! 🌟",
]
# Хештеги за ключем-підрядком назви (ті самі ключі, що CATEGORY_BENEFIT).
HASHTAGS_BY_KEY = {
    "пазл": "#пазли", "конструктор": "#конструктор", "лял": "#ляльки", "пупс": "#пупси",
    "машин": "#машинки", "антистрес": "#антистрес", "сквіш": "#сквіш", "творч": "#творчість",
    "малюванн": "#малювання", "ліпленн": "#ліплення", "тварин": "#фігуркитварин",
    "настільн": "#настільніігри", "м'як": "#мякііграшки", "мяк": "#мякііграшки",
    "ведмед": "#мякііграшки", "музичн": "#музичніграшки", "розвива": "#розвитокдитини",
    "навчальн": "#навчання", "кубик": "#кубики", "фігурк": "#фігурки", "пісок": "#кінетичнийпісок",
    "вод": "#іграшкидляводи", "спорт": "#спортивніігри", "самокат": "#самокат",
}
DEFAULT_HASHTAGS = ["#дитячііграшки", "#іграшкиукраїна", "#plutustoys"]


def _clean_name(name: str) -> str:
    """Прибирає внутрішній мотлох із назви Toysi для соцпосту: '#374' (внутр. артикул),
    '(10/100)'/'(12/600)' (кількість у коробі). Кольори/розміри в дужках (не цифра/цифра)
    лишаються. Схлопує пробіли. Назву як факт НЕ перекручуємо — лише службові токени."""
    n = re.sub(r"<[^>]+>", " ", name or "")   # захист: жодних HTML-тегів у підписі
    n = re.sub(r"#\d+", "", n)
    n = re.sub(r"\(\s*\d+\s*/\s*\d+\s*\)", "", n)
    return re.sub(r"\s+", " ", n).strip()


def _url_alive(url: str, cache: dict) -> bool:
    """GET із трасуванням редіректів → True лише на фінальному 200. Лінк-кеш власного сайту
    (own_product_links_cache) буває застарілим → мертві 404 (реально зустрілось). Не постимо
    битих посилань. Мережна помилка → вважаємо мертвим (fail-safe: краще пропустити, ніж
    опублікувати биту URL). Результат кешується в межах прогону."""
    if url in cache:
        return cache[url]
    try:
        ok = requests.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True).status_code == 200
    except requests.RequestException:
        ok = False
    cache[url] = ok
    return ok


def _match_key(name: str):
    n = (name or "").lower()
    for key in CATEGORY_BENEFIT:
        if key in n:
            return key
    return None


def _benefit(name: str, sku: str) -> str:
    key = _match_key(name)
    return _pick(CATEGORY_BENEFIT[key], sku, "b") if key else _pick(GENERIC_BENEFIT, sku, "b")


def _hashtags(name: str) -> str:
    key = _match_key(name)
    tags = ([HASHTAGS_BY_KEY[key]] if key and HASHTAGS_BY_KEY.get(key) else []) + DEFAULT_HASHTAGS
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return " ".join(out[:4])


def load_products() -> list:
    """Товари з meta_feed.xml (уже живі/in-stock, з коректним URL і фото). Порядок фіду
    зберігаємо — він відображає пріоритет пайплайна."""
    if not META_FEED.exists():
        print(f"[social] нема {META_FEED} — спершу згенеруй Meta-фід.", file=sys.stderr)
        return []
    try:
        root = ET.parse(META_FEED).getroot()
    except ET.ParseError as e:   # битий/обрізаний фід не має валити прогін (симетрично з ledger)
        print(f"[social] {META_FEED} биті XML ({e}) — пропускаю прогін.", file=sys.stderr)
        return []
    out = []
    for item in root.iter("item"):
        g = lambda t: item.findtext("g:" + t, namespaces=NS)
        sku = (g("id") or item.findtext("id") or "").strip()
        name = _clean_name(item.findtext("title") or g("title") or "")
        url = (g("link") or item.findtext("link") or "").strip()
        image = (g("image_link") or "").strip()
        price = (g("price") or "").strip()
        avail = (g("availability") or "").strip().lower()
        if not (sku and name and url and image):
            continue
        if avail and avail != "in stock":
            continue
        out.append({"sku": sku, "name": name, "url": url, "image": image, "price": price})
    return out


def _price_grn(raw: str) -> str:
    """'518.02 UAH' -> '518 грн.'; порожнє/невідоме -> ''."""
    try:
        return f"{round(float((raw or '').split()[0]))} грн."
    except (ValueError, IndexError):
        return ""


def build_caption(p: dict) -> str:
    sku = p["sku"]
    lines = [_pick(HOOKS, sku, "h"), p["name"] + ".", _benefit(p["name"], sku)]
    price = _price_grn(p.get("price"))
    if price:
        lines.append(f"Ціна: {price}")
    lines.append(p["url"])
    lines.append(_hashtags(p["name"]))
    return "\n".join(lines)


def _load_ledger() -> dict:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
    except (OSError, ValueError):
        return {}


def _save_ledger(led: dict) -> None:
    LEDGER.write_text(json.dumps(led, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_dryrun(p: dict, caption: str) -> None:
    d = OUT_DIR / p["sku"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "caption.txt").write_text(caption, encoding="utf-8")
    (d / "info.json").write_text(json.dumps({"sku": p["sku"], "url": p["url"], "image": p["image"]},
                                            ensure_ascii=False, indent=2), encoding="utf-8")
    try:  # images.prom.ua без хотлінк-блокування → можна одразу тягнути на прев'ю власнику
        r = requests.get(p["image"], timeout=REQUEST_TIMEOUT)
        if r.ok and r.content:
            (d / "photo.jpg").write_bytes(r.content)
    except requests.RequestException:
        pass


def _publish_fb(p: dict, caption: str) -> dict:
    """POST /{page-id}/photos — фото на URL + підпис. Повертає {ok, id|error}. Ніколи не кидає."""
    try:
        resp = requests.post(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{FB_PAGE_ID}/photos",
            data={"url": p["image"], "caption": caption, "published": "true",
                  "access_token": FB_PAGE_ACCESS_TOKEN},
            timeout=REQUEST_TIMEOUT,
        )
        j = resp.json() if resp.content else {}
        if resp.ok and (j.get("post_id") or j.get("id")):
            return {"ok": True, "id": j.get("post_id") or j.get("id")}
        return {"ok": False, "error": j.get("error", {}).get("message", f"HTTP {resp.status_code}")}
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "error": str(e)}


def _notify(text: str) -> None:
    try:
        from telegram_notify import send_telegram_message
        send_telegram_message(text)
    except Exception:
        pass


def run(limit: int, publish: bool) -> dict:
    products = load_products()
    if not products:
        return {"posted": 0}
    ledger = _load_ledger()
    live_publish = publish and FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN
    if publish and not live_publish:
        print("[social] --publish задано, але нема FB_PAGE_ID/FB_PAGE_ACCESS_TOKEN у .env — "
              "тихий no-op (готую dry-run пости). Постинг оживе, щойно токен буде в оточенні.",
              file=sys.stderr)

    stats = {"posted": 0, "dryrun": 0, "skipped": 0, "dead_links": 0, "errors": 0}
    url_cache = {}
    for p in products:
        if stats["posted"] + stats["dryrun"] >= limit:
            break
        if p["sku"] in ledger:
            stats["skipped"] += 1
            continue
        if not _url_alive(p["url"], url_cache):   # не постимо битих посилань (стейл-кеш → 404)
            stats["dead_links"] += 1
            print(f"[social] пропущено {p['sku']}: мертве посилання {p['url']}", file=sys.stderr)
            continue
        caption = build_caption(p)
        _write_dryrun(p, caption)   # завжди лишаємо артефакт (і як прев'ю, і як лог опублікованого)
        if live_publish:
            res = _publish_fb(p, caption)
            if res["ok"]:
                ledger[p["sku"]] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "post_id": res["id"]}
                _save_ledger(ledger)
                stats["posted"] += 1
                print(f"[social] опубліковано {p['sku']} → {res['id']}")
            else:
                stats["errors"] += 1
                print(f"[social] ПОМИЛКА публікації {p['sku']}: {res['error']}", file=sys.stderr)
                if stats["errors"] >= 3 and stats["posted"] == 0:
                    # 3 поспіль невдачі без жодного успіху → системна проблема (протухлий/
                    # неправильний токен, бан). Зупиняємось, щоб не гатити 100+ мертвих POST у FB.
                    print("[social] 3 невдалі публікації поспіль, 0 успішних — зупиняю прогін "
                          "(ймовірно токен/дозвіл). Перевір FB_PAGE_ACCESS_TOKEN.", file=sys.stderr)
                    _notify("⚠️ Facebook-постер зупинено: 3 невдачі поспіль (токен/дозвіл?).")
                    break
        else:
            stats["dryrun"] += 1

    if live_publish and stats["posted"]:
        _notify(f"📣 Facebook: опубліковано {stats['posted']} пост(ів). Помилок: {stats['errors']}.")
    print(f"[social] posted={stats['posted']} dryrun={stats['dryrun']} "
          f"skipped(вже постили)={stats['skipped']} мертвих_лінків={stats['dead_links']} "
          f"errors={stats['errors']}. Артефакти: {OUT_DIR}")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=3, help="Скільки постів за прогін (дефолт 3).")
    ap.add_argument("--publish", action="store_true",
                    help="Реально постити на FB-Сторінку. Без FB_PAGE_ID/FB_PAGE_ACCESS_TOKEN — no-op.")
    args = ap.parse_args()
    run(limit=args.limit, publish=args.publish)


if __name__ == "__main__":
    main()
