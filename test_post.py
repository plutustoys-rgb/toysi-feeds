#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_post.py — регрес post.py (запис у канал адресата).

ІНВАРІАНТИ:
  - канал обирається за тим, що адресат РЕАЛЬНО читає (recall._CONFIGS + _header_ok), а не за таблицею «в голові»;
  - кілька адресатів: лише канал, який читають усі; нема спільного -> відмова (або --split);
  - tester/architector/невідома роль -> відмова, нічого не пишеться;
  - вставка угорі перед першим `## `, шапка файлу цілою; CRLF/LF і BOM файлу зберігаються; решта файлу байт-у-байт;
  - порожнє тіло/заголовок -> відмова; --dry-run не пише; заблокований канал -> відмова, не повисання.
Усе в тимчасових теках (справжні канали не чіпає). `python test_post.py` -> exit 0/1.
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import post

F = []
NOW = datetime(2026, 10, 25, 9, 5)


def chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        F.append(name)


def refuses(**kw):
    try:
        post.post(now=NOW, **kw)
    except post.PostError:
        return True
    return False


tmp = Path(tempfile.mkdtemp(prefix="post_test_"))
post.COWORK = tmp
CHANNELS = ["EXECUTOR_CHANNEL.md", "CONSULTANT_CHANNEL.md", "SEO_CHANNEL.md", "COPYWRITER_CHANNEL.md",
            "SITE_CHANNEL.md", "MARKETING_CHANNEL.md", "КОДВ_CHANNEL.md", "SELLER_CHANNEL.md", "OWNER_INBOX.md"]


def reset(nl="\r\n", bom=False, shape="header"):
    for c in CHANNELS:
        if shape == "header":     # шапка-коментар + заголовок + старий запис (як EXECUTOR_CHANNEL)
            t = f"<!-- ШАПКА -->{nl}{nl}## [A → B] 2026-10-24 10:00 — старий{nl}{nl}тіло старого{nl}"
        elif shape == "title":    # `# TITLE` + опис + запис (як SITE_CHANNEL/OWNER_INBOX)
            t = f"# {c}{nl}{nl}опис каналу{nl}{nl}## [A → B] 2026-10-24 10:00 — старий{nl}{nl}x{nl}"
        else:                     # без жодного `## `
            t = f"# {c}{nl}опис без записів"
        (tmp / c).write_bytes((b"\xef\xbb\xbf" if bom else b"") + t.encode("utf-8"))


def rd(c):
    return (tmp / c).read_bytes().decode("utf-8-sig")


# --- маршрутизація
chk("kod -> EXECUTOR", post.pick_channel(["kod"], "## [Консультант → Код] x") == "EXECUTOR_CHANNEL.md")
chk("site -> SITE_CHANNEL (не SHOPIFY/MARKETING)", post.pick_channel(["site"], "## [Код → Shopify-code] x") == "SITE_CHANNEL.md")
chk("auditor -> EXECUTOR", post.pick_channel(["auditor"], "## [Код → Аудитор] x") == "EXECUTOR_CHANNEL.md")
chk("smm -> MARKETING", post.pick_channel(["smm"], "## [Код → SMM] x") == "MARKETING_CHANNEL.md")
chk("owner -> OWNER_INBOX", post.pick_channel(["owner"], "## [Код → Власник] x") == "OWNER_INBOX.md")
h = "## [SEO → Копірайтер, Shopify-code] x"
chk("copywriter+site -> SEO_CHANNEL (власні різні; спільний читають обидва за іменем)", post.pick_channel(["copywriter", "site"], h) == "SEO_CHANNEL.md")
chk("copywriter+seo -> SEO_CHANNEL", post.pick_channel(["copywriter", "seo"], "## [Код → Копірайтер, SEO] x") == "SEO_CHANNEL.md")
chk("kod+consultant -> спільний канал, який читають обидва",
    post.pick_channel(["kod", "consultant"], "## [SEO → Код, Консультант] x") in ("EXECUTOR_CHANNEL.md", "CONSULTANT_CHANNEL.md"))
chk("site+kodv -> нема спільного (КОДВ не читає ні SEO/MARKETING/CONSULTANT з іменем сайту) -> відмова", refuses(sender="kod", to_csv="site,kodv", title="t", body="b"))
chk("smm+copywriter: MARKETING читає copywriter лише з іменем у заголовку -> ок",
    post.pick_channel(["smm", "copywriter"], "## [Код → SMM, Копірайтер] x") == "MARKETING_CHANNEL.md")
chk("aliases: shopify-code/Код/сайт", post.norm_role("Shopify-code") == "site" and post.norm_role("Код") == "kod" and post.norm_role("сайт") == "site")
chk("tester -> відмова", refuses(sender="kod", to_csv="tester", title="t", body="b"))
chk("architector -> відмова", refuses(sender="kod", to_csv="architector", title="t", body="b"))
chk("невідома роль -> відмова", refuses(sender="kod", to_csv="vasya", title="t", body="b"))
chk("невідомий відправник -> відмова", refuses(sender="vasya", to_csv="seo", title="t", body="b"))

