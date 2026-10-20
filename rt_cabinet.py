# -*- coding: utf-8 -*-
"""rt_cabinet.py — кабінет партнера RoyalToys (royaltoys.com.ua): замовлення вводяться ЛИШЕ там (API нема). Як і eva_cabinet_scraper: вхід робить ВЛАСНИК сам (пароль/2FA — тільки він), сесія зберігається у storage_state, далі — headless на VPS.

Режими (Playwright; паролі я не вводжу ніколи):
  python rt_cabinet.py --login      # раз, на ПК власника: відкриє вікно, власник логіниться, Enter → .local_secrets/rt_cabinet_state.json (зберігається, лише якщо вхід підтверджено; інакше питає)
  python rt_cabinet.py --explore    # ТІЛЬКИ ЧИТАННЯ: під збереженою сесією обходить навігаційні посилання кабінету (GET) і пише приватний звіт .local_secrets/reports/rt_cabinet_explore_<дата>.md (текст сторінок + поля форм) — щоб побудувати введення замовлення за реальною структурою
  python rt_cabinet.py --keepalive  # оновлює сесію (лише при підтвердженому вході)
  python rt_cabinet.py --vps-login  # НА VPS, headless: форма входу (телефон+пароль) заповнюється з RT_CABINET_PHONE / RT_CABINET_PASSWORD у /opt/plutustoys/.env — їх у файл вписує ВЛАСНИК сам (я пароль не бачу й не вводжу); зберігає сесію у RT_CABINET_STATE_FILE.
                                    # Якщо сайт показав капчу — зупиняється з кодом 3 (капчу НЕ обходимо), стан не змінюється. Пароль ніколи не друкується.
  python rt_cabinet.py --order order.json   # ОФОРМЛЕННЯ замовлення «по своїй ТТН» (20.10): кошик → /ua/onestep/ → доставка «По своей ТТН» → ПІБ/телефон/№ТТН/PDF маркування → ЗУПИНКА перед фінальною кнопкою (кнопка НЕ натискається). Результат — JSON у stdout.
  python rt_cabinet.py --order order.json --submit-reserve   # те саме + фінал «Резерв (Прошу не відправляти)» (20.10 підключено; лише з RT_ORDER_SUBMIT=1); «З балансу» НЕ підключено
  python rt_cabinet.py --order order.json --submit-balance   # (НЕ ПІДКЛЮЧЕНО — відмова) було: фінал «З балансу (Прошу відправити)»: ТІЛЬКИ з RT_ORDER_SUBMIT=1 у середовищі, достатнім балансом, суми кошика = очікуваній, без дубля за № ТТН (перший бойовий запуск — під наглядом власника)
Шлях стану: RT_CABINET_STATE_FILE (на VPS — /opt/plutustoys/.secrets/rt_cabinet_state.json). Режим введення замовлення (--order) — ПІСЛЯ вивчення кабінету, окремим PR з аудитом.

ЗАХИСТ --explore (аудит PR #871): (1) мережевий замок: усі запити, крім GET/HEAD, блокуються на рівні контексту (жоден POST/форма/JS-дія не пройде); (2) хост лише royaltoys.com.ua, лише https; (3) відкидаються посилки з небезпечними дієсловами (вихід/видалення/скасування/оплата/повтор/додавання/зміна статусу…), з query-ключами-діями, і `/<числовий id>/<ще сегмент>`; (4) після кожного goto перевіряється фінальний URL (редірект на вихід/вхід = зупинка); (5) стан сесії в explore НЕ пересохраняється;
(6) звіт пишеться у finally (збій однієї сторінки не втрачає решту), права 0600, лежить у .local_secrets/ (поза git). Звіт містить ПІБ/телефони клієнтів — не копіювати в git/канали/публічні місця без редагування."""
import argparse
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote, unquote_plus, urljoin, quote

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
    kind, login_id, url = ("email", email, AUTH_URL) if email else ("phone", phone, AUTH_URL)      # email-форма завантажується віджетом сайту зі сторінки /ua/auth/ (кнопка «Вхід за допомогою EMAIL»)
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
            if kind == "email":
                # Сторінка /ua/auth/email/ напряму НЕ підходить: без ініціалізованого віджета форма — звичайна GET-форма (виміряно 19.10 на VPS: Enter/click відправляють email і пароль у URL; замок це блокує).
                # Через віджет форма відправляється AJAX POST на /ua/auth/login/email/ (пароль у тілі запиту). Посилання віджета відкриваємо JS-кліком (його перекриває fancybox-слайд).
                stage = "open-email-form"
                page.locator("a.auth-form--email-log").first.evaluate("e => e.click()")
                page.wait_for_selector("form:has(input[name='email']):has(input[name='password'])", state="attached", timeout=15000)
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
            stage = "submit-enter"
            # Відправка клавішею Enter у полі пароля (ОДНА спроба, без повторів): форма email-входу лежить у fancybox-слайді, який перекриває кнопку «Увійти» (elementFromPoint = fancybox-slide, виміряно 19.10 на VPS),
            # тому click() не проходить перевірку «елемент приймає події» (TimeoutError на кроці click). Enter ініціює неявну відправку форми (клік по submit-кнопці) без залежності від покриття.
            form.locator("input[name='password']").press("Enter", timeout=15000, no_wait_after=True)
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


