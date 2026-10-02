# -*- coding: utf-8 -*-
"""Регрес recall.py: detect_role (роль із 1-го повідомлення транскрипта) + restore_card (картка стану).
Перші повідомлення — дослівні з реальних сесій 2026-10-01/02. Мережа/репо не потрібні."""
import json, sys, tempfile
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import recall

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

tmp = Path(tempfile.mkdtemp())
def transcript(first, name="t.jsonl"):
    p = tmp / name
    p.write_text(json.dumps({"type": "queue-operation"}) + "\n" +
                 json.dumps({"type": "user", "message": {"content": first}}, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(p)

cases = [
    ("@SELLER_CHANNEL.md привіт! ти агент-продажник. ознайомся.", "seller"),
    ("Ти — агент-виконавець PlutusToys (`plutus-executor`). Спершу прочитай свою повну персону", "executor"),
    ("## [Код → SEO] 2026-08-30 — 👋 Вітаю, новий SEO-агенте. Введення: що читати", "seo"),
    ("ти бізнес консультант. треба нова ідея для цифрового бізнеса.", "consultant"),
    ("Ти — сесія бізнес-консультанта PlutusToys. Ти прокинувся за монітором каналу", "consultant"),
    ("<scheduled-task name=\"kodv-independent-book-audit\" file=\"x\"> This is an automated run", "kodv"),
    ("You are Code-Agent, an elite Autonomous Software Engineering AI Agent.", ""),
    ("Ось черга завдань для Код (повний текст у PROJECT_STATUS.md)", ""),
    ("ти бізнес консультант і агент-продажник", ""),   # нічия → не вгадуємо
]
for i, (first, want) in enumerate(cases):
    chk(f"detect_role[{i}] → {want or 'порожньо'}", recall.detect_role(transcript(first, f"c{i}.jsonl")) == want)
chk("detect_role: файлу нема → порожньо (не падає)", recall.detect_role(str(tmp / "nope.jsonl")) == "")

# restore_card на ізольованому Cowork-каталозі
cw = tmp / "cw"; cw.mkdir()
(cw / "SEO_CHANNEL.md").write_text("# h\n## [SEO → Код] 2026-10-02 — новіше\n## [Код → SEO] 2026-10-01 — старіше\n", encoding="utf-8")
(cw / "OWNER_INBOX.md").write_text(
    "## [SEO] 2026-10-01 — ВІДКРИТО. питання\n## [SEO] 2026-09-30 — ✅ ЗРОБЛЕНО. закрите\n## [Консультант] 2026-10-01 — ВІДКРИТО. чуже\n",
    encoding="utf-8")
recall.COWORK_DIR = cw
recall._CONFIGS["seo"] = [cw / "SEO_CHANNEL.md", cw / "OWNER_INBOX.md"]
card = recall.restore_card("seo")
chk("restore: заголовки каналу з датами, newest-on-top", card.index("новіше") < card.index("старіше"))
chk("restore: відкритий пункт власної ролі є", "2026-10-01 — ВІДКРИТО. питання" in card)
chk("restore: закритий пункт НЕ показується", "закрите" not in card)
chk("restore: чужий пункт НЕ показується", "чуже" not in card)
chk("restore: невідома роль → порожньо", recall.restore_card("nope") == "")
chk("restore: cap тримається", len(recall.restore_card("seo", cap=40)) <= 40)

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ recall detect_role/restore — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
