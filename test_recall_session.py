#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_recall_session.py — регрес відновлення ролі після ущільнення (recall.py: save_state / session_context).

Скарга власника 22.10: після стискання контексту агент не знає ні ролі, ні на чому зупинився.
ІНВАРІАНТИ:
  - save_state (PreCompact) пише останні запити/відповідь/дії у <роль>.md; секрети маскуються;
    системні вставки (<system-reminder…>) і tool_result не потрапляють у «запити»;
  - session_context (SessionStart) називає роль, віддає збережений стан + картку каналів + ядро правил;
  - ≤3000 символів, ядро правил не втрачається навіть при великому стані;
  - роль, знайдена за текстом, кешується за id сесії (ім'я .jsonl) і переживає транскрипт без маркерів;
  - env PLUTUS_ROLE має пріоритет; невідома роль → чесне «не визначена», без падіння;
  - ролі kod/architector/tester існують у _CONFIGS.
Усе в тимчасових теках. `python test_recall_session.py` → exit 0/1.
"""
import json
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import recall

F = []


def chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        F.append(name)


tmp = Path(tempfile.mkdtemp())
recall.ROLE_STATE_DIR = tmp / "rs"
cw = tmp / "cw"
cw.mkdir()
(cw / "SEO_CHANNEL.md").write_text("## [SEO → Код] 2026-10-22 — свіже\n## [Код → SEO] 2026-10-21 — старе\n", encoding="utf-8")
(cw / "OWNER_INBOX.md").write_text("## [SEO] 2026-10-22 — ВІДКРИТО. питання власнику\n", encoding="utf-8")
recall.COWORK_DIR = cw
recall._CONFIGS["seo"] = [cw / "SEO_CHANNEL.md", cw / "OWNER_INBOX.md"]


def line(**o):
    return json.dumps(o, ensure_ascii=False)


def make_transcript(name, first, extra=()):
    p = tmp / name
    rows = [line(type="user", message={"content": first}),
            line(type="assistant", message={"content": [
                {"type": "text", "text": "Роблю PR. api_key=SUPERSECRETVALUE123 не показувати"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "gh pr create --title fix"}}]}),
            line(type="user", message={"content": "<system-reminder>службова вставка</system-reminder>"}),
            line(type="user", message={"content": [{"type": "tool_result", "content": "вивід"}]}),
            line(type="user", message={"content": "дороби аудит PR 961 і замерджи"})]
    rows += list(extra)
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return str(p)


# --- ролі
for r in ("kod", "architector", "tester"):
    chk(f"_CONFIGS має роль {r}", r in recall._CONFIGS)

# --- save_state
tp = make_transcript("sess1.jsonl", "Ти — агент-SEO PlutusToys (plutus-seo). Працюй.")
path = recall.save_state({"transcript_path": tp, "trigger": "auto"})
chk("save_state пише <роль>.md", path.endswith("seo.md") and Path(path).exists())
txt = Path(path).read_text(encoding="utf-8")
chk("стан має останній запит власника", "дороби аудит PR 961" in txt)
chk("стан має останню дію інструмента", "Bash: gh pr create" in txt)
chk("секрет замасковано", "SUPERSECRETVALUE123" not in txt and "[приховано]" in txt)
chk("системна вставка не потрапила в запити", "службова вставка" not in txt)

# --- session_context
ctx = recall.session_context({"transcript_path": tp, "source": "compact"})
chk("контекст називає роль", "«seo»" in ctx)
chk("контекст має збережений стан", "дороби аудит PR 961" in ctx)
chk("контекст має картку каналів", "SEO_CHANNEL.md" in ctx and "питання власнику" in ctx)
chk("контекст має ядро правил", "ЯДРО ПРАВИЛ" in ctx)
chk("≤3000 символів", len(ctx) <= 3000)

# --- кеш ролі: транскрипт без маркерів, але сесія вже відома
tp2 = make_transcript("sess2.jsonl", "Привіт, продовжимо.")
chk("без маркерів і без кешу — роль порожня", recall.detect_role(tp2) == "")
recall.set_role(tp2, "kodv")
chk("після set_role роль береться з кешу", recall.detect_role(tp2) == "kodv")
tp3 = make_transcript("sess3.jsonl", "Ти — головний бухгалтер PlutusToys")
recall.detect_role(tp3)
Path(tp3).write_text(line(type="user", message={"content": "вже без маркерів"}) + "\n", encoding="utf-8")
chk("знайдена за текстом роль переживає транскрипт без маркерів", recall.detect_role(tp3) == "kodv")

# --- великий стан не витісняє ядро правил
(recall.ROLE_STATE_DIR / "seo.md").write_text("дуже довго\n" * 600, encoding="utf-8")
big = recall.session_context({"transcript_path": tp, "source": "resume"})
chk("великий стан: ≤3000 і ядро правил на місці", len(big) <= 3000 and "ЯДРО ПРАВИЛ" in big)

# --- невідома роль та порожній транскрипт не падають
unk = recall.session_context({"transcript_path": str(tmp / "nope.jsonl"), "source": "startup"})
chk("невідома роль: чесне повідомлення + ядро правил", "НЕ визначена" in unk and "ЯДРО ПРАВИЛ" in unk)
chk("save_state без транскрипта → порожньо, без падіння", recall.save_state({"transcript_path": str(tmp / 'nope.jsonl')}) == "")

# --- env пріоритетніший
import os
os.environ["PLUTUS_ROLE"] = "tester"
chk("PLUTUS_ROLE перекриває детекцію", recall.detect_role(tp) == "tester")
del os.environ["PLUTUS_ROLE"]

sys.exit(1 if F else 0)
