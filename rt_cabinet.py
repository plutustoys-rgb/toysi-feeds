# -*- coding: utf-8 -*-
"""rt_cabinet.py — кабінет партнера RoyalToys (royaltoys.com.ua): замовлення вводяться ЛИШЕ там (API нема). Як і eva_cabinet_scraper: вхід робить ВЛАСНИК сам (пароль/2FA — тільки він), сесія зберігається у storage_state, далі — headless на VPS.

Режими (Playwright; паролі я не вводжу ніколи):
  python rt_cabinet.py --login      # раз, на ПК власника: відкриє вікно, власник логіниться, Enter → .local_secrets/rt_cabinet_state.json (зберігається, лише якщо вхід підтверджено; інакше питає)
  python rt_cabinet.py --explore    # ТІЛЬКИ ЧИТАННЯ: під збереженою сесією обходить навігаційні посилання кабінету (GET) і пише приватний звіт .local_secrets/reports/rt_cabinet_explore_<дата>.md (текст сторінок + поля форм) — щоб побудувати введення замовлення за реальною структурою
  python rt_cabinet.py --keepalive  # оновлює сесію (лише при підтвердженому вході)
Шлях стану: RT_CABINET_STATE_FILE (на VPS — /opt/plutustoys/.secrets/rt_cabinet_state.json). Режим введення замовлення (--order) — ПІСЛЯ вивчення кабінету, окремим PR з аудитом.

ЗАХИСТ --explore (аудит PR #871): (1) мережевий замок: усі запити, крім GET/HEAD, блокуються на рівні контексту (жоден POST/форма/JS-дія не пройде); (2) хост лише royaltoys.com.ua, лише https; (3) відкидаються посилки з небезпечними дієсловами (вихід/видалення/скасування/оплата/повтор/додавання/зміна статусу…), з query-ключами-діями, і `/<числовий id>/<ще сегмент>`; (4) після кожного goto перевіряється фінальний URL (редірект на вихід/вхід = зупинка); (5) стан сесії в explore НЕ пересохраняється;
(6) звіт пишеться у finally (збій однієї сторінки не втрачає решту), права 0600, лежить у .local_secrets/ (поза git). Звіт містить ПІБ/телефони клієнтів — не копіювати в git/канали/публічні місця без редагування."""
import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = Path(os.environ.get("RT_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "rt_cabinet_state.json")))
REPORT_DIR = Path(os.environ.get("RT_EXPLORE_DIR", str(BASE_DIR / ".local_secrets" / "reports")))
HOST_OK = {"royaltoys.com.ua", "www.royaltoys.com.ua"}
AUTH_URL = "https://royaltoys.com.ua/ua/auth/"
HOME_URL = "https://royaltoys.com.ua/ua/"
NAV_TIMEOUT_MS = 60000
MAX_PAGES = 25
# дієслова/дії у шляху (укр/рос/лат, початок слова або сегмент): будь-який збіг → посилання НЕ відкриваємо
_VERB_RE = re.compile(
    r"(?:^|[/_\-.])(?:log(?:out|off)|sign(?:out|-out)|exit|del(?:ete)?|drop|remove|cancel|clear|annul|reset|revoke|unsubscribe|toggle|deactivate|"
    r"add|buy|checkout|create|new|edit|save|update|repeat|reorder|copy|pay|confirm|send|close|status|approve|submit|"
    r"выход|вихід|удал\w*|видал\w*|відміна|отмен\w*|скасув\w*|оплат\w*|повтор\w*|додат\w*|добавить)(?:$|[/_\-.?])", re.I)
_ACTION_KEYS = {"action", "do", "act", "op", "delete", "remove", "cancel", "token", "logout", "exit", "confirm", "pay", "status"}
_ID_SUB_RE = re.compile(r"/\d+/[^/]+")                       # /orders/12/<щось> — дія над конкретним об'єктом
_NAV_HINT_RE = re.compile(r"/(?:my|cabinet|kabinet|profile|account|orders?|zamovl\w*|dropship\w*|partner|balance|payments?|ttn|invoice)(?:/|$)", re.I)


def safe_link(href: str) -> bool:
    """Чиста функція: чи можна відкрити це посилання в режимі читання."""
    try:
        u = urlparse(href)
    except ValueError:
        return False
    if u.scheme != "https" or (u.hostname or "").lower() not in HOST_OK:
        return False
    path = u.path or "/"
    if _VERB_RE.search(path) or _ID_SUB_RE.search(path) or not _NAV_HINT_RE.search(path):
        return False
    q = {k.lower() for k in parse_qs(u.query)}
    if ((q - {"page"}) & _ACTION_KEYS) or any(_VERB_RE.search("/" + k) for k in q if k != "page"):
        return False
    return True


def _norm(href: str) -> str:
    u = urlparse(href)
    page = parse_qs(u.query).get("page", [""])[0]
    return f"{u.scheme}://{u.hostname}{u.path}" + (f"?page={page}" if page.isdigit() else "")


def _write_private(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    os.replace(tmp, path)


def _logged_in(page) -> bool:
    """Лише ПОЗИТИВНІ докази: не на сторінці входу і з'явилось посилання на вихід або на кабінет."""
    if "/auth" in page.url or "login" in page.url:
        return False
    try:
        page.wait_for_selector('a[href*="logout"], a[href*="exit"], a[href*="/my/"]', timeout=10000)
        return True
    except Exception:  # noqa: BLE001
        return False


def _save_state(ctx) -> None:
    import json
    _write_private(STATE_FILE, json.dumps(ctx.storage_state(), ensure_ascii=False))


def login() -> int:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto(AUTH_URL, timeout=NAV_TIMEOUT_MS)
            print("[RtCabinet] Увійдіть у кабінет партнера RoyalToys у вікні, що відкрилось, потім натисніть Enter тут.")
            try:
                input()
            except EOFError:
                print("[RtCabinet] Немає інтерактивного вводу — --login треба запускати вручну в терміналі.", file=sys.stderr)
                return 1
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            if not _logged_in(page):
                print("[RtCabinet] Не бачу ознак входу (посилання на вихід/кабінет).")
                try:
                    ans = input("Зберегти сесію все одно? (y/N): ").strip().lower()
                except EOFError:
                    ans = "n"
                if ans != "y":
                    print("[RtCabinet] Не збережено (існуючий стан не змінено).")
                    return 1
            _save_state(ctx)
        finally:
            browser.close()
    print(f"[RtCabinet] Сесію збережено: {STATE_FILE}")
    return 0


def keepalive() -> int:
    from playwright.sync_api import sync_playwright
    if not STATE_FILE.exists():
        print(f"[RtCabinet] нема сесії ({STATE_FILE}); запусти раз --login", file=sys.stderr)
        return 1
    ok = False
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(storage_state=str(STATE_FILE))
            page = ctx.new_page()
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            ok = _logged_in(page)
            if ok:
                _save_state(ctx)
        except Exception as e:  # noqa: BLE001
            print(f"[RtCabinet] keepalive збій: {type(e).__name__}", file=sys.stderr)
        finally:
            browser.close()
    print("[RtCabinet] keepalive:", "сесію оновлено" if ok else "сесія протухла/збій — потрібен --login")
    return 0 if ok else 1


def explore() -> int:
    from playwright.sync_api import sync_playwright
    if not STATE_FILE.exists():
        print(f"[RtCabinet] нема сесії ({STATE_FILE}); запусти раз --login", file=sys.stderr)
        return 1
    out = REPORT_DIR / f"rt_cabinet_explore_{datetime.now():%Y-%m-%d}.md"
    lines = [f"# Кабінет RoyalToys — розвідка {datetime.now():%Y-%m-%d %H:%M} (лише читання; містить персональні дані — не копіювати назовні)", ""]
    seen, queue, pages, rc = set(), [], 0, 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(storage_state=str(STATE_FILE))
            ctx.route("**/*", lambda r: r.abort() if r.request.method not in ("GET", "HEAD") else r.continue_())     # мережевий замок: лише читання
            page = ctx.new_page()
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            if not _logged_in(page):
                print("[RtCabinet] сесія не прийнята — потрібен --login", file=sys.stderr)
                return 1
            links = page.eval_on_selector_all("a[href]", "els => els.map(a => [a.textContent.trim().slice(0,60), a.href])")
            lines += ["## Усі посилання головної (для огляду; відкриваються лише безпечні)", ""] + [f"- {t} → {h}  {'✔' if safe_link(h) else '✖'}" for t, h in dict.fromkeys(map(tuple, links)) if "royaltoys" in h][:150] + [""]
            for _t, h in links:
                if safe_link(h) and _norm(h) not in queue:
                    queue.append(_norm(h))
            while queue and pages < MAX_PAGES:
                url = queue.pop(0)
                if url in seen:
                    continue
                seen.add(url)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                    page.wait_for_timeout(1500)
                    final = page.url
                    if not safe_link(final) and "/auth" not in final:
                        lines += [f"## {url}", f"Редірект на небезпечний/сторонній URL ({final[:100]}) — пропущено", ""]
                        continue
                    if "/auth" in final:
                        lines += [f"## {url}", "Сесія втрачена (редірект на вхід) — зупинка", ""]
                        rc = 1
                        break
                    text = re.sub(r"\n{3,}", "\n\n", (page.inner_text("body") or "").strip())[:6000]
                    forms = page.eval_on_selector_all("form", "fs => fs.map(f => ({action: f.action, method: f.method, fields: [...f.querySelectorAll('input,select,textarea,button')].map(e => [e.tagName, e.type||'', e.name||'', (e.placeholder||e.textContent||'').trim().slice(0,40)])}))")
                    sub = page.eval_on_selector_all("a[href]", "els => els.map(a => a.href)")
                    for h in sub:
                        n = _norm(h) if safe_link(h) else None
                        if n and n not in seen and n not in queue:
                            queue.append(n)
                    pages += 1
                    lines += [f"## {page.title()} — {final}", "", "```", text, "```", ""]
                    for f in forms:
                        lines += [f"Форма {f['method']} {f['action']}:", ""] + [f"- {x}" for x in f["fields"][:60]] + [""]
                except Exception as e:  # noqa: BLE001
                    lines += [f"## {url}", f"Помилка: {type(e).__name__}", ""]
        finally:
            browser.close()
            _write_private(out, "\n".join(lines))                  # звіт пишемо завжди (навіть після збою)
    print(f"[RtCabinet] розвідка записана: {out} ({pages} сторінок)")
    if pages == 0:
        print("[RtCabinet] 0 сторінок: посилання кабінету не знайдено (меню рендериться JS або інша структура) — див. перелік посилань у звіті.", file=sys.stderr)
        return 1
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--login", action="store_true")
    g.add_argument("--explore", action="store_true")
    g.add_argument("--keepalive", action="store_true")
    a = ap.parse_args()
    sys.exit(login() if a.login else explore() if a.explore else keepalive())
