#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — запис у канал АДРЕСАТА: відправник називає лише «кому», канал обирає скрипт.

Навіщо (власник 25.10: «вам так зручно? усім?» → «п.1 так»): канали росли по одному під ролі, і відправники
плутали, хто що читає (24.10: запис Коду поклала не в той канал; Консультант дублював один запис у 4 канали;
Shopify-code писала в чужий). Тепер вибір каналу — код, а не пам'ять.

Правило вибору (одне, перевіряється по recall.py, тобто по тому, що роль РЕАЛЬНО читає після стискання):
  1. кандидати — власні канали адресатів, далі спільні SEO/MARKETING/CONSULTANT (OWNER_INBOX — лише якщо адресат «owner»);
  2. канал придатний, якщо КОЖЕН адресат його читає: це його власний канал, або він є в recall._CONFIGS цієї
     ролі і заголовок проходить recall._header_ok (спільні SEO/MARKETING/CONSULTANT фільтруються за іменем);
  3. беремо перший придатний. Нема спільного — відмова з поясненням (--split пише окремі записи по каналах).
Один запис — один канал. Час — з годинника машини (не з послідовності). Вставка угорі (newest-on-top) перед
першим рядком `## `; CRLF/LF і BOM файлу зберігаються; запис перечитується з диска і звіряється (друк «ЗАПИСАНО»
лише після звірки). Не міняє нічого, окрім файлу-каналу.

    python post.py --from kod --to seo,site --title "Заголовок" --body-file запис.md
    python post.py --from kod --to consultant --title "..." --body-file - < запис.md   (stdin)
    python post.py ... --dry-run     # показати канал і текст, нічого не писати
