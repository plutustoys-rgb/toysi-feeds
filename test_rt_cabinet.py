#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rt_cabinet.py — rt_cabinet.safe_link (аудит PR #871): режим читання не відкриває небезпечних посилань. Офлайн, без Playwright."""
import sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import rt_cabinet as rc  # noqa: E402
F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)
B = "https://royaltoys.com.ua"
for ok in ("/ua/my/", "/ua/my/orders/", "/ua/my/orders/?page=2", "/ua/my/orders/12/", "/ua/my/profile/", "/ua/dropshipping/"):
    chk(f"дозволено: {ok}", rc.safe_link(B + ok))
bad = ["/orders/12/del", "/orders/12/drop", "/my/orders/repeat/12", "/my/order/reorder/12", "/my/orders/pay/12", "/my/orders/confirm/12", "/my/orders/12/send", "/my/orders/12/close", "/my/orders/12/status/2",
       "/my/signout", "/my/logout/", "/my/wishlist/add/3", "/my/orders/12/annul", "/my/orders/create", "/my/orders/add", "/order/delete/12", "/cart/add/5", "/ua/auth/", "/my/orders/12/скасувати", "/my/orders/видалити", "/my/orders/отмена",
       "/my/orders/?action=remove", "/ua/my/?logout", "/ua/my/orders/cancelorder/12", "/ua/my/cart/removeitem/3", "/ua/my/orders/%64elete/12", "/ua/my/orders/disable/", "/my/orders/archive/", "/my/orders/return/", "/my/orders/unlink", "/ua/my/orders/?mode=delete", "/ua/my/orders/?go=logout", "/my/orders/?token=abc", "/my/orders/?do=cancel", "/my/orders/?status=2", "/blog/", "/ua/category/igrushki/"]
for b in bad:
    chk(f"заблоковано: {b}", not rc.safe_link(B + b))
chk("чужий хост і підрядок у query: заблоковано", not rc.safe_link("https://evil.tld/?r=royaltoys.com.ua/my/") and not rc.safe_link("https://royaltoys.com.ua.evil.tld/my/") and not rc.safe_link("http://royaltoys.com.ua/my/"))
chk("_norm лишає лише ?page=N", rc._norm(B + "/my/orders/?page=3&sort=x") == B + "/my/orders/?page=3" and rc._norm(B + "/my/orders/?sort=x") == B + "/my/orders/")
import os as _os, io as _io, contextlib as _cl
for k in ("RT_CABINET_EMAIL", "RT_CABINET_PHONE", "RT_CABINET_PASSWORD"):
    _os.environ.pop(k, None)
import dotenv as _dotenv
_dotenv.load_dotenv = lambda *a, **k: False      # тест не має підтягувати реальні RT_CABINET_* з локального .env (інакше запустив би справжній вхід)
_err = _io.StringIO()
with _cl.redirect_stderr(_err):
    _rc = rc.vps_login()
chk("--vps-login без RT_CABINET_PHONE/PASSWORD у середовищі → код 1, браузер не запускається, облікових даних у виводі нема", _rc == 1 and "RT_CABINET_EMAIL" in _err.getvalue())
src = Path(rc.__file__).read_text(encoding="utf-8")
chk("пароль ніколи не друкується: у файлі нема print з password/phone-змінними", not any(("password" in ln or "phone" in ln) and "print(" in ln and "RT_CABINET_" not in ln for ln in src.splitlines()))
B2 = "https://royaltoys.com.ua"
for pw_ in ("x~y", "a*b@c", "pa ss&w=rd", "Zażółć1", "abc123"):
    from urllib.parse import quote as _q, quote_plus as _qp
    chk(f"замок: пароль {pw_!r} у query (сирий/quote/quote_plus) → leaks", rc.leaks_secret(pw_, f"{B2}/ua/auth/email/?password={pw_}") and rc.leaks_secret(pw_, f"{B2}/x?p={_q(pw_, safe='')}") and rc.leaks_secret(pw_, f"{B2}/x?p={_qp(pw_)}"))
chk("замок: браузерне кодування `~`→%7E і `*` сирий (a*b%40c) ловиться через unquote", rc.leaks_secret("x~y", f"{B2}/x?p=x%7Ey") and rc.leaks_secret("a*b@c", f"{B2}/x?p=a*b%40c"))
chk("замок: пароль у fragment → leaks", rc.leaks_secret("secret1", f"{B2}/x#secret1"))
chk("замок: легітимне — короткий пароль «ua»/«auth» збігається зі шляхом, але не з query → НЕ leaks", not rc.leaks_secret("ua", f"{B2}/ua/auth/email/") and not rc.leaks_secret("auth", f"{B2}/ua/auth/email/") and not rc.leaks_secret("royal", f"{B2}/ua/auth/email/?page=2"))
chk("замок: тіло запиту на сторонній хост з паролем → leaks; без тіла/без пароля → ні; порожній секрет → ні", rc.leaks_secret("pw123456", "https://evil.tld/c", "email=a&password=pw123456") and not rc.leaks_secret("pw123456", "https://evil.tld/c", "email=a") and not rc.leaks_secret("", f"{B2}/x?p=1"))
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
