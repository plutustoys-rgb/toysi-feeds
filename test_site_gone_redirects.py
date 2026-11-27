#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_gone_redirects.py — 27.11.2026 (завдання SEO: зняті картки з попитом віддавали 410 → 301 на живий аналог).

Що перевіряється (Apache тут не запускається — правило береться З ТЕКСТУ activate_site_apache.sh):
  • джерело site/gone_redirects.json: формат, id лише ASCII-цифри (ціль ще rt-…), нема ланцюжків (ціль не є знятою), нема пар «сама на себе»; жодна знята картка не збігається з site/removed_pages.json (410 назавжди лишається 410);
  • генератор site_gone_redirects.py: рядки мапи; жива знята сторінка НЕ редіректиться; відсутня ціль → пара пропущена (лишається 410); ланцюжок/сміття пропущено; rt-ціль знаходить публічний product-yX.html;
    зіпсований/відсутній JSON не перезаписує наявну мапу (exit 1), порожній JSON при непорожній мапі — теж; мапи ще нема → порожня з заголовком;
  • правило Apache: RewriteMap sitegoneredir, умови (є в мапі, файлу знятої НЕМА), 301 на значення мапи, ДО загального 410 видалених карток; скрипт активації викликає генератор, кладе порожню мапу за відсутності, має пробу;
  • МУТАЦІЇ: порядок правил / відсутність умови -f / відсутність захисту «жива» роблять набір червоним.
Самодостатній."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))

GEN_SRC = (HERE / "site_gone_redirects.py").read_text(encoding="utf-8")
SCRIPT_SRC = (HERE / "deploy" / "activate_site_apache.sh").read_text(encoding="utf-8")
SRC = json.loads((HERE / "site" / "gone_redirects.json").read_text(encoding="utf-8"))
REMOVED = json.loads((HERE / "site" / "removed_pages.json").read_text(encoding="utf-8"))
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def load_gen(src, tmp):
    m = types.ModuleType("sgr_under_test")
    m.__file__ = str(HERE / "site_gone_redirects.py")
    m.__dict__["__name__"] = "sgr_under_test"
    os.environ["SITE_DIR"] = str(tmp / "site")
    os.environ["SITE_GONE_REDIRECTS_JSON"] = str(tmp / "site" / "gone_redirects.json")
    os.environ["SITE_GONE_REDIRECT_MAP_APACHE"] = str(tmp / "out.map")
    exec(compile(src, "site_gone_redirects.py", "exec"), m.__dict__)
    return m


def run_main(m):
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = m.main()
    return rc, err.getvalue()


# ── 1. джерело ──
red = SRC.get("redirects", {})
chk("джерело: непорожнє, id — лише ASCII-цифри, ціль — цифри або rt-…", bool(red) and all(re.fullmatch(r"[0-9]{1,20}", k) and re.fullmatch(r"(?:rt-[0-9A-Za-z_]{1,40}|[0-9]{1,20})", str(v)) for k, v in red.items()))
chk("джерело: нема пар «сама на себе» й ланцюжків (ціль не є знятою)", all(k != str(v) for k, v in red.items()) and not (set(red) & {str(v) for v in red.values()}))
removed = set((REMOVED.get("products") or {}).keys())
chk("джерело: жодна знята картка не з removed_pages.json (знято назавжди → лишається 410)", not (set(red) & removed))

# ── 2. генератор ──
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    (tmp / "site").mkdir()
    site = tmp / "site"
    # живі цілі: 251737 (цифри), rt-33859 → публічний product-y33859.html; ціль 311349 відсутня; знята 18332 раптом ЖИВА
    for f in ("product-251737.html", "product-y33859.html", "product-286750.html", "product-18332.html"):
        (site / f).write_text("x", encoding="utf-8")
    (site / "gone_redirects.json").write_text(json.dumps({"redirects": {
        "279941": "251737", "205162": "rt-33859", "283552": "286750", "305996": "311349", "18332": "251737",
        "111": "222", "222": "251737", "abc": "251737", "333": "٣٣٣", "444": "444", "٥٥٥": "251737"}}), encoding="utf-8")
    g = load_gen(GEN_SRC, tmp)
    rc, err = run_main(g)
    out = (tmp / "out.map").read_text(encoding="utf-8")
    lines = [l for l in out.splitlines() if l and not l.startswith("#")]
    chk("генератор: пари з живою ціллю → рядки «product-<знята> product-<ціль>.html»", "product-279941 product-251737.html" in lines and "product-283552 product-286750.html" in lines)
    chk("генератор: rt-ціль знаходить публічний product-y33859.html", "product-205162 product-y33859.html" in lines)
    chk("генератор: знята картка, що ЖИВА в збірці (18332), НЕ редіректиться + попередження", not any(l.startswith("product-18332 ") for l in lines) and "ЖИВА" in err)
    chk("генератор: відсутня ціль (311349) → пару пропущено (лишається 410) + попередження", not any(l.startswith("product-305996 ") for l in lines) and "цілі нема" in err)
    chk("генератор: ланцюжок (111 → 222, а 222 сама знята) пропущено; сміттєві/нелатинські ключі, самоціль пропущено", not any(l.startswith(("product-111 ", "product-333 ", "product-444 ")) for l in lines) and "ланцюжок" in err and all(re.fullmatch(r"product-[0-9]+ product-\S+\.html", l) for l in lines))
    chk("генератор: exit 0, файл має заголовок-коментар", rc == 0 and out.startswith("#"))

    # зіпсований JSON → мапа не перезаписана
    before = (tmp / "out.map").read_text(encoding="utf-8")
    (site / "gone_redirects.json").write_text("{не json", encoding="utf-8")
    g = load_gen(GEN_SRC, tmp)
    rc, err = run_main(g)
    chk("зіпсований JSON: exit 1, наявна мапа НЕ перезаписана", rc == 1 and (tmp / "out.map").read_text(encoding="utf-8") == before)
    # порожній валідний JSON при непорожній мапі → не чіпаємо
    (site / "gone_redirects.json").write_text(json.dumps({"redirects": {}}), encoding="utf-8")
    g = load_gen(GEN_SRC, tmp)
    rc, err = run_main(g)
    chk("порожній JSON при непорожній мапі: exit 1, мапа не стерта (shrink-guard)", rc == 1 and (tmp / "out.map").read_text(encoding="utf-8") == before)
    # відсутній JSON і мапи ще нема → порожня мапа з заголовком
    (tmp / "out.map").unlink()
    (site / "gone_redirects.json").unlink()
    g = load_gen(GEN_SRC, tmp)
    rc, err = run_main(g)
    chk("нема ні JSON, ні мапи: exit 1, але порожня мапа створена (Apache configtest не впаде)", rc == 1 and (tmp / "out.map").exists() and (tmp / "out.map").read_text(encoding="utf-8").startswith("#"))

