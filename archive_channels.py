"""archive_channels.py — auto-archiver каналів агентів: виносить СТАРІ записи з гарячих каналів
у `archive/<роль>/`, тримаючи гарячий файл лінивим (свіже + відкрите).

ЧОМУ (пряме зауваження власника 2026-09-01): дисципліна архівації СЛИПНУЛА — теки archive/<роль>/
порожні (лише README), а канали розпухли (SEO_CHANNEL 6446 рядків). Я лише дописував, старе не виносив.
Механічний фікс замість «пам'ятати руками»: цей скрипт на розкладі сам архівує (як gate/driftcheck —
enforcement, не пам'ять). Принцип: РОБИТЬ роботу, а не лише свариться (тому auto-archiver, не хук).

ЩО: у кожному каналі записи мають дато-заголовок `## [X → Y] YYYY-MM-DD …` (newest-on-top). Записи,
СТАРІШІ за KEEP_DAYS, ПЕРЕНОСЯТЬСЯ у `archive/<роль>/<YYYY-MM>_channel_archive.md` (append), а гарячий
файл переписується лише зі свіжими. Преамбула (Протокол/Ролі — усе до першого `## [`) ЗАВЖДИ лишається.

БЕЗПЕКА даних: переносить, НЕ видаляє (спершу дописує в архів, тоді переписує гарячий). --apply гейт:
без нього DRY-RUN (лише звіт, скільки і що архівувалося б). Секрети/cost/margin у каналах не фігурують
публічно (Cowork локальний, не git); архів теж локальний.

ЗАПУСК: python archive_channels.py [--apply] [--keep-days N]
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COWORK_DIR = Path(os.environ.get("PLUTUS_COWORK_DIR",
                                 r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
# гарячий канал → роль (тека archive/<роль>/)
CHANNELS = {
    "SEO_CHANNEL.md": "seo",
    "КОДВ_CHANNEL.md": "kodv",
    "MARKETING_CHANNEL.md": "smm",
    "CONSULTANT_CHANNEL.md": "consultant",
}
DEFAULT_KEEP_DAYS = 30
DEFAULT_MAX_ENTRIES = 40   # канал розпухає й від ОБСЯГУ (170 записів за 11 днів), не лише віку
_ENTRY_RE = re.compile(r"^## \[")                     # маркер дато-запису каналу
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _split_channel(text: str):
    """(преамбула, [записи]) — записи це блоки від одного '## [' до наступного."""
    lines = text.splitlines(keepends=True)
    starts = [i for i, ln in enumerate(lines) if _ENTRY_RE.match(ln)]
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


def process_channel(fname: str, role: str, cutoff: str, max_entries: int, apply: bool) -> dict:
    src = COWORK_DIR / fname
    if not src.exists():
        return {"file": fname, "skipped": "нема файлу"}
    text = src.read_text(encoding="utf-8")
    preamble, entries = _split_channel(text)
    # entries — newest-on-top. Архівуємо запис, якщо він ПОЗА останніми max_entries АБО старший за
    # cutoff. Тримаємо гарячим лише свіжі-й-небагато. Запис без дати рахуємо як «свіжий» (не гадаємо),
    # але він теж підпадає під ліміт кількості (позиція).
    keep, old = [], []
    for i, e in enumerate(entries):
        too_many = i >= max_entries
        too_old = bool(e["date"]) and e["date"] < cutoff
        (old if (too_many or too_old) else keep).append(e)
    res = {"file": fname, "entries": len(entries), "keep": len(keep), "archive": len(old)}
    if not old:
        return res
    if apply:
        arch_dir = COWORK_DIR / "archive" / role
        arch_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m")
        arch_file = arch_dir / f"{stamp}_channel_archive.md"
        # 1) СПЕРШУ дописуємо в архів (щоб при збої не втратити)
        header = "" if arch_file.exists() else f"# Архів каналу {fname} (холодна історія)\n\n"
        with arch_file.open("a", encoding="utf-8") as f:
            f.write(header + "".join(e["text"] for e in old))
        # 2) тоді переписуємо гарячий: преамбула + свіжі
        new_hot = preamble + "".join(e["text"] for e in keep)
        src.write_text(new_hot, encoding="utf-8")
        res["archived_to"] = str(arch_file)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto-archiver каналів агентів (старе → archive/<роль>/).")
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
    for fname, role in CHANNELS.items():
        r = process_channel(fname, role, cutoff, args.max_entries, args.apply)
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