# --- запис: шапка + CRLF
reset("\r\n", shape="header")
before = rd("SEO_CHANNEL.md")
res = post.post("kod", "seo", "Новий запис", "рядок 1\nрядок 2", now=NOW)
after = rd("SEO_CHANNEL.md")
chk("повертає канал і рядок заголовка", res[0][0] == "SEO_CHANNEL.md" and res[0][1] == 3)
chk("шапка-коментар лишилась першим рядком", after.startswith("<!-- ШАПКА -->\r\n"))
chk("новий запис угорі, перед старим", after.index("— Новий запис") < after.index("— старий"))
chk("мітка часу з годинника (now)", "## [Код → SEO] 2026-10-25 09:05 — Новий запис" in after)
chk("CRLF збережено: жодного голого \\n", "\n" not in after.replace("\r\n", ""))
chk("решта файлу байт-у-байт", after.endswith(before[before.index("## [A → B]"):]))
chk("тіло з \\n перетворено на CRLF", "рядок 1\r\nрядок 2\r\n" in after)
chk("інші канали не чіпано", rd("EXECUTOR_CHANNEL.md").count("Новий запис") == 0)
chk("lock-файл прибрано", not list(tmp.glob("*.lock")) and not list(tmp.glob("*.tmp")))

# --- LF + BOM + `# TITLE`
reset("\n", bom=True, shape="title")
post.post("kod", "site", "Т", "тіло", now=NOW)
raw = (tmp / "SITE_CHANNEL.md").read_bytes()
t = raw.decode("utf-8-sig")
chk("BOM збережено", raw.startswith(b"\xef\xbb\xbf"))
chk("LF збережено (немає \\r)", "\r" not in t)
chk("заголовок `# ...` та опис лишились над записом", t.startswith("# SITE_CHANNEL.md\n\nопис каналу\n\n## [Код → Shopify-code]"))

# --- файл без жодного `## `
reset("\r\n", shape="bare")
post.post("seo", "kod", "Перший", "x", now=NOW)
t = rd("EXECUTOR_CHANNEL.md")
chk("без `## `: запис дописано в кінець, опис цілий", t.startswith("# EXECUTOR_CHANNEL.md\r\nопис без записів\r\n") and "## [SEO → Код]" in t)

# --- відмови нічого не пишуть
reset("\r\n", shape="header")
snap = {c: (tmp / c).read_bytes() for c in CHANNELS}
chk("порожнє тіло -> відмова", refuses(sender="kod", to_csv="seo", title="t", body="  \n "))
chk("порожній заголовок -> відмова", refuses(sender="kod", to_csv="seo", title="  ", body="b"))
chk("задовгий заголовок -> відмова", refuses(sender="kod", to_csv="seo", title="x" * 300, body="b"))
chk("відмови нічого не змінили", snap == {c: (tmp / c).read_bytes() for c in CHANNELS})
r = post.post("kod", "seo", "dry", "b", dry_run=True, now=NOW)
chk("--dry-run не пише і не повертає рядок", r[0][1] is None and snap == {c: (tmp / c).read_bytes() for c in CHANNELS})

# --- --split
r = post.post("kod", "site,kodv", "розділ", "b", split=True, now=NOW)
chk("--split: два канали, окремі заголовки",
    sorted(x[0] for x in r) == ["SITE_CHANNEL.md", "КОДВ_CHANNEL.md"]
    and "→ Shopify-code]" in rd("SITE_CHANNEL.md") and "→ КОДВ]" in rd("КОДВ_CHANNEL.md")
    and "КОДВ]" not in rd("SITE_CHANNEL.md").split("— старий")[0])

# --- відсутній канал не створюється
(tmp / "SELLER_CHANNEL.md").unlink()
chk("нема файлу каналу -> відмова, файл не створено", refuses(sender="kod", to_csv="seller", title="t", body="b")
    and not (tmp / "SELLER_CHANNEL.md").exists())

# --- заблокований канал
reset("\r\n", shape="header")
lock = tmp / "SEO_CHANNEL.md.lock"
lock.write_text("x")
orig = post._Lock.__init__


def fast(self, path, wait=0.6, stale=60.0):
    orig(self, path, wait, stale)


post._Lock.__init__ = fast
chk("свіжий lock -> відмова за таймаутом, а не зависання", refuses(sender="kod", to_csv="seo", title="t", body="b") and lock.exists())
import os
old = lock.stat().st_mtime - 3600
os.utime(lock, (old, old))
chk("протухлий lock (>60 с) знімається, запис проходить",
    post.post("kod", "seo", "після лока", "b", now=NOW)[0][1] == 3 and not lock.exists())
post._Lock.__init__ = orig

# --- CLI
reset("\r\n", shape="header")
bf = tmp / "body.md"
bf.write_text("тіло з `бектиками` і $HOME", encoding="utf-8")
rc = post.main(["--from", "kod", "--to", "consultant", "--title", "CLI", "--body-file", str(bf)])
chk("CLI: код 0, бектики й $ у тілі дослівно", rc == 0 and "тіло з `бектиками` і $HOME" in rd("CONSULTANT_CHANNEL.md"))
chk("CLI: відмова -> код 2", post.main(["--from", "kod", "--to", "tester", "--title", "t", "--body-file", str(bf)]) == 2)

sys.exit(1 if F else 0)