# ── 3. правило Apache і скрипт активації ──
RULE = re.search(r'RewriteMap sitegoneredir "txt:@APP@/site_gone_redirect_apache\.map"\n(\s*RewriteCond[^\n]+\n){2}\s*RewriteRule[^\n]+\n', SCRIPT_SRC)
chk("Apache: RewriteMap sitegoneredir + дві умови + RewriteRule з R=301", RULE is not None)
blk = RULE.group(0) if RULE else ""
chk("Apache: умова «є в мапі» (${sitegoneredir:$1|-} !=-) і «файлу знятої НЕМА» (!-f) — жива сторінка не редіректиться", "${sitegoneredir:$1|-} !=-" in blk and "%{DOCUMENT_ROOT}/$1.html !-f" in blk)
chk("Apache: RewriteRule — лише product-<цифри>, 301 на значення мапи", "RewriteRule ^/(product-[0-9]+)\\.html$ /${sitegoneredir:$1} [R=301,L]" in blk)
i_red = SCRIPT_SRC.find("RewriteMap sitegoneredir")
i_gen = SCRIPT_SRC.find("RewriteRule ^/(product-[A-Za-z0-9_-]+)\\.html$ - [G,L]")
chk("Apache: правило 301 стоїть ДО загального 410 видалених карток (інакше 410 перекрило б 301)", 0 < i_red < i_gen)
chk("скрипт активації: викликає site_gone_redirects.py, кладе порожню мапу за відсутності, виставляє ACL", 'site_gone_redirects.py' in SCRIPT_SRC and 'GONEREDIRMAP="$APP/site_gone_redirect_apache.map"' in SCRIPT_SRC and 'setfacl -m "u:$APUSER:r--" "$GONEREDIRMAP"' in SCRIPT_SRC and "порожня мапа 301" in SCRIPT_SRC)
chk("скрипт активації: проба (перший рядок мапи → 301 на ціль, ціль 200; хибне = bad=1)", "gr_exp=" in SCRIPT_SRC and "веде не на аналог" in SCRIPT_SRC and "ціль 301" in SCRIPT_SRC)

# ── 4. МУТАЦІЇ ──
def mutate_gen(old, new):
    assert old in GEN_SRC, old
    return GEN_SRC.replace(old, new)

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td); (tmp / "site").mkdir(); site = tmp / "site"
    (site / "product-251737.html").write_text("x", encoding="utf-8")
    (site / "product-18332.html").write_text("x", encoding="utf-8")
    (site / "product-222.html").write_text("x", encoding="utf-8")   # «ціль-ланцюжок» існує як файл: ловить саме захист ланцюжка, а не «цілі нема»
    (site / "gone_redirects.json").write_text(json.dumps({"redirects": {"18332": "251737", "279941": "251737", "111": "279941"}}), encoding="utf-8")
    m1 = load_gen(mutate_gen('if (site_dir / f"product-{src}.html").exists():', "if False:"), tmp)
    lines1 = m1.build_lines({"18332": "251737"}, site)
    chk("МУТАЦІЯ «прибрано захист живої сторінки» → набір це ловить (рядок для живої 18332 з'являється)", any(l.startswith("product-18332 ") for l in lines1))
    m2 = load_gen(mutate_gen("if dst in srcs:", "if False:"), tmp)
    lines2 = m2.build_lines({"111": "222", "222": "251737"}, site)
    chk("МУТАЦІЯ «прибрано захист ланцюжка» → ловиться (111 → 222 проходить)", any(l.startswith("product-111 ") for l in lines2))
    m3 = load_gen(GEN_SRC, tmp)
    chk("контроль без мутації: захисти працюють", not any(l.startswith("product-18332 ") for l in m3.build_lines({"18332": "251737"}, site)) and not any(l.startswith("product-111 ") for l in m3.build_lines({"111": "222", "222": "251737"}, site)))
mut_script = SCRIPT_SRC.replace("RewriteCond %{DOCUMENT_ROOT}/$1.html !-f\n    RewriteRule ^/(product-[0-9]+)", "RewriteRule ^/(product-[0-9]+)")
chk("МУТАЦІЯ «прибрано умову !-f у правилі 301» → тест умови її ловить", "%{DOCUMENT_ROOT}/$1.html !-f" not in (re.search(r'RewriteMap sitegoneredir[^\n]*\n(?:\s*RewriteCond[^\n]+\n)*\s*RewriteRule[^\n]+\n', mut_script).group(0)))

print("\nALL OK" if not F else f"\nFAIL: {len(F)}")
sys.exit(1 if F else 0)
