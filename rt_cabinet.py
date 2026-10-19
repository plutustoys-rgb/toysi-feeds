# -*- coding: utf-8 -*-
"""rt_cabinet.py — кабінет партнера RoyalToys (royaltoys.com.ua): замовлення вводяться ЛИШЕ там (API нема). Як і eva_cabinet_scraper: вхід робить ВЛАСНИК сам (пароль/2FA — тільки він), сесія зберігається у storage_state, далі — headless на VPS.

Режими (Playwright; паролі я не вводжу ніколи):
  python rt_cabinet.py --login      # раз, на ПК власника: відкриє вікно, власник логіниться, Enter → .local_secrets/rt_cabinet_state.json (зберігається, лише якщо вхід підтверджено; інакше питає)
  python rt_cabinet.py --explore    # ТІЛЬКИ ЧИТАННЯ: під збереженою сесією обходить навігаційні посилання кабінету (GET) і пише приватний звіт .local_secrets/reports/rt_cabinet_explore_<дата>.md (текст сторінок + поля форм) — щоб побудувати введення замовлення за реальною структурою
  python rt_cabinet.py --keepalive  # оновлює сесію (лише при підтвердженому вході)
  python rt_cabinet.py --vps-login  # НА VPS, headless: форма входу (телефон+пароль) заповнюється з RT_CABINET_PHONE / RT_CABINET_PASSWORD у /opt/plutustoys/.env — їх у файл вписує ВЛАСНИК сам (я пароль не бачу й не вводжу); зберігає сесію у RT_CABINET_STATE_FILE.
                                    # Якщо сайт показав капчу — зупиняється з кодом 3 (капчу НЕ обходимо), стан не змінюється. Пароль ніколи не друкується.
Шлях стану: RT_CABINET_STATE_FILE (на VPS — /opt/plutustoys/.secrets/rt_cabinet_state.json). Режим введення замовлення (--order) — ПІСЛЯ вивчення кабінету, окремим PR з аудитом.

ЗАХИСТ --explore (аудит PR #871): (1) мережевий замок: усі запити, крім GET/HEAD, блокуються на рівні контексту (жоден POST/форма/JS-дія не пройде); (2) хост лише royaltoys.com.ua, лише https; (3) відкидаються посилки з небезпечними дієсловами (вихід/видалення/скасування/оплата/повтор/додавання/зміна статусу…), з query-ключами-діями, і `/<числовий id>/<ще сегмент>`; (4) після кожного goto перевіряється фінальний URL (редірект на вихід/вхід = зупинка); (5) стан сесії в explore НЕ пересохраняється;
(6) звіт пишеться у finally (збій однієї сторінки не втрачає решту), права 0600, лежить у .local_secrets/ (поза git). Звіт містить ПІБ/телефони клієнтів — не копіювати в git/канали/публічні місця без редагування."""
import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote, unquote_plus, urljoin

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = Path(os.environ.get("RT_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "rt_cabinet_state.json")))
REPORT_DIR = Path(os.environ.get("RT_EXPLORE_DIR", str(BASE_DIR / ".local_secrets" / "reports")))
HOST_OK = {"royaltoys.com.ua", "www.royaltoys.com.ua"}
AUTH_URL = "https://royaltoys.com.ua/ua/auth/"
EMAIL_AUTH_URL = "https://royaltoys.com.ua/ua/auth/email/"
HOME_URL = "https://royaltoys.com.ua/ua/"
NAV_TIMEOUT_MS = 60000
MAX_PAGES = 25
# дієслова/дії у шляху (укр/рос/лат, початок слова або сегмент): будь-який збіг → посилання НЕ відкриваємо
_STRONG_VERB_RE = re.compile(r"delete|remove|cancel|logout|logoff|signout|sign-out|sign_out|signoff|purge|wipe|erase|kill|quit|oplat|oformit|zakaz\w*at|clear|annul|скасув|видал|удал|отмен|выход|вихід|оплат", re.I)    # підрядок без межі слова (cancelorder, removeitem)
_VERB_RE = re.compile(
    r"(?:^|[/_\-.])(?:log(?:out|off)|sign(?:out|-out)|exit|del(?:ete)?|drop|remove|cancel|clear|annul|reset|revoke|unsubscribe|toggle|deactivate|"
    r"add|buy|checkout|create|new|edit|save|update|repeat|reorder|copy|pay|confirm|send|close|status|approve|submit|disable|activate|restore|archive|return|refund|accept|decline|reject|publish|unlink|subscribe|"
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
    if u.scheme != "https" or (u.hostname or "").lower() not in HOST_OK or u.port not in (None, 443):
        return False
    path = unquote(u.path or "/")
    if _STRONG_VERB_RE.search(path) or _VERB_RE.search(path) or _ID_SUB_RE.search(path) or not _NAV_HINT_RE.search(path):
        return False
    q = {k.lower() for k in parse_qs(u.query, keep_blank_values=True)}      # у query дозволено лише ?page=N: будь-яка інша змінна (logout, mode=…, go=…) → посилання небезпечне
    return not (q - {"page"})


def _norm(href: str) -> str:
    u = urlparse(href)
    page = parse_qs(u.query).get("page", [""])[0]
    return f"{u.scheme}://{u.hostname}{u.path}" + (f"?page={page}" if page.isdigit() else "")


def _write_private(path: Path, data) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.unlink()                      # O_CREAT не виставляє права на вже існуючий файл (.tmp від збою)
    except OSError:
        pass
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    os.replace(tmp, path)


def _logged_in(page) -> bool:
    """Лише ПОЗИТИВНІ докази: не на сторінці входу і з'явилось посилання на вихід або на кабінет."""
    path = urlparse(page.url).path.lower()
    if "/auth" in path or "/login" in path:
        return False
    try:
        # state="attached": посилання на вихід у випадному меню профілю невидиме, але є в DOM
        page.wait_for_selector('a[href*="logout"], a[href*="exit"], a[href*="/my/"]', state="attached", timeout=10000)
    except Exception:  # noqa: BLE001
        return False
    if page.query_selector('a[href*="logout"], a[href*="exit"]'):
        return True
    return page.query_selector('input[type="password"]') is None      # «Мій кабінет» гостю веде на вхід, а на сторінці входу є поле пароля


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


def leaks_secret(secret: str, url: str, post_data=None) -> bool:
    """Чиста функція замка: чи містить запит секрет там, де його не має бути. Перевіряємо query і fragment URL (звідти пароль береться GET-формою) у сирому й декодованому вигляді (браузерне кодування відрізняється від Python quote: `~`→%7E, `*` не кодується), а також тіло запиту — але тіло лише для запитів НА СТОРОННІ хости (на royaltoys.com.ua пароль у тілі POST — легітимний). Порожній секрет → False."""
    if not secret:
        return False
    u = urlparse(url)
    for part in (u.query, u.fragment):
        for form in (part, unquote(part), unquote_plus(part)):
            if secret in form:
                return True
    if post_data:
        raw = post_data if isinstance(post_data, (bytes, bytearray)) else str(post_data).encode("utf-8", "replace")
        txt = raw.decode("utf-8", "replace")
        if secret.encode("utf-8") in raw or secret in txt or secret in unquote_plus(txt):
            return True
    return False


def vps_login() -> int:
    """Вхід без вікна. Власник вписує у .env на VPS RT_CABINET_EMAIL (або RT_CABINET_PHONE) і RT_CABINET_PASSWORD. Капча → код 3, нічого не зберігаємо. ОДНА спроба за запуск. Облікові дані ніколи не потрапляють у вивід, звіт чи стан (storage_state містить лише куки сесії)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")             # явний шлях: python-dotenv без аргументів шукає від каталогу файлу вгору й міг би взяти чужий .env
    except ImportError:
        pass
    email = (os.environ.get("RT_CABINET_EMAIL") or "").strip()
    phone = (os.environ.get("RT_CABINET_PHONE") or "").strip()
    password = os.environ.get("RT_CABINET_PASSWORD") or ""
    kind, login_id, url = ("email", email, EMAIL_AUTH_URL) if email else ("phone", phone, AUTH_URL)
    if not login_id or not password:
        print("[RtCabinet] нема RT_CABINET_EMAIL (або RT_CABINET_PHONE) і RT_CABINET_PASSWORD у середовищі (.env на VPS) — їх вписує власник.", file=sys.stderr)
        return 1
    from playwright.sync_api import sync_playwright
    rc = 1
    stage = "launch"                                   # статична мітка кроку (без даних): показує, ДЕ саме стався збій
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(service_workers="block")
            blocked = {"hit": False}

            def _guard(r):
                try:
                    req = r.request
                    u = urlparse(req.url)
                    body = req.post_data_buffer if (u.hostname or "").lower() not in HOST_OK else None      # bytes: бінарне тіло не ламає обробник (post_data робить строгий decode)
                    if leaks_secret(password, req.url, body):
                        blocked["hit"] = True
                        return r.abort()                                           # пароль у query/фрагменті URL (GET-форма) або у даних запиту на сторонній хост — не відправляємо
                    return r.continue_()
                except Exception:  # noqa: BLE001
                    blocked["hit"] = True                                          # fail-closed: збій перевірки = запит не пропускаємо, і він завжди завершується
                    try:
                        return r.abort()
                    except Exception:  # noqa: BLE001
                        return None
            ctx.route("**/*", _guard)
            page = ctx.new_page()
            stage = "goto-login"
            page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            if (urlparse(page.url).hostname or "").lower() not in HOST_OK:
                print("[RtCabinet] сторінка входу переадресована поза royaltoys.com.ua — зупинка, дані не введено.", file=sys.stderr)
                return 1
            stage = "find-form"
            if kind == "email":
                forms = page.locator("form:has(input[name='email']):has(input[name='password'])")
            else:
                forms = page.locator("form:has(input[name='password']):has(input[name='type'][value='auth'])")
            if forms.count() != 1:
                print(f"[RtCabinet] очікувалась рівно 1 форма входу, знайдено {forms.count()} — зупинка, дані не введено.", file=sys.stderr)
                return 1
            form = forms.first
            method = form.get_attribute("method")
            action = urlparse(urljoin(page.url, form.get_attribute("action") or ""))
            # явний method має бути POST; форма БЕЗ method/action (email-вхід Webasyst відправляє її JS-ом) допускається лише під мережевим замком вище (пароль у URL і сторонні хости блокуються)
            if (method is not None and method.lower() != "post") or action.scheme != "https" or (action.hostname or "").lower() not in HOST_OK or (kind == "phone" and (method or "get").lower() != "post"):
                print("[RtCabinet] форма входу не POST на royaltoys.com.ua по https — зупинка, дані не введено.", file=sys.stderr)
                return 1
            if _captcha_visible(page):
                print("[RtCabinet] капча вже видима до відправлення — вхід не робимо (капчу не обходимо).", file=sys.stderr)
                return 3
            stage = "fill"
            form.locator("input[name='email']" if kind == "email" else "input[name='phone']").fill(login_id, timeout=15000)
            form.locator("input[name='password']").fill(password, timeout=15000)
            stage = "click"
            form.locator("input[type='submit'], button[type='submit'], button:not([type])").first.click(timeout=15000, no_wait_after=True)      # ОДНА спроба, без повторів; не чекаємо навігації (сайт відправляє форму JS-ом)
            stage = "after-click"
            page.wait_for_timeout(4000)
            if blocked["hit"]:
                print("[RtCabinet] мережевий замок заблокував запит із паролем (форма відправляє його в URL або на сторонній хост) — вхід не відбувся, стан не збережено.", file=sys.stderr)
                return 1
            stage = "wait-load"
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:  # noqa: BLE001
                pass                                    # сторінка після JS-відправки могла не перезавантажуватись — це не помилка
            if _captcha_visible(page):
                print("[RtCabinet] сайт показав капчу — вхід зупинено (капчу не обходимо). Зачекайте або увійдіть вручну.", file=sys.stderr)
                return 3
            stage = "goto-home"
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            stage = "check-login"
            if not _logged_in(page):
                print("[RtCabinet] вхід не підтверджено (невірні дані або інша форма) — стан не збережено. Повторно поспіль не запускати.", file=sys.stderr)
                return 1
            _save_state(ctx)
            rc = 0
        except Exception as e:  # noqa: BLE001
            print(f"[RtCabinet] vps-login збій на кроці «{stage}»: {type(e).__name__}", file=sys.stderr)       # лише статична мітка кроку й назва класу; текст винятку не друкуємо (журнал викликів Playwright міг би містити введене)
        finally:
            browser.close()
    if rc == 0:
        print(f"[RtCabinet] Сесію збережено: {STATE_FILE}")
    return rc


def _captcha_visible(page) -> bool:
    """fail-closed: будь-який збій перевірки (напр. «Execution context was destroyed» під час навігації) = вважаємо, що капча є."""
    js = ("() => { const vis = e => !!e && !e.classList.contains('d-none') && e.offsetParent !== null; "
          "return vis(document.querySelector('.auth-form--captcha')) || vis(document.querySelector('.g-recaptcha')) || vis(document.querySelector(\"iframe[src*='recaptcha']\")); }")
    try:
        return bool(page.evaluate(js))
    except Exception:  # noqa: BLE001
        return True


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
            ctx = browser.new_context(storage_state=str(STATE_FILE), service_workers="block")

            def _lock(r):
                req = r.request
                if req.method not in ("GET", "HEAD"):
                    return r.abort()                                        # лише читання: жодного POST/PUT/DELETE
                if req.is_navigation_request() and req.url.rstrip("/") != HOME_URL.rstrip("/") and not safe_link(req.url) and urlparse(req.url).path.rstrip("/") != "/ua/auth":
                    return r.abort()                                        # навіть редірект/GET-навігація на небезпечну адресу не виконується
                return r.continue_()
            ctx.route("**/*", _lock)
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
                    if not safe_link(final) and urlparse(final).path.rstrip("/") != "/ua/auth":
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
    g.add_argument("--vps-login", action="store_true")
    a = ap.parse_args()
    sys.exit(login() if a.login else explore() if a.explore else vps_login() if a.vps_login else keepalive())
