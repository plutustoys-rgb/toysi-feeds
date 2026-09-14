"""archive_channels.py — auto-archiver каналів+журналів: виносить СТАРІ записи з гарячих файлів
у `archive/<роль>/`, тримаючи гарячий файл лінивим (свіже + відкрите).

ЧОМУ (пряме зауваження власника 2026-09-01): дисципліна архівації СЛИПНУЛА — теки archive/<роль>/
порожні (лише README), а канали розпухли (SEO_CHANNEL 6446 рядків). Я лише дописував, старе не виносив.
Механічний фікс замість «пам'ятати руками»: цей скрипт на розкладі сам архівує (як gate/driftcheck —
enforcement, не пам'ять). Принцип: РОБИТЬ роботу, а не лише свариться (тому auto-archiver, не хук).

РОЗШИРЕННЯ (архітектурний аудит 2026-09-14): 4 канали мали архіватор, а `CODE_LOG.md`(репо)/
`STATUS.md`/`OWNER_INBOX.md`(Cowork) — ні, і росли необмежено (STATUS.md — 2327 рядків, CODE_LOG.md
без гальм). Додано ті самі 3 файли в TARGETS з урахуванням їхньої іншої структури:
- `CODE_LOG.md`: заголовок запису БЕЗ дужок (`## YYYY-MM-DD — ...`), не `## [...]`.
- `STATUS.md`: те саме + внизу файлу постійний довідковий "хвіст" (розділи без дати: "Що це за
  проєкт", "Ролі" тощо) — записи БЕЗ дати НІКОЛИ не архівуються (ні за віком, ні за позицією),
  інакше структурний блок піде в архів каналу разом з журналом.
- `OWNER_INBOX.md`: черга РІШЕНЬ власника, формат заголовка як у каналів (`## [Агент] дата — ...`),
  АЛЕ запис архівується, лише якщо він явно ЗАКРИТИЙ (`ЗАКРИТО`/`ЗРОБЛЕНО` у заголовку, і БЕЗ
  `ВІДКРИТО`). Запис без чіткого статусу (немає жодного з трьох слів) вважається ВІДКРИТИМ і НІКОЛИ
  не архівується — приховати відкрите рішення власника від архівації небезпечніше, ніж лишити канал
  трохи довшим. Слова шукаються ПО МЕЖІ СЛОВА (`\b`), не підрядком — інакше гіпотетичне
  «НЕЗАКРИТО»/«ПЕРЕЗАКРИТО» хибно зчиталось би як «ЗАКРИТО» (знайдено незалежним аудитом цього PR).

ПОБІЧНИЙ ЕФЕКТ генералізації (не бага, консервативніше за старе): запис БЕЗ дати тепер ніколи не
рахується в позиційний ліміт (MAX_ENTRIES) для ЖОДНОГО файлу, включно з початковими 4 каналами —
раніше такий запис мовчки підпадав під ліміт за позицією. Наразі в цих 4 каналах записів без дати
немає, тож це не змінює поточну поведінку, лише убезпечує від майбутньої.

ЩО: записи мають дато-заголовок (з дужками або без — див. TARGETS). Записи, СТАРІШІ за KEEP_DAYS АБО
ПОЗА останніми MAX_ENTRIES, ПЕРЕНОСЯТЬСЯ у `archive/<роль>/<YYYY-MM>_archive.md` (append), а гарячий
файл переписується лише зі свіжими. Преамбула (усе до першого запису) ЗАВЖДИ лишається.

БЕЗПЕКА даних: переносить, НЕ видаляє (спершу дописує в архів, тоді переписує гарячий). --apply гейт:
без нього DRY-RUN (лише звіт, скільки і що архівувалося б). Секрети/cost/margin у каналах не фігурують
публічно (Cowork локальний, не git); архів теж локальний. `CODE_LOG.md`(репо) — git-трекований, тому
його архів комітиться звичайним PR-процесом, як і сам скрипт.

ЗАПУСК: python archive_channels.py [--apply] [--keep-days N] [--max-entries N]
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_DIR = Path(__file__).resolve().parent
COWORK_DIR = Path(os.environ.get("PLUTUS_COWORK_DIR",
                                 r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))

_BRACKET_ENTRY_RE = r"^## \["   # канали + OWNER_INBOX: '## [Агент] дата — ...'
_PLAIN_ENTRY_RE = r"^## "       # CODE_LOG/STATUS: '## дата — ...' (без дужок)

# гарячий файл → {тека, роль (archive/<роль>/), формат заголовка, чи вимагати явний "закритий" статус}
TARGETS = {
    "SEO_CHANNEL.md":       {"dir": COWORK_DIR, "role": "seo"},
    "КОДВ_CHANNEL.md":      {"dir": COWORK_DIR, "role": "kodv"},
    "MARKETING_CHANNEL.md": {"dir": COWORK_DIR, "role": "smm"},
    "CONSULTANT_CHANNEL.md":{"dir": COWORK_DIR, "role": "consultant"},
    "STATUS.md":            {"dir": COWORK_DIR, "role": "status", "entry_re": _PLAIN_ENTRY_RE},
    "OWNER_INBOX.md":       {"dir": COWORK_DIR, "role": "owner_inbox", "require_closed": True},
    "CODE_LOG.md":          {"dir": REPO_DIR, "role": "code_log", "entry_re": _PLAIN_ENTRY_RE},
}
DEFAULT_KEEP_DAYS = 30
DEFAULT_MAX_ENTRIES = 40   # файл розпухає й від ОБСЯГУ (170 записів за 11 днів), не лише віку
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_CLOSED_RE = re.compile(r"\b(ЗАКРИТО|ЗРОБЛЕНО)\b")
_OPEN_RE = re.compile(r"\bВІДКРИТО\b")


def _split(text: str, entry_re: str):
    """(преамбула, [записи]) — записи це блоки від одного заголовка-запису до наступного."""
    pat = re.compile(entry_re)
    lines = text.splitlines(keepends=True)
    starts = [i for i, ln in enumerate(lines) if pat.match(ln)]
    if not starts:
        return text, []
    preamble = "".join(lines[:starts[0]])
    entries = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(lines)
        block = "".join(lines[s:e])
        m = _DATE_RE.search(lines[s])          # дата з рядка-заголовка
        entries.append({"date": m.group(1) if m else None, "text": block})
    return preamble, entries


def _is_unambiguously_closed(entry_text: str) -> bool:
    first_line = entry_text.splitlines()[0] if entry_text else ""
    return bool(_CLOSED_RE.search(first_line)) and not _OPEN_RE.search(first_line)


def process_channel(fname: str, cfg: dict, cutoff: str, max_entries: int, apply: bool) -> dict:
    src = cfg["dir"] / fname
    if not src.exists():
        return {"file": fname, "skipped": "нема файлу"}
    text = src.read_text(encoding="utf-8")
    entry_re = cfg.get("entry_re", _BRACKET_ENTRY_RE)
    preamble, entries = _split(text, entry_re)
    require_closed = cfg.get("require_closed", False)
    # entries — newest-on-top. Архівуємо запис, якщо він ПОЗА останніми max_entries АБО старший за
    # cutoff (і, для OWNER_INBOX, ЯВНО закритий). Запис БЕЗ дати — завжди структурний/преамбульний
    # хвіст (напр. довідковий блок унизу STATUS.md), НІКОЛИ не архівується — ні за віком, ні за
    # позицією — і не рахується в ліміт кількості для решти записів.
    keep, old = [], []
    dated_seen = 0
    for e in entries:
        if e["date"] is None:
            keep.append(e)
            continue
        too_many = dated_seen >= max_entries
        too_old = e["date"] < cutoff
        dated_seen += 1
        eligible = too_many or too_old
        if eligible and require_closed and not _is_unambiguously_closed(e["text"]):
            eligible = False   # немає чіткого "ЗАКРИТО/ЗРОБЛЕНО" без "ВІДКРИТО" → вважати відкритим
        (old if eligible else keep).append(e)
    res = {"file": fname, "entries": len(entries), "keep": len(keep), "archive": len(old)}
    if not old:
        return res
    if apply:
        arch_dir = cfg["dir"] / "archive" / cfg["role"]
        arch_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m")
        arch_file = arch_dir / f"{stamp}_archive.md"
        # 1) СПЕРШУ дописуємо в архів (щоб при збої не втратити)
        header = "" if arch_file.exists() else f"# Архів {fname} (холодна історія)\n\n"
        with arch_file.open("a", encoding="utf-8") as f:
            f.write(header + "".join(e["text"] for e in old))
        # 2) тоді переписуємо гарячий: преамбула + свіжі (в оригінальному порядку)
        new_hot = preamble + "".join(e["text"] for e in keep)
        src.write_text(new_hot, encoding="utf-8")
        res["archived_to"] = str(arch_file)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto-archiver каналів+журналів (старе → archive/<роль>/).")
    ap.add_argument("--apply", action="store_true", help="Реально перенести. Без нього — DRY-RUN.")
    ap.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS,
                    help=f"Лишати гарячими записи не старші за N днів (дефолт {DEFAULT_KEEP_DAYS}).")
    ap.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES,
                    help=f"Лишати гарячими не більше N останніх записів (дефолт {DEFAULT_MAX_ENTRIES}).")
    args = ap.parse_args()
    cutoff = (datetime.now() - timedelta(days=args.keep_days)).strftime("%Y-%m-%d")
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[archive] {mode}: гаряче = останні ≤{args.max_entries} записів І не старші за {cutoff} "
          f"({args.keep_days} дн.); решта → архів.")
    total_arch = 0
    for fname, cfg in TARGETS.items():
        r = process_channel(fname, cfg, cutoff, args.max_entries, args.apply)
        if r.get("skipped"):
            print(f"  {fname}: {r['skipped']}")
            continue
        total_arch += r["archive"]
        note = f" → {r['archived_to']}" if r.get("archived_to") else ""
        print(f"  {fname}: записів {r['entries']}, лишаю {r['keep']}, "
              f"{'архівую' if args.apply else 'архівував би'} {r['archive']}{note}")
    print(f"[archive] Разом до архіву: {total_arch} записів. "
          + ("Перенесено." if args.apply else "DRY-RUN — нічого не змінено (додай --apply)."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