# ───────────────────────── оформлення замовлення «по своїй ТТН» ─────────────────────────
ONESTEP_URL = "https://royaltoys.com.ua/ua/onestep/"
CART_URL = "https://royaltoys.com.ua/ua/cart/"
SEARCH_URL = "https://royaltoys.com.ua/ua/search/?query="
SHIPPING_OWN_TTN = "10"                       # shipping_id «По своей ТТН» (виміряно 19.10)
PAYMENT_PREPAID = "Предоплата на карту"       # варіант оплати для клієнта (накладений вимкнено рішенням власника)
MAX_PDF_BYTES = 2 * 1024 * 1024
# allow-list POST: лише СПОСТЕРЕЖЕНІ під час розвідки 19-20.10 (cart/add, cart/delete, onestep/shipping, data/shipping/<id>/warehouse|findstreets, orderaddon; cart/save — для кількості, перевірено в Shop-Script). Нових — лише за фактичним логом.
_ONESTEP_OK_RE = re.compile(r"^(?:/ua)?/(?:onestep/shipping|data/shipping/\d+/(?:warehouse|findstreets)|cart/(?:add|delete|save)|orderaddon)$")


# ФІНАЛ оформлення (20.10, probe кабінету: кнопки type=button .glavs.no «Резерв (Прошу не відправляти)» і .glavs.otp «З балансу (Прошу відправити замовлення)», hidden confirmation=1; менеджер: резерв = «очікує оплату»,
# нічого не відправляється до підтвердження, тримається 2 дні). Реалізовано РЕЗЕРВ (безпечний: ні гроші, ні відправка); «З балансу» НЕ підключено (баланс 0, спершу перший резерв під наглядом власника).
FINAL_MODES = ("reserve",)


def final_implemented(mode: str) -> bool:
    return mode in FINAL_MODES


class OrderError(Exception):
    pass


def validate_order(o: dict) -> dict:
    """Чиста перевірка вхідного замовлення (ніякої мережі). Повертає нормалізовану копію або кидає OrderError."""
    if not isinstance(o, dict):
        raise OrderError("order.json: очікується об'єкт")
    items = o.get("items")
    if not isinstance(items, list) or not items:
        raise OrderError("нема позицій")
    norm = []
    for it in items:
        vc = str((it or {}).get("vendor_code") or "").strip()
        try:
            qty = int((it or {}).get("qty"))
        except (TypeError, ValueError):
            raise OrderError("кількість позиції некоректна") from None
        if not re.fullmatch(r"[0-9A-Za-zА-Яа-яІіЇїЄє._/\- ]{2,40}", vc) or qty < 1 or qty > 100:
            raise OrderError(f"позиція некоректна: {vc!r} × {qty}")
        try:
            cost = float((it or {}).get("cost"))
        except (TypeError, ValueError):
            raise OrderError(f"{vc}: потрібна оптова cost (число)") from None
        if not math.isfinite(cost) or cost <= 0:
            raise OrderError(f"{vc}: cost має бути скінченним числом > 0")
        norm.append({"vendor_code": vc, "qty": qty, "cost": cost})
    if len({n["vendor_code"] for n in norm}) != len(norm):
        raise OrderError("дублі артикулів у позиціях: об'єднайте їх у одну позицію з сумарною кількістю")
    # ПІБ/телефон отримувача: необов'язкові ЛИШЕ без require_recipient (exploration); RoyalToys у реальному замовленні їх вимагає (менеджер 20.10), тож runner передає їх завжди й ставить require_recipient=True (власник 20.10: «ПІБ передавай»).
    name = " ".join(str(o.get("recipient_name") or "").split())
    if name and len(name.split()) < 2:
        raise OrderError("ПІБ отримувача: потрібні щонайменше прізвище й ім'я (або не вказувати)")
    raw_phone = str(o.get("recipient_phone") or "")
    phone = ""
    if raw_phone.strip():
        digits = re.sub(r"\D", "", raw_phone)
        if len(digits) == 10 and digits.startswith("0"):
            digits = "38" + digits
        if not re.fullmatch(r"380\d{9}", digits):
            raise OrderError("телефон отримувача не розпізнано (або не вказувати)")
        phone = "+" + digits
    if o.get("require_recipient") and not (name and phone):
        raise OrderError("ПІБ і телефон отримувача обов'язкові: RoyalToys без них замовлення не пропускає (менеджер 20.10; власник 20.10: «ПІБ передавай»)")
    ttn = re.sub(r"\s", "", str(o.get("ttn_number") or ""))
    if not re.fullmatch(r"\d{14}", ttn):
        raise OrderError("номер ТТН НП має бути 14 цифр")
    pdf = Path(str(o.get("marking_pdf") or ""))
    if not pdf.is_file() or pdf.stat().st_size > MAX_PDF_BYTES or pdf.stat().st_size < 100 or not pdf.read_bytes()[:5].startswith(b"%PDF"):
        raise OrderError("marking_pdf: потрібен існуючий PDF (до 2 МБ)")
    return {"items": norm, "recipient_name": name, "recipient_phone": phone, "ttn_number": ttn, "marking_pdf": str(pdf), "comment": str(o.get("comment") or "")[:300]}