Тіло беріть з ФАЙЛУ або stdin (heredoc лише з одинарними лапками <<'EOF'): зворотні лапки в тілі інакше
виконує shell.
"""
import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import recall

COWORK = recall.COWORK_DIR

# роль -> (ім'я у заголовку, конфіг recall.py або None, власний канал)
ROLES = {
    "kod": ("Код", "kod", "EXECUTOR_CHANNEL.md"),
    "auditor": ("Аудитор", "executor", "EXECUTOR_CHANNEL.md"),   # Аудитор кабінетів читає конфіг executor
    "consultant": ("Консультант", "consultant", "CONSULTANT_CHANNEL.md"),
    "seo": ("SEO", "seo", "SEO_CHANNEL.md"),
    "copywriter": ("Копірайтер", "copywriter", "COPYWRITER_CHANNEL.md"),
    "site": ("Shopify-code", "site", "SITE_CHANNEL.md"),
    "smm": ("SMM", "smm", "MARKETING_CHANNEL.md"),
    "kodv": ("КОДВ", "kodv", "КОДВ_CHANNEL.md"),
    "seller": ("Продажник", "seller", "SELLER_CHANNEL.md"),
    "owner": ("Власник", None, "OWNER_INBOX.md"),
}
ALIASES = {
    "код": "kod", "code": "kod", "executor": "auditor", "аудитор": "auditor",
    "консультант": "consultant", "business-consultant": "consultant",
    "shopify-code": "site", "shopify": "site", "сайт": "site",
    "копірайтер": "copywriter", "кодв": "kodv", "бухгалтер": "kodv",
    "продажник": "seller", "власник": "owner",
}
# ролі без записуваного каналу: навмисно відмова, а не вгадування
NO_CHANNEL = {
    "tester": "журнал Тестувальника веде лише він (тест_сайту_журнал.md); дефекти сайту — SITE_CHANNEL.md (--to site)",
    "architector": "власного каналу немає; питання — Коду/Консультанту або OWNER_INBOX.md",
}
OWNER_INBOX = "OWNER_INBOX.md"
SHARED_FALLBACK = ["SEO_CHANNEL.md", "MARKETING_CHANNEL.md", "CONSULTANT_CHANNEL.md"]   # якщо власні канали адресатів не збігаються


class PostError(Exception):
    pass


def norm_role(name: str) -> str:
    key = (name or "").strip().lower()
    key = ALIASES.get(key, key)
    if key in NO_CHANNEL:
        raise PostError(f"роль «{name}»: {NO_CHANNEL[key]}")
    if key not in ROLES:
        raise PostError(f"невідома роль «{name}». Відомі: {', '.join(sorted(ROLES))}")
    return key


def parse_roles(csv: str) -> list:
    out = []
    for part in re.split(r"[,;]", csv or ""):
        if part.strip():
            r = norm_role(part)
            if r not in out:
                out.append(r)
    if not out:
        raise PostError("порожній список адресатів (--to)")
    return out


def reads_channel(role: str, channel: str, header: str) -> bool:
    """Чи побачить роль запис із цим заголовком у цьому каналі після стискання (за recall.py)."""
    _name, cfg, own = ROLES[role]
    if channel == own:
        return True
    if channel == OWNER_INBOX:
        return True                      # OWNER_INBOX читають усі ролі, без фільтра
    if cfg is None or cfg not in recall._CONFIGS:
        return False
    names = {p.name for p in recall._CONFIGS[cfg]}
    return channel in names and recall._header_ok(cfg, channel, header)


def make_header(sender: str, recipients: list, title: str, stamp: str) -> str:
    s = ROLES[sender][0]
    to = ", ".join(ROLES[r][0] for r in recipients)
    return f"## [{s} → {to}] {stamp} — {title}"


def pick_channel(recipients: list, header: str) -> str:
    """Один канал, який читають УСІ адресати; інакше PostError."""
    cands = []
    for r in recipients:
        own = ROLES[r][2]
        if own == OWNER_INBOX and "owner" not in recipients:
            continue
        if own not in cands:
            cands.append(own)
    cands += [c for c in SHARED_FALLBACK if c not in cands]
    for c in cands:
        if all(reads_channel(r, c, header) for r in recipients):
            return c
    per = "; ".join(f"{ROLES[r][0]} → {ROLES[r][2]}" for r in recipients)
    raise PostError("нема каналу, який читають усі адресати разом (" + per + "). "
                    "Розбий на окремі записи по адресатах або додай --split.")


def _newline_at(text: str, pos: int) -> str:
    """Закінчення рядка біля місця вставки (файли в Cowork змішані: CRLF/LF)."""
    seg = text[pos:pos + 4000] if pos < len(text) else text[-4000:]
    m = re.search(r"\r?\n", seg)
    if m:
        return m.group(0)
    return "\r\n" if "\r\n" in text else "\n"


def build_entry(header: str, body: str, nl: str) -> str:
    body = body.strip("\r\n")
    body = re.sub(r"\r\n|\r|\n", nl, body)
    return header + nl + nl + body + nl + nl


def insert_entry(text: str, entry_fn) -> tuple:
    """-> (новий_текст, позиція_вставки, база). Вставка перед першим рядком `## `; база = текст до вставки
    (з доданим кінцевим переводом рядка, якщо заголовків не було і файл ним не закінчувався)."""
    m = re.search(r"(?m)^## ", text)
    pos = m.start() if m else len(text)
    nl = _newline_at(text, pos)
    entry = entry_fn(nl)
    if pos == len(text) and text and not text.endswith(("\n", "\r")):
        text += nl
        pos = len(text)
    return text[:pos] + entry + text[pos:], pos, text


class _Lock:
    def __init__(self, path: Path, wait: float = 10.0, stale: float = 60.0):
        self.path, self.wait, self.stale, self.fd = Path(str(path) + ".lock"), wait, stale, None

    def __enter__(self):
        t0 = time.time()
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                if time.time() - t0 > self.wait:
                    raise PostError(f"канал зайнятий (є {self.path.name} > {self.wait:.0f} с) — спробуй ще раз")
                time.sleep(0.2)

    def __exit__(self, *a):
        try:
            if self.fd is not None:
                os.close(self.fd)
            self.path.unlink()
        except OSError:
            pass


def write_entry(channel_path: Path, header: str, body: str) -> int:
    """Пише запис угору файлу і ПЕРЕЧИТУЄ його. -> номер рядка заголовка (1-based). PostError при розбіжності."""
    if not channel_path.exists():
        raise PostError(f"немає файлу каналу {channel_path} (не створюю нових каналів автоматично)")
    with _Lock(channel_path):
        raw = channel_path.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw[3:].decode("utf-8") if bom else raw.decode("utf-8")
        new_text, pos, base = insert_entry(text, lambda nl: build_entry(header, body, nl))
        data = (b"\xef\xbb\xbf" if bom else b"") + new_text.encode("utf-8")
        tmp = channel_path.with_name(channel_path.name + ".post.tmp")
        tmp.write_bytes(data)
        os.replace(str(tmp), str(channel_path))
        back = channel_path.read_bytes()
        if back != data:
            raise PostError("після запису вміст файлу не збігається з очікуваним — перевір канал вручну")
        back_text = back[3:].decode("utf-8") if bom else back.decode("utf-8")
        if back_text[pos:pos + len(header)] != header or back_text[:pos] != base[:pos] or not back_text.endswith(base[pos:]):
            raise PostError("звірка після запису не пройшла: заголовок не на очікуваному місці або решта файлу змінилась")
        return back_text[:pos].count("\n") + 1


def post(sender: str, to_csv: str, title: str, body: str, dry_run: bool = False,
         split: bool = False, now=None) -> list:
    """-> [(канал, рядок_заголовка|None, заголовок)]. Кидає PostError."""
    s = norm_role(sender)
    recipients = parse_roles(to_csv)
    title = re.sub(r"\s+", " ", (title or "")).strip()
    if not title:
        raise PostError("порожній заголовок (--title)")
    if len(title) > 220:
        raise PostError("заголовок задовгий (>220); короткий заголовок, деталі в тілі")
    if not (body or "").strip():
        raise PostError("порожнє тіло запису")
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    header = make_header(s, recipients, title, stamp)
    plan = []
    try:
        plan.append((pick_channel(recipients, header), header))
    except PostError:
        if not split:
            raise
        groups = {}
        for r in recipients:
            groups.setdefault(ROLES[r][2], []).append(r)
        for ch, rs in groups.items():
            plan.append((ch, make_header(s, rs, title, stamp)))
    out = []
    for ch, h in plan:
        if dry_run:
            out.append((ch, None, h))
        else:
            out.append((ch, write_entry(COWORK / ch, h, body), h))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Запис у канал адресата (один запис — один канал).")
    ap.add_argument("--from", dest="sender", required=True, help="хто пише: " + ", ".join(sorted(ROLES)))
    ap.add_argument("--to", required=True, help="адресати через кому")
    ap.add_argument("--title", required=True)
    ap.add_argument("--body-file", help="файл з тілом; «-» або відсутній при stdin = stdin")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--split", action="store_true", help="якщо нема спільного каналу — окремі записи по каналах")
    a = ap.parse_args(argv)
    try:
        if a.body_file and a.body_file != "-":
            body = Path(a.body_file).read_text(encoding="utf-8-sig")
        elif a.body_file == "-" or not sys.stdin.isatty():
            body = sys.stdin.buffer.read().decode("utf-8-sig")
        else:
            raise PostError("тіло не задано: --body-file ФАЙЛ або stdin")
        res = post(a.sender, a.to, a.title, body, a.dry_run, a.split)
    except PostError as e:
        print(f"ВІДМОВА: {e}")
        return 2
    except OSError as e:
        print(f"ПОМИЛКА файлу: {e}")
        return 1
    live = ", ".join(ROLES[r][0] for r in parse_roles(a.to))
    for ch, line, h in res:
        if a.dry_run:
            print(f"[dry-run] канал {ch}\n  {h}")
        else:
            print(f"ЗАПИСАНО у {ch}, рядок {line}: {h}")
    print(f"Далі: живим адресатам ({live}) — SendMessage з ID із ListAgents безпосередньо перед відправкою; канал — журнал, не доставка.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
