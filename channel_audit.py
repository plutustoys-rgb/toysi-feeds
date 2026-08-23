"""channel_audit.py — дисципліна «Код завжди дописує в канал агента, що для нього зробив».

Показує по КОЖНОМУ каналу агента, ХТО говорив ОСТАННІМ (newest-on-top):
  • якщо останній запис від АГЕНТА до Кода → м'яч у Кода: перевір, чи ти відповів/залогував;
  • якщо останній від Кода → ти відписав останнім (ок).

Не вміє читати думки — лише сигналить «агент говорив останнім, ГЛЯНЬ». Закриття
(«ЗАКРИТО», «ВИКОНАНО», «відкликаю», ✅) помічає окремо, щоб не гнати як гарячий борг.

Правило власника 2026-08-23: «чому не завжди дописуєш у журнал агента… дисципліна хромає,
може є скрипт». Це той скрипт. Гнати на старті/в кінці роботи (як recall.py).

  python channel_audit.py            # стан усіх каналів
  python channel_audit.py --verbose  # + 3 останні записи кожного каналу
"""
import argparse
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COWORK_DIR = Path(os.environ.get(
    "PLUTUS_COWORK_DIR", r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))

# (людська назва, файл, як зветься «Код» у цьому файлі, як зветься агент)
CHANNELS = [
    ("SEO",       COWORK_DIR / "SEO_CHANNEL.md",       "SEO-агент"),
    ("SMM",       COWORK_DIR / "MARKETING_CHANNEL.md", "Маркетолог (SMM)"),
    ("Бухгалтер", COWORK_DIR / "CODE_LOG.md",          "АГЕНТ-БУХГАЛТЕР"),
]

_CODE_NAMES = ("код", "code")  # лівий бік «→» = Код (нижнім регістром)
# Прибрано голий ✅ (над-матчив відкриті запити з галочкою в тексті) і звужено закрив→\bзакрив\b
# (інакше «закривається» = часто «ще чекаю на тебе» хибно рахувалось закриттям) — аудит PR #380,
# щоб число боргу в підсумку не НЕДОрахувалось. Безпечний бік: радше пере-флагнути 🔴, ніж сховати.
_CLOSED_RE = re.compile(r"ЗАКРИТО|ВИКОНАНО|відкликаю|\bзакрив\b|зроблено,?\s*PR\s*#", re.IGNORECASE)
# «## [A → B] …»  або  «## АГЕНТ-БУХГАЛТЕР → КОД, …»  або код-власний «## 2026-.. — …»
_HDR_BRACKET = re.compile(r"^##\s*\[([^\]→]+?)\s*→\s*([^\]]+?)\]\s*(.*)$")
_HDR_ARROW   = re.compile(r"^##\s*([^→\[]+?)\s*→\s*([^,—\n]+?)[,—]\s*(.*)$")


def _classify_header(line: str):
    """→ (author, target, rest) або None, якщо це не заголовок-запис."""
    m = _HDR_BRACKET.match(line) or _HDR_ARROW.match(line)
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    # Код-власний запис у CODE_LOG: «## 2026-08-20 — суть» / «## КОД 2026-…»
    m2 = re.match(r"^##\s*(КОД\b|\d{4}-\d{2}-\d{2})\s*(.*)$", line)
    if m2:
        return "Код", "", m2.group(2).strip()
    return None


def _is_code(name: str) -> bool:
    return name.strip().lower() in _CODE_NAMES or name.strip().lower().startswith("код")


def audit_file(path: Path):
    if not path.exists():
        return {"exists": False}
    entries = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("## "):
            c = _classify_header(line)
            if c:
                entries.append(c + (line[3:].strip(),))
        if len(entries) >= 6:
            break
    if not entries:
        return {"exists": True, "empty": True}
    author, target, rest, raw = entries[0]
    code_last = _is_code(author)
    # «до Кода» — цільова сторона згадує Код (або це стрілочний бух-формат → КОД)
    to_code = (not code_last) and ("код" in (target or "").lower() or "код" in raw.lower())
    closed = bool(_CLOSED_RE.search(raw))
    return {
        "exists": True, "empty": False,
        "last_author": author, "code_last": code_last,
        "to_code": to_code, "closed": closed,
        "newest": raw[:110], "entries": entries,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verbose", action="store_true", help="показати 3 останні записи кожного каналу")
    a = ap.parse_args()

    print("=== CHANNEL AUDIT — де агент говорив останнім (м'яч у Кода) ===")
    debt = 0
    for label, path, _agent in CHANNELS:
        r = audit_file(path)
        if not r.get("exists"):
            print(f"\n[{label}] ⚠️ файлу нема: {path}")
            continue
        if r.get("empty"):
            print(f"\n[{label}] (порожньо / без записів)")
            continue
        if r["code_last"]:
            flag = "🟢 Код відписав останнім"
        elif r["to_code"] and r["closed"]:
            flag = "⚪ агент говорив останнім, але це ЗАКРИТТЯ/FYI — глянь, чи треба лог"
        elif r["to_code"]:
            flag = "🔴 АГЕНТ ЧЕКАЄ — Код НЕ відповів у канал"
            debt += 1
        else:
            flag = f"⚪ останній автор: {r['last_author']} — глянь"
        print(f"\n[{label}] {flag}")
        print(f"    останнє: «{r['newest']}»")
        if a.verbose:
            for author, target, rest, raw in r["entries"][:3]:
                print(f"      · {raw[:100]}")
    print(f"\n--- ПІДСУМОК: гарячого боргу (агент чекає, Код мовчить): {debt} ---")
    if debt:
        print("    → допиши в канал агента, що зроблено/вирішено, ПЕРШ ніж рухати далі.")


if __name__ == "__main__":
    main()