def _balance(txt: str):
    """«Доступно
0.00» (без «грн») → число або None."""
    m = re.search(r"Доступно\s*([\d][\d\s ]*(?:[.,]\d+)?)", txt or "")
    return float(re.sub(r"[\s ]", "", m.group(1)).replace(",", ".")) if m else None


def _money(txt: str):
    m = re.search(r"(\d[\d\s\u00a0]*(?:[.,]\d+)?)\s*грн", txt or "")
    return float(re.sub(r"[\s\u00a0]", "", m.group(1)).replace(",", ".")) if m else None


def _squash(t: str) -> str:
    """Текст без пробілів/переносів/nbsp — для звірки 14-значного № ТТН незалежно від групування в кабінеті."""
    return re.sub(r"\s", "", t or "")


def lock_decision(method: str, host_ok: bool, path: str, armed: bool) -> str:
    """Рішення мережевого локу для запиту: continue | final_pass (озброєний фінал, ОДИН пропуск) | final_blocked | abort. Чиста функція (тестується без браузера)."""
    if method in ("GET", "HEAD"):
        return "continue"
    if host_ok and _ONESTEP_OK_RE.search(path):
        return "continue"
    if host_ok and path.endswith("/onestep"):
        return "final_pass" if armed else "final_blocked"
    return "abort"


