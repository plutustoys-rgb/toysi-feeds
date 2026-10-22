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

# --- аудит PR #961: B1 (межі слова kod/kodv)
def role_of(first):
    return recall.detect_role(make_transcript(f"r{abs(hash(first))}.jsonl", first))


chk("plutus-kodv -> kodv (не нічия і не kod)", role_of("Ти — агент plutus-kodv, бухгалтер") == "kodv")
chk("«агент-кодв, перевір книгу» НЕ kod", role_of("агент-кодв, перевір книгу") != "kod")
chk("«агент-код» -> kod", role_of("Ти — агент-код PlutusToys, працюй") == "kod")
chk("plutus-kod -> kod", role_of("Ти — plutus-kod. Читай канали") == "kod")

# --- B2: маскування
for label, src, secret in [
    ("Bearer", "curl -H 'Authorization: Bearer abc123DEF456ghi'", "abc123DEF456ghi"),
    ("JSON-ключ", '{"password": "hunter2xyz", "api_key": "k-9f8e7d"}', "hunter2xyz"),
    ("*_SECRET_KEY", "export LIQPAY_PRIVATE_KEY=sandbox_i123456789 ; X_SECRET_KEY=zzTOPzz1", "sandbox_i123456789"),
    ("--password", "mysql --password s3cr3tPass -h x", "s3cr3tPass"),
    ("-p'x'", "mysql -u root -p'MyRootPw99' db", "MyRootPw99"),
    ("пароль:", "мій пароль: Qwerty12345 вставте", "Qwerty12345"),
    ("ghp_ короткий", "token ghp_A1b2C3d4E5f6G7h8", "A1b2C3d4E5f6G7h8"),
    ("AKIA", "key AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
]:
    chk(f"маскується: {label}", secret not in recall._redact(src))
sha = "a" * 8 + "1" * 32
chk("git-SHA (40 hex) не маскується", recall._redact(f"коміт {sha}") == f"коміт {sha}")
chk("звичайний текст без змін", recall._redact("Мерж PR 961 після аудиту, файл recall.py") == "Мерж PR 961 після аудиту, файл recall.py")
# токен у команді довше 90 символів: маска ДО обрізання
longtok = "ghp_" + "A1b2C3d4E5" * 4
tpx = tmp / "redact.jsonl"
tpx.write_text("\n".join([
    line(type="user", message={"content": "Ти — агент-SEO (plutus-seo)"}),
    line(type="assistant", message={"content": [{"type": "tool_use", "name": "Bash",
         "input": {"command": "curl -H 'Authorization: Bearer " + longtok + "' https://x"}}]}),
]) + "\n", encoding="utf-8")
pr = recall.save_state({"transcript_path": str(tpx), "trigger": "auto"})
chk("токен у команді не лишається навіть частково", "A1b2C3d4E5" not in Path(pr).read_text(encoding="utf-8"))

# --- вибірка запитів: службове не потрапляє, справжнє — так
tpq = tmp / "queries.jsonl"
tpq.write_text("\n".join([
    line(type="user", message={"content": "Ти — агент-SEO (plutus-seo)"}),
    line(type="user", isCompactSummary=True, message={"content": "This session is being continued from a previous"}),
    line(type="user", message={"content": "[Request interrupted by user for tool use]"}),
    line(type="user", message={"content": "<local-command-stdout>вивід</local-command-stdout>"}),
    line(type="user", message={"content": [{"type": "text", "text": "<system-reminder>вставка</system-reminder>"},
                                            {"type": "text", "text": "ЗАПИТ-ПІСЛЯ-ВСТАВКИ зроби X"}]}),
    line(type="attachment", attachment={"type": "queued_command", "prompt": "ЗАПИТ-ПОСЕРЕД-ХОДУ зроби Y",
                                         "origin": {"kind": "human"}}),
    line(type="attachment", attachment={"type": "queued_command", "prompt": "<task-notification>x</task-notification>",
                                         "origin": {"kind": "task-notification"}}),
    "[1]", '"рядок"', '{"message": "str"}',
]) + "\n", encoding="utf-8")
st2 = Path(recall.save_state({"transcript_path": str(tpq), "trigger": "manual"})).read_text(encoding="utf-8")
chk("запит після вставки в одному повідомленні збережено", "ЗАПИТ-ПІСЛЯ-ВСТАВКИ" in st2)
chk("повідомлення посеред ходу (queued_command/human) збережено", "ЗАПИТ-ПОСЕРЕД-ХОДУ" in st2)
chk("compact-summary / interrupted / local-command / task-notification не потрапили",
    not any(x in st2 for x in ("This session is being continued", "Request interrupted", "вивід", "task-notification")))

# --- SessionStart: стан лише для compact/resume; чужий cwd — тиша
(recall.ROLE_STATE_DIR / "seo.md").write_text("СТАН-ДЛЯ-ТЕСТУ\n", encoding="utf-8")
chk("source=compact: стан є, з позначкою «історичний контекст»",
    "СТАН-ДЛЯ-ТЕСТУ" in (c := recall.session_context({"transcript_path": tp, "source": "compact"})) and "ІСТОРИЧНИЙ" in c)
chk("source=startup: стану попередньої сесії НЕМАЄ",
    "СТАН-ДЛЯ-ТЕСТУ" not in recall.session_context({"transcript_path": tp, "source": "startup"}))
chk("cwd поза C:/Users/smach -> порожньо",
    recall.session_context({"transcript_path": tp, "source": "compact", "cwd": "D:/other/project"}) == "")
chk("cwd у домашній теці -> працює",
    "ЯДРО ПРАВИЛ" in recall.session_context({"transcript_path": tp, "source": "compact", "cwd": "C:\\Users\\smach\\rozetka_agent"}))

# --- роль за діяльністю сесії (дефект Консультанта 22.10: перша фраза власника роль не називає)
def activity_transcript(name, blocks, first="у нас стався збій, як ти? у тебе як з роллю та памяттю?"):
    p = tmp / name
    rows = [line(type="user", message={"content": first})]
    for b in blocks:
        rows.append(line(type="assistant", message={"content": [b]}))
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return str(p)


def skill(name):
    return {"type": "tool_use", "name": "Skill", "input": {"skill": name}}


for sk, want in [("anthropic-skills:business-consultant", "consultant"), ("anthropic-skills:seo-agent", "seo"),
                 ("anthropic-skills:plutustoys-smm", "smm"), ("anthropic-skills:accountant", "kodv"),
                 ("anthropic-skills:seller", "seller"), ("anthropic-skills:agent-code", "kod"),
                 ("anthropic-skills:agent-architector", "architector")]:
    chk(f"виклик скіла {sk.split(':')[1]} -> {want}", recall.detect_role(activity_transcript(f"sk_{want}.jsonl", [skill(sk)])) == want)
chk("нерольовий скіл (loop) -> порожньо", recall.detect_role(activity_transcript("sk_loop.jsonl", [skill("loop")])) == "")
chk("два різні рольові скіли -> нічия -> порожньо",
    recall.detect_role(activity_transcript("sk_two.jsonl", [skill("anthropic-skills:seo-agent"), skill("anthropic-skills:seller")])) == "")
chk("скіл з префіксом-пустим іменем (seo-agent) теж ловиться",
    recall.detect_role(activity_transcript("sk_bare.jsonl", [skill("seo-agent")])) == "seo")

hdr_w = {"type": "tool_use", "name": "Write", "input": {"content": "## [Консультант → Код] 2026-10-22 — тест\nтіло\n"}}
hdr_e = {"type": "tool_use", "name": "Edit", "input": {"new_string": "## [Консультант → SEO] 2026-10-22 (2) — тест\n"}}
chk("2 власні заголовки каналу -> роль автора", recall.detect_role(activity_transcript("h2.jsonl", [hdr_w, hdr_e])) == "consultant")
chk("1 заголовок (можливо цитата) -> порожньо", recall.detect_role(activity_transcript("h1.jsonl", [hdr_w])) == "")
quote = tmp / "hq.jsonl"
quote.write_text("\n".join([
    line(type="user", message={"content": "Привіт"}),
    line(type="user", message={"content": "## [Консультант → Код] 1\n## [Консультант → Код] 2\n## [Консультант → Код] 3"}),
    line(type="attachment", attachment={"type": "hook_additional_context", "content": "## [SEO → Код] 1\n## [SEO → Код] 2"}),
]) + "\n", encoding="utf-8")
chk("заголовки у ВХІДНИХ повідомленнях/вставках не рахуються", recall.detect_role(str(quote)) == "")
chk("перше повідомлення з маркером важливіше за діяльність",
    recall.detect_role(activity_transcript("first_wins.jsonl", [skill("anthropic-skills:seller")], first="Ти — агент-SEO (plutus-seo)")) == "seo")

# --- ролі site / tester: картка бере і журнал-чергу (останні заголовки), і окремий канал Shopify
chk("_CONFIGS має роль site", "site" in recall._CONFIGS)
jdir = tmp / "jr"
jdir.mkdir()
(jdir / "тест_сайту_журнал.md").write_text("# журнал\n### [10.10] 🔴 1. СТАРА знахідка\n### [15.10] 🟡 2. середня\n"
                                           "### [22.10] 🔴 3. НАЙНОВІША знахідка\n", encoding="utf-8")
(jdir / "SHOPIFY_CHANNEL.md").write_text("## [Тестувальник → Консультант, Код-Shopify] 2026-09-29 (82) — свіжа\n"
                                         "## [Код-Shopify → Тестувальник] 2026-09-28 — стара\n", encoding="utf-8")
(jdir / "OWNER_INBOX.md").write_text("## [Тестувальник] 2026-10-22 — ВІДКРИТО. питання до власника\n", encoding="utf-8")
recall._CONFIGS["tester"] = [jdir / "тест_сайту_журнал.md", jdir / "OWNER_INBOX.md"]
(jdir / "CONSULTANT_CHANNEL.md").write_text("".join(f"## [Консультант → Код] 2026-10-{d:02d} — запис {d}" + chr(10) for d in range(22, 10, -1)), encoding="utf-8")
recall._CONFIGS["site"] = [jdir / "SHOPIFY_CHANNEL.md", jdir / "тест_сайту_журнал.md", jdir / "CONSULTANT_CHANNEL.md", jdir / "OWNER_INBOX.md"]
with open(cw / "OWNER_INBOX.md", "a", encoding="utf-8") as fh:   # restore_card читає OWNER_INBOX з COWORK_DIR (тут = cw)
    fh.write("## [Тестувальник] 2026-10-22 — ВІДКРИТО. питання до власника\n")
card_t = recall.restore_card("tester", n=2)
chk("tester: картка бере ОСТАННІ заголовки журналу, не перші", "НАЙНОВІША" in card_t and "середня" in card_t and "СТАРА" not in card_t)
chk("tester: картка позначає журнал як «лише читання» + відкрите питання власнику", "лише читання" in card_t and "питання до власника" in card_t)
card_s = recall.restore_card("site")
chk("site: картка має канал Shopify і журнал Тестувальника", "SHOPIFY_CHANNEL.md" in card_s and "НАЙНОВІША" in card_s)
chk("site: заголовки каналу newest-on-top (свіжа раніше за стару)", card_s.index("свіжа") < card_s.index("стара"))
chk("журнал-картка не вмикається для довільних журналів (КОДВ_журнал не читається як чергa)",
    "КОДВ_журнал.md" not in recall._JOURNAL_CARD)
chk("власні заголовки Код-Shopify -> роль site",
    recall.detect_role(activity_transcript("site_h.jsonl", [
        {"type": "tool_use", "name": "Write", "input": {"content": "## [Код-Shopify → Тестувальник] 2026-10-22 — a\n"}},
        {"type": "tool_use", "name": "Edit", "input": {"new_string": "## [Shopify-code → Консультант] 2026-10-22 — b\n"}}])) == "site")
chk("перше повідомлення «Ти — Shopify-code» -> site", role_of("Ти — Shopify-code проєкту Vartov") == "site")
chk("«агент-Shopify» -> site", role_of("Ти — агент-Shopify проєкту Vartov") == "site")
# негативні (аудит PR #964): колега, що ЗГАДУЄ Shopify-code у перших 140 символах, не стає site і не втрачає свою роль
chk("Код, що згадує Shopify-code -> kod, не site", role_of("Ти — агент-код. Shopify-code чекає на PR від тебе.") == "kod")
chk("Консультант, що згадує Shopify-code -> consultant", role_of("Ти — бізнес-консультант. Передай Shopify-code, що ...") == "consultant")
chk("SEO, що згадує Shopify-code -> seo", role_of("Ти — агент-SEO (plutus-seo). Передай Shopify-code ...") == "seo")
chk("SMM, що згадує Shopify-code двічі -> smm", role_of("Ти — агент-маркетолог соцмереж; Shopify-code, Shopify-code ...") == "smm")
tight = recall.restore_card("site", cap=900)
chk("site: при тісному cap журнал (головний вхід) лишається в картці, а не відрізається", "НАЙНОВІША" in tight)

# --- 22.10 (Shopify-code спитала власника «я Vartov-Код чи Shopify-code?»): SEO_CHANNEL у картці site лише з її записами;
#     seo не читає чергу, адресовану Shopify-code; згадка vartov/файла процедури НЕ дає ролі
seo_dir = tmp / "seo_ch"
seo_dir.mkdir()
(seo_dir / "SEO_CHANNEL.md").write_text(
    "## [SEO → Shopify-code, Консультант] 2026-10-12 — два відкриті питання сайту\\n".replace("\\n", chr(10)) +
    "## [Консультант → SEO] 2026-10-14 — чужа SEO-черга ПРО GMC" + chr(10) +
    "## [Shopify-code → SEO] 2026-10-13 — відповідь по сайту" + chr(10) +
    "## [Консультант → Shopify-code] 2026-10-11 — лише Shopify-code, без SEO" + chr(10) +
    "## [SEO → Консультант] 2026-10-10 — SEO-запис без сайту" + chr(10), encoding="utf-8")
(seo_dir / "MARKETING_CHANNEL.md").write_text("## [SMM → Shopify-code] 2026-10-21 — додай рух «Муха»" + chr(10) +
                                              "## [SMM → SEO] 2026-10-20 — чужа маркетингова черга SEO" + chr(10), encoding="utf-8")
recall._CONFIGS["site"] = [jdir / "SHOPIFY_CHANNEL.md", seo_dir / "MARKETING_CHANNEL.md", seo_dir / "SEO_CHANNEL.md", jdir / "OWNER_INBOX.md"]
recall._CONFIGS["seo"] = [seo_dir / "SEO_CHANNEL.md", seo_dir / "MARKETING_CHANNEL.md", jdir / "OWNER_INBOX.md"]
card_site = recall.restore_card("site", n=4)
chk("site: у картці записи SEO_CHANNEL, де фігурує Shopify-code (адресат АБО автор)",
    "два відкриті питання сайту" in card_site and "відповідь по сайту" in card_site)   # n=2 для site: два найсвіжіші
chk("site: чужа SEO-черга (без Shopify-code) у картку НЕ потрапляє",
    "ПРО GMC" not in card_site and "SEO-запис без сайту" not in card_site)
card_seo = recall.restore_card("seo", n=6)
chk("seo: записи, адресовані ЛИШЕ Shopify-code (без SEO), у картку seo НЕ потрапляють", "лише Shopify-code, без SEO" not in card_seo)
chk("site: запис MARKETING_CHANNEL, адресований Shopify-code, у картці; чужий SEO-запис — ні",
    "додай рух «Муха»" in card_site and "чужа маркетингова черга SEO" not in card_site)
chk("seo: запис MARKETING_CHANNEL, адресований ЛИШЕ Shopify-code, у картці seo немає; свій — є",
    "додай рух «Муха»" not in card_seo and "чужа маркетингова черга SEO" in card_seo)
chk("seo: власні записи і записи з участю SEO лишаються",
    "ПРО GMC" in card_seo and "два відкриті питання сайту" in card_seo and "відповідь по сайту" in card_seo and "SEO-запис без сайту" in card_seo)
chk("згадка vartov / процедура_vartov_vhost.md у першому повідомленні роль НЕ дає",
    role_of("Прочитай процедура_vartov_vhost.md у теці shopify_feed_app для vartov.app") == "")
chk("«Vartov-Код» без форми «Ти — …» роль не дає", role_of("Vartov-Код, перевір vhost") == "")

# --- 22.10 (прохання Консультанта): тег «Сайт» = автор/адресат ролі site (SEO_CHANNEL: `## [Сайт → SEO]`)
(seo_dir / "SEO_CHANNEL.md").write_text(
    "## [Сайт → SEO] 2026-10-15 — відповідь по сайту від тегу «Сайт»" + chr(10) +
    "## [SEO → Консультант] 2026-10-14 — SEO-запис про тестувальника сайту" + chr(10) +
    "## [Тестувальник сайту → Консультант] 2026-10-13 — «сайту» у назві ролі не є тегом site" + chr(10) +
    "## [Консультант → Сайт, SEO] 2026-10-12 — питання адресоване Сайту" + chr(10), encoding="utf-8")
recall._CONFIGS["site"] = [seo_dir / "SEO_CHANNEL.md", jdir / "OWNER_INBOX.md"]
recall._CONFIGS["seo"] = [seo_dir / "SEO_CHANNEL.md", jdir / "OWNER_INBOX.md"]
card_s2 = recall.restore_card("site", n=6)
chk("site: заголовки з тегом «Сайт» (автор і адресат) у картці",
    "відповідь по сайту від тегу" in card_s2 and "питання адресоване Сайту" in card_s2)
chk("site: «Тестувальник сайту» (слово «сайту») і чужі SEO-записи НЕ потрапляють",
    "«сайту» у назві ролі" not in card_s2 and "SEO-запис про тестувальника" not in card_s2)
card_seo2 = recall.restore_card("seo", n=6)
chk("seo: записи з її участю лишаються, а «Сайт → SEO» і «Консультант → Сайт, SEO» — теж (в них є SEO)",
    "відповідь по сайту від тегу" in card_seo2 and "питання адресоване Сайту" in card_seo2 and "SEO-запис про тестувальника" in card_seo2)
chk("seo: запис ЛИШЕ для тегу «Сайт» (без SEO) у картку seo не потрапляє",
    "тільки для сайту" not in recall.restore_card("seo", n=6))
(seo_dir / "SEO_CHANNEL.md").write_text(
    "## [Консультант → Сайт] 2026-10-11 — тільки для сайту без SEO" + chr(10) +
    "## [SEO → Консультант] 2026-10-10 — SEO-запис" + chr(10), encoding="utf-8")
chk("seo: запис [Консультант → Сайт] (без SEO) відфільтровано дзеркально до Shopify-code",
    "тільки для сайту" not in recall.restore_card("seo", n=6) and "SEO-запис" in recall.restore_card("seo", n=6))
chk("site: цей запис [Консультант → Сайт] — у картці site", "тільки для сайту" in recall.restore_card("site", n=6))
chk("активність: власні заголовки «Сайт →» двічі -> роль site",
    recall.detect_role(activity_transcript("site_tag.jsonl", [
        {"type": "tool_use", "name": "Write", "input": {"content": "## [Сайт → SEO] 2026-10-22 — a" + chr(10)}},
        {"type": "tool_use", "name": "Edit", "input": {"new_string": "## [Сайт → Консультант] 2026-10-22 — b" + chr(10)}}])) == "site")

# --- env пріоритетніший
import os
os.environ["PLUTUS_ROLE"] = "tester"
chk("PLUTUS_ROLE перекриває детекцію", recall.detect_role(tp) == "tester")
del os.environ["PLUTUS_ROLE"]

sys.exit(1 if F else 0)