def order_flow(order: dict, submit: bool = False, mode: str = "reserve") -> dict:
    """Оформлення в кабінеті RoyalToys. Без submit — зупиняємось перед фіналом. Повертає словник зі станом. Кошик лишається ЧИСТИМ (finally)."""
    from playwright.sync_api import sync_playwright
    order = validate_order(order)
    if submit and os.environ.get("RT_ORDER_SUBMIT") != "1":
        raise OrderError("фінал вимагає RT_ORDER_SUBMIT=1 (свідоме рішення власника)")
    if submit and not final_implemented(mode):
        raise OrderError(f"режим фіналу «{mode}» не підключено (є: {', '.join(FINAL_MODES)})")
    if not STATE_FILE.exists():
        raise OrderError(f"нема сесії {STATE_FILE.name}: --vps-login")
    res = {"ready": False, "submitted": False, "not_submitted_certain": False, "problems": []}
    blocked = {"final": False}
    armed = {"on": False, "passed": 0}        # фінальний POST /onestep дозволяється ЛИШЕ після всіх перевірок, безпосередньо перед кліком по «Резерв»
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(storage_state=str(STATE_FILE), service_workers="block")

            def _lock(r):
                req = r.request
                u = urlparse(req.url)
                d = lock_decision(req.method, (u.hostname or "").lower() in HOST_OK, u.path.rstrip("/"), armed["on"])
                if d == "continue":
                    return r.continue_()
                if d == "final_pass":
                    armed["on"] = False                          # ОДНОРАЗОВО: після першого пропущеного фінального POST лок знову закритий
                    armed["passed"] += 1
                    return r.continue_()
                if d == "final_blocked":
                    blocked["final"] = True                      # фінальний POST поза озброєним вікном блокується ЗАВЖДИ
                return r.abort()
            ctx.route("**/*", _lock)
            page = ctx.new_page()
            page.on("dialog", lambda d: d.accept())
            page.goto(CART_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            if not _logged_in(page):
                raise OrderError("сесія не прийнята — --vps-login")
            res["balance"] = _balance(page.inner_text("body"))

            def cart_empty() -> bool:
                """Позитивна ознака порожнього кошика: залогінені, нема рядків кількості і є напис про порожній кошик."""
                return _logged_in(page) and page.locator("input[name^='quantity[']").count() == 0 and bool(re.search(r"порожн|пусто", page.inner_text("body"), re.I))

            def clear_cart():
                for _ in range(30):
                    page.goto(CART_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                    page.wait_for_timeout(1500)
                    if cart_empty():
                        return
                    rm = page.locator(".cart-item__remove")
                    if rm.count() == 0:
                        raise OrderError("кошик не порожній, але елемента видалення нема (сесія/верстка) — очистіть вручну")
                    rm.first.evaluate("e => e.click()")
                    page.wait_for_timeout(1800)
                raise OrderError("кошик не вдалося очистити")
            if page.locator("input[name^='quantity[']").count() > 0:
                raise OrderError("кошик у кабінеті RoyalToys не порожній (чужі позиції) — очистіть вручну; автоматично не чіпаю")
            try:
                # 1) позиції в кошик (за артикулом: пошук → картка з точним «Арт.: …»)
                for it in order["items"]:
                    page.goto(SEARCH_URL + quote(it["vendor_code"]), wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                    page.wait_for_timeout(1800)
                    links = page.eval_on_selector_all("a[href*='/product/']", "els => [...new Set(els.map(e => e.getAttribute('href')))].slice(0,8)")
                    found = False
                    for href in links:
                        page.goto("https://royaltoys.com.ua" + href, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                        page.wait_for_timeout(1200)
                        if re.search(r"Арт\.?\s*:?\s*" + re.escape(it["vendor_code"]) + r"(?!\w)", page.inner_text("body")):
                            btn = page.locator("button.add-to-cart, button[class*=add], .js-add-to-cart").first
                            if btn.count() == 0:
                                raise OrderError(f"{it['vendor_code']}: кнопки «в кошик» нема (товар недоступний?)")
                            btn.evaluate("e => e.click()")
                            page.wait_for_timeout(2200)
                            found = True
                            break
                    if not found:
                        raise OrderError(f"{it['vendor_code']}: товар за артикулом не знайдено")
                # 2) кількості + звірка кошика
                page.goto(CART_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                page.wait_for_timeout(1800)
                rows = page.eval_on_selector_all("input[name^='quantity[']", "els => els.map(e => [e.name, e.value, (e.closest('[class*=cart-item]') || e.closest('tr') || e.parentElement.parentElement).innerText])")
                want = {it["vendor_code"]: it["qty"] for it in order["items"]}
                for name, val, txt in rows:
                    m = re.search(r"Арт\.?\s*:?\s*([^\s\n]+)", txt)
                    vc = m.group(1) if m else ""
                    if vc in want and int(val or 0) != want[vc]:
                        page.locator("input[name='%s']" % name).first.fill(str(want[vc]))
                        page.locator("input[name='%s']" % name).first.press("Tab")
                        page.wait_for_timeout(2200)
                page.goto(CART_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                page.wait_for_timeout(1800)
                got = {}
                for name, val, txt in page.eval_on_selector_all("input[name^='quantity[']", "els => els.map(e => [e.name, e.value, (e.closest('[class*=cart-item]') || e.closest('tr') || e.parentElement.parentElement).innerText])"):
                    m = re.search(r"Арт\.?\s*:?\s*([^\s\n]+)", txt)
                    got[m.group(1) if m else name] = int(val or 0)
                if got != want:
                    raise OrderError(f"кошик не збігається з замовленням: у кошику {got}, потрібно {want}")
                cart_text = page.inner_text("body")
                res["cart_total"] = _money(cart_text.split("Всього", 1)[-1][:40]) if "Всього" in cart_text else None
                expected = sum(it["cost"] * it["qty"] for it in order["items"])
                if res["cart_total"] is None:
                    raise OrderError("не вдалося прочитати суму кошика — зупинка")
                if abs(expected - res["cart_total"]) > 0.5:
                    raise OrderError(f"сума кошика {res['cart_total']} ≠ очікувана за оптом {expected:.2f} — зупинка (ціна на сайті постачальника змінилась?)")
                res["min_order_note"] = [l.strip() for l in cart_text.splitlines() if "Мінімальн" in l][:2]
                # 3) оформлення «по своїй ТТН»
                page.goto(ONESTEP_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                page.wait_for_timeout(3500)
                page.locator("input[name='shipping_id'][value='%s']" % SHIPPING_OWN_TTN).first.evaluate("e => e.click()")
                page.wait_for_timeout(3000)

                def setf(name, val):
                    page.locator("[name='%s']" % name).first.fill(val, timeout=8000)
                pre = "customer_%s[address.shipping]" % SHIPPING_OWN_TTN
                if order["recipient_name"]:
                    setf(pre + "[fio-poluchately]", order["recipient_name"])
                if order["recipient_phone"]:
                    setf(pre + "[telefon-polucha]", order["recipient_phone"])
                setf(pre + "[nomer-ttn]", order["ttn_number"])
                sel = page.locator("[name='%s[variant-oplaty]']" % pre).first
                sel.evaluate("(e, v) => { const o = [...e.options].find(x => x.value === v); if (!o) throw new Error('no option'); e.value = v; e.dispatchEvent(new Event('change', {bubbles:true})); }", PAYMENT_PREPAID)
                if order["comment"]:
                    page.locator("textarea[name='comment']").first.evaluate("(e, v) => { e.value = v; e.dispatchEvent(new Event('input', {bubbles:true})); e.dispatchEvent(new Event('change', {bubbles:true})); }", order["comment"])
                page.locator("input[type=file][name='addon[]']").first.set_input_files(order["marking_pdf"], timeout=15000)
                page.wait_for_timeout(3500)
                form_text = re.sub(r"\s+", " ", page.eval_on_selector("form.checkout-form", "f => f.innerText"))
                res["pdf_uploaded"] = Path(order["marking_pdf"]).name in form_text
                if not res["pdf_uploaded"]:
                    res["problems"].append("PDF маркування не з'явився в формі після завантаження")
                res["total_on_checkout"] = _money(form_text.split("Всього", 1)[-1][:40]) if "Всього" in form_text else None
                res["checkout_warnings"] = [w for w in page.eval_on_selector_all("[class*=error], [class*=warning], .alert", "els => els.filter(e => e.offsetParent !== null).map(e => e.textContent.trim().slice(0,160))") if w][:5]
                res["balance"] = _balance(form_text) if _balance(form_text) is not None else res.get("balance")
                # ДІАГНОСТИКА (20.10, перегляд результату — одразу після першого dry-run на VPS: з цього пишу фінальні кнопки «Резерв»/«З балансу»): які саме кнопки/поля є у формі оформлення. Лише читання DOM, без значень полів ПІБ/телефону.
                try:
                    res["final_buttons"] = page.eval_on_selector_all("form.checkout-form button, form.checkout-form input[type=submit], form.checkout-form input[name=confirmation]",
                                                                    "els => els.map(e => ({tag: e.tagName, type: e.type || '', name: e.name || '', value: (e.name === 'confirmation' ? String(e.value || '') : '').slice(0, 60), text: (e.innerText || '').trim().slice(0, 80), cls: String(e.className || '').slice(0, 60), form_action: ((e.form && e.form.getAttribute('action')) || '').split('?')[0]}))")
                except Exception:  # noqa: BLE001
                    res["final_buttons"] = None      # None = збій читання (≠ «форма без кнопок» = [])
                if not res["problems"] and not res["checkout_warnings"]:
                    res["ready"] = True
                if submit:
                    res["not_submitted_certain"] = True      # усі наступні raise — ДО натискання; фінальна реалізація МУСИТЬ виставити False безпосередньо ПЕРЕД кліком (runner відкочує крок лише за True)
                    if not res["ready"]:
                        raise OrderError(f"фінал не виконано: {res['problems'] or res['checkout_warnings']}")
                    if res["total_on_checkout"] is None:
                        raise OrderError("не вдалося прочитати суму на оформленні — зупинка")
                    if mode == "balance" and (res["balance"] is None or res["balance"] < res["total_on_checkout"]):
                        raise OrderError(f"недостатньо балансу (доступно {res['balance']}, сума {res['total_on_checkout']})")   # для «Резерв» баланс НЕ потрібен (замовлення просто чекає оплати)
                    def orders_text():
                        """Список замовлень у ОКРЕМІЙ вкладці (форма оформлення в основній не губиться)."""
                        p2 = ctx.new_page()
                        try:
                            p2.goto("https://royaltoys.com.ua/ua/my/orders/", wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                            p2.wait_for_timeout(1500)
                            return p2.inner_text("body")
                        finally:
                            p2.close()
                    if _squash(order["ttn_number"]) in _squash(orders_text()):
                        raise OrderError("замовлення з цим № ТТН уже є в кабінеті — повторно не створюємо")
                    # РЕЗЕРВ. Усі перевірки нижче — ДО озброєння: помилка тут = «точно не відправлено» (чистий відкат кроку)
                    if blocked["final"]:
                        raise OrderError("під час заповнення форми сторінка вже пробувала відправити фінальний POST — зупинка (форма поводиться несподівано)")
                    btn = page.locator("form.checkout-form button.glavs.no")
                    if btn.count() != 1:
                        raise OrderError(f"кнопка «Резерв» не знайдена або неоднозначна ({btn.count()}) — верстка змінилась, зупинка")
                    btn_text = (btn.first.inner_text() or "")
                    if "Резерв" not in btn_text or "балансу" in btn_text:
                        raise OrderError("кнопка за селектором .glavs.no — не «Резерв» (верстка змінилась) — зупинка")
                    # З цього рядка результат може бути невідомим: помилки лишають rt_ordering (автоповтору немає)
                    res["not_submitted_certain"] = False
                    armed["on"] = True
                    try:
                        btn.first.evaluate("e => e.click()")
                        page.wait_for_timeout(6000)
                    finally:
                        armed["on"] = False                      # лок відкритий лише на мить кліку (і при винятку теж)
                    after = orders_text()
                    if _squash(order["ttn_number"]) not in _squash(after):
                        raise OrderError("після кліку «Резерв» замовлення з цим № ТТН у списку не знайдено — результат невідомий, звірте кабінет вручну")
                    # номер замовлення RoyalToys (RT…) найближчий ПЕРЕД ТТН у тексті списку; ТТН показана групами/нема RT — позначаємо за ТТН
                    pos = after.find(order["ttn_number"])
                    window = after[max(0, pos - 600):pos + 300] if pos >= 0 else ""
                    refs = re.findall(r"RT\d{6,}", window)
                    res["rt_order_ref"] = refs[-1] if refs else "ttn-" + order["ttn_number"]
                    res["reserve_status_seen"] = bool(re.search(r"резерв|очіку\w*\s+оплат|очікує", window, re.I))
                    res["submitted"] = True
                    res["mode"] = "reserve"
            finally:
                try:
                    clear_cart()                                     # кошик завжди чистий (не лишаємо чернетку)
                except Exception:  # noqa: BLE001
                    res["problems"].append("кошик не очищено")
        except OrderError as e:
            res["problems"].append(str(e))
        except Exception as e:  # noqa: BLE001
            res["problems"].append(f"збій {type(e).__name__}")        # без тексту винятку: журнал Playwright міг би містити введені значення
        finally:
            browser.close()
    res["final_post_blocked"] = blocked["final"]          # інформаційно: спроба фінального POST (мала б бути 0, бо код фінал не натискає)
    res["ready"] = bool(res["ready"] and not res["problems"] and not blocked["final"])
    return res


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
    g.add_argument("--order", metavar="order.json")
    ap.add_argument("--submit-reserve", action="store_true")      # фінал «Резерв (Прошу не відправляти)»: лише з RT_ORDER_SUBMIT=1 (під наглядом власника)
    ap.add_argument("--submit-balance", action="store_true")      # «З балансу» — НЕ підключено
    a = ap.parse_args()
    if (a.submit_balance or a.submit_reserve) and not a.order:
        ap.error("--submit-* працює лише разом з --order")
    if a.order:
        import json
        try:
            out = order_flow(json.loads(Path(a.order).read_text(encoding="utf-8")), submit=a.submit_balance or a.submit_reserve, mode="balance" if a.submit_balance else "reserve")
        except (OrderError, OSError, ValueError) as e:
            out = {"ready": False, "submitted": False, "problems": [str(e)]}
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(0 if out.get("ready") or out.get("submitted") else 1)
    sys.exit(login() if a.login else explore() if a.explore else vps_login() if a.vps_login else keepalive())
