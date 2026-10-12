#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_prom_group_redirects.py — 12.10.2026 (SEO-замовлення): старі URL груп Prom `/g<gid>-<slug>` (88 шт., ≈40% показів у GSC) → 301 на /category-<slug>.html.

Що перевіряється (Apache тут не запускається — правила беруться З ТЕКСТУ activate_site_apache.sh, а їх порядок/умови відтворює міні-симулятор mod_rewrite):
  • генератор (generate_prom_redirects.py): існуюча/неіснуюча категорія, null, дублікати gid, порожній/зіпсований JSON, кирилиця, зайві пробіли, [a-z0-9-]+ / лише цифри,
    сортування «найбільше показів першим», формат кожного рядка, збій мапи груп не ламає мапу товарів, мапа-файл існує завжди (RewriteMap з відсутнім файлом валить configtest);
  • правило Apache: мовний префікс (/ua, /ru, без), довільний хвіст slug, gid поза мапою → /catalog.html як було, ?query відкидається (QSD), наявні p<id>/410 і чужі адреси не зачеплено;
  • проби/відкат у скрипті активації (статично);
  • дані SEO у репо (prom_group_to_category.json) структурно валідні;
  • МУТАЦІЇ: кожна з навмисних поломок генератора/правила/проб має зробити набір червоним (інакше тест нічого не доводить).
Самодостатній."""
import contextlib
import io
import json
import re
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))

GEN_SRC = (HERE / "generate_prom_redirects.py").read_text(encoding="utf-8")
SCRIPT_SRC = (HERE / "deploy" / "activate_site_apache.sh").read_text(encoding="utf-8")
JSON_PATH = HERE / "prom_group_to_category.json"


def load_gen(src):
    m = types.ModuleType("gpr_under_test")
    m.__file__ = str(HERE / "generate_prom_redirects.py")
    exec(compile(src, "generate_prom_redirects.py", "exec"), m.__dict__)
    return m


def mutate(src, old, new):
    assert src.count(old) == 1, f"мутація не знайшла рівно одне входження: {old[:60]!r} ({src.count(old)})"
    return src.replace(old, new)


# ── міні-симулятор mod_rewrite над RewriteCond/RewriteRule із body_tpl() скрипта активації ─────────────────────────────────────────────
def body_rules(script):
    body = script.split("body_tpl() {", 1)[1].split("\nEOF\n", 1)[0]
    return [l.strip() for l in body.splitlines() if l.strip().startswith(("RewriteCond ", "RewriteRule "))]


def simulate(script, path, query="", maps=None):
    """-> (status, target): (301, '/category-x.html'), (410, None), ('rewrite', '/assets/…'), (200, None) — жодне правило не спрацювало."""
    maps = maps or {}
    conds = []
    for line in body_rules(script):
        if line.startswith("RewriteCond "):
            conds.append(line[len("RewriteCond "):])
            continue
        m = re.fullmatch(r"RewriteRule (\S+) (\S+)(?: \[([^\]]*)\])?", line)
        pat, sub, flags = m.group(1), m.group(2), (m.group(3) or "").split(",")
        pending, conds = conds, []
        mm = re.search(pat, path)
        if not mm:
            continue

        def expand(s, mm=mm):
            s = re.sub(r"\$\{(\w+):\$(\d)(?:\|([^}]*))?\}",
                       lambda x: maps.get(x.group(1), {}).get(mm.group(int(x.group(2))), x.group(3) if x.group(3) is not None else ""), s)
            return re.sub(r"\$(\d)", lambda x: mm.group(int(x.group(1))), s)
        ok = True
        for cond in pending:
            if cond.startswith("%{THE_REQUEST}"):
                continue                        # симулюємо пряме звернення клієнта
            cm = re.fullmatch(r"(\S+) (!?)=(.*)", cond)
            if (expand(cm.group(1)) == cm.group(3)) == (cm.group(2) == "!"):
                ok = False
        if not ok:
            continue
        if "G" in flags:
            return 410, None
        if sub == "-":
            continue
        target = expand(sub)
        if any(f.startswith("R=") for f in flags):
            if query and "QSD" not in flags:
                target += "?" + query
            return 301, target
        return "rewrite", target
    return 200, None


def silent():
    return contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO())


def read_map_lines(path):
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l[:1].isdigit()]


def run_groups(g, data, cats, raw_text=None, preexisting=None):
    """Запускає write_group_map() генератора g на тимчасовій теці. -> (rc, рядки мапи, stderr, шлях мапи)."""
    tmp = Path(tempfile.mkdtemp())
    site = tmp / "site"
    site.mkdir()
    (site / "index.json").write_text("[]", encoding="utf-8")
    for c in cats:
        (site / f"category-{c}.html").write_text("x", encoding="utf-8")
    src = tmp / "groups.json"
    src.write_text(raw_text if raw_text is not None else json.dumps(data, ensure_ascii=False), encoding="utf-8")
    g.GROUP_JSON = src
    g.INDEX_FILE = site / "index.json"
    out = tmp / "groups_apache.map"
    g.OUT_FILE_GROUPS_APACHE = out
    if preexisting is not None:
        out.write_text(preexisting, encoding="utf-8")
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = g.write_group_map()
    return rc, (read_map_lines(out) if out.exists() else None), err.getvalue(), out


def rec(cat, impr=1, slug="x"):
    return {"g_slug": slug, "impressions_90d": impr, "category": cat}


def suite(gen_src, script_src):
    """Повертає список назв провалених перевірок (порожній = усе гаразд)."""
    F = []

    def chk(name, cond):
        if not cond:
            F.append(name)
    g = load_gen(gen_src)

    # ── 1. існуюча / неіснуюча категорія / null ──────────────────────────────────────────────────────────────────────────────
    rc, lines, err, _ = run_groups(g, {"111": rec("lialky", 5), "222": rec("nema-takoi", 9), "333": rec(None, 7)}, ["lialky"])
    chk("існуюча категорія → рядок «gid /category-slug.html»", lines == ["111 /category-lialky.html"])
    chk("неіснуюча категорія пропущена, генератор не впав (rc=1 рядок)", rc == 1 and "222" not in " ".join(lines))
    chk("неіснуюча категорія → ГУЧНЕ попередження в stderr з gid і slug", "222" in err and "category-nema-takoi.html" in err and "УВАГА" in err)
    chk("null-категорія → у мапі немає і без шуму в stderr", "333" not in " ".join(lines) and "333" not in err)

    # ── 2. дублікати ────────────────────────────────────────────────────────────────────────────────────────────────────────
    rc, lines, err, _ = run_groups(g, None, ["a", "b"], raw_text='{"123": {"category": "a"}, "123": {"category": "b"}, "124": {"category": "a"}}')
    chk("дубль ключа gid у JSON (різні категорії) → неоднозначно: обидва пропущено, решта живе", lines == ["124 /category-a.html"] and "123" in err)
    rc, lines, err, _ = run_groups(g, {" 123 ": rec("a"), "123": rec("b")}, ["a", "b"])
    chk("« 123 » і «123» з різними категоріями → неоднозначно, пропущено", lines == [] and "123" in err)
    rc, lines, err, _ = run_groups(g, {" 123 ": rec("a"), "123": rec("a")}, ["a"])
    chk("« 123 » і «123» з тією ж категорією → один рядок", lines == ["123 /category-a.html"])

    # ── 3. порожній / зіпсований / не той тип JSON ──────────────────────────────────────────────────────────────────────────
    rc, lines, err, out = run_groups(g, {}, ["a"])
    chk("порожній JSON {} → файл мапи існує, без рядків, rc=0", rc == 0 and lines == [] and out.read_text(encoding="utf-8").startswith("#"))
    old = "# h\n777 /category-a.html\n"
    for label, raw in (("зіпсований JSON", "{not json"), ("масив замість об'єкта", "[1,2]"), ("порожній файл", "")):
        rc, lines, err, out = run_groups(g, None, ["a"], raw_text=raw, preexisting=old)
        chk(f"{label}: стара мапа лишається без змін, rc=-1, попередження", rc == -1 and out.read_text(encoding="utf-8") == old and "УВАГА" in err)
    rc, lines, err, out = run_groups(g, None, ["a"], raw_text="{bad", preexisting=None)
    chk("зіпсований JSON і мапи ще нема → створюється порожня мапа (Apache RewriteMap не терпить відсутнього файла)", out.exists() and lines == [])
    rc, lines, err, out = run_groups(g, {"1": "не об'єкт", "2": [], "3": {"category": 5}}, ["a"])
    chk("значення не-об'єкт / category не рядок → пропущено, без винятку", lines == [])

    # ── 4. кирилиця, пробіли, формат gid/slug ───────────────────────────────────────────────────────────────────────────────
    cats = ["lialky", "a-b-2"]
    rc, lines, err, _ = run_groups(g, {"1": rec("лялькі"), "2": rec("Lialky"), "3": rec("la lky"), "4": rec("../etc"), "5": rec("a/b"), "6": rec("lialky.html"),
                                      "7": rec("lialky\nx"), "8": rec(""), "9": rec("   "), "10": rec("lialky ")}, cats)
    chk("slug: кирилиця, верхній регістр, внутрішній пробіл, ../, /, .html, \\n, порожній — відхилено; «lialky » (пробіл на краю) → прийнято після strip",
        lines == ["10 /category-lialky.html"])
    chk("відхилений slug → попередження «не відповідає [a-z0-9-]+» з gid (а не «сторінки немає»: шар валідації формату працює сам)",
        all(f"gid {i}:" in err for i in (1, 2, 3, 4, 5, 6, 7, 8, 9)) and err.count("не відповідає") == 9 and "НЕМАЄ" not in err)
    rc, lines, err, _ = run_groups(g, {"abc": rec("lialky"), "١٢٣": rec("lialky"), "12 3": rec("lialky"), "-5": rec("lialky"), "1.5": rec("lialky"), "": rec("lialky"),
                                      " 42 ": rec("lialky"), "1" * 21: rec("lialky")}, cats)
    chk("gid: 7 негідних ключів відхилено саме валідацією gid («не gid»)", err.count("не gid") == 7)
    chk("gid: літери, арабсько-індійські цифри, внутрішній пробіл, мінус, крапка, порожній, >20 цифр — відхилено; « 42 » → 42", lines == ["42 /category-lialky.html"])
    rc, lines, err, _ = run_groups(g, {"5": rec("a-b-2")}, cats)
    chk("slug із суфіксом -2 (дубль slugify у build_site) та дефісами — валідний", lines == ["5 /category-a-b-2.html"])

    # ── 5. порядок і формат ─────────────────────────────────────────────────────────────────────────────────────────────────
    rc, lines, err, out = run_groups(g, {"30": rec("a", 10, "EVILSLUG"), "10": rec("a", 500), "20": rec("a", 500), "5": rec("a", True), "6": rec("a", -4), "7": rec("a", "9999"), "8": rec("a", None)},
                                     ["a"])
    chk("перший рядок мапи = найбільше показів; рівні — за коротшим/меншим gid; bool/від'ємні/рядок/None-показів = 0 без падіння",
        [l.split()[0] for l in lines] == ["10", "20", "30", "5", "6", "7", "8"])
    chk("кожен рядок мапи відповідає формату «<цифри> /category-<[a-z0-9-]+>.html»", all(g.GROUP_LINE_RE.fullmatch(l) for l in lines))
    raw_bytes = out.read_bytes()
    chk("файл мапи: LF-кінці, без CR, закінчується новим рядком, перший рядок — коментар", b"\r" not in raw_bytes and raw_bytes.endswith(b"\n") and raw_bytes.startswith(b"#"))
    chk("g_slug (недовірений текст) у мапу не потрапляє", b"EVILSLUG" not in raw_bytes)

    # ── 6. main(): збій мапи груп не ламає мапу товарів; мапи пишуться атомарно у вказані шляхи ─────────────────────────────────
    tmp = Path(tempfile.mkdtemp())
    site = tmp / "site"
    site.mkdir()
    (site / "index.json").write_text(json.dumps([{"id": "1"}]), encoding="utf-8")
    (site / "category-a.html").write_text("x", encoding="utf-8")
    (tmp / "cache.json").write_text(json.dumps({"1": {"prom_id": 1001}}), encoding="utf-8")
    (tmp / "groups.json").write_text(json.dumps({"11": rec("a", 3)}), encoding="utf-8")
    g.CACHE_FILE, g.INDEX_FILE, g.OUT_FILE, g.OUT_FILE_APACHE = tmp / "cache.json", site / "index.json", tmp / "p.map", tmp / "pa.map"
    g.GROUP_JSON, g.OUT_FILE_GROUPS_APACHE = tmp / "groups.json", tmp / "ga.map"
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        g.main()
    chk("main(): пише і мапу товарів, і мапу груп", read_map_lines(tmp / "pa.map") == ["1001 /product-1.html"] and read_map_lines(tmp / "ga.map") == ["11 /category-a.html"])
    (tmp / "groups.json").write_text("{broken", encoding="utf-8")
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        g.main()
    chk("main(): зіпсований JSON груп — мапа товарів ціла, мапа груп не затерта", read_map_lines(tmp / "pa.map") == ["1001 /product-1.html"] and read_map_lines(tmp / "ga.map") == ["11 /category-a.html"])
    (tmp / "groups.json").write_text(json.dumps({"11": rec("a", 3)}), encoding="utf-8")
    (site / "category-a.html").unlink()
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        g.main()
    chk("main(): сторінка категорії зникла зі збірки → її gid випадає з мапи (повертається /catalog.html), мапа файлом лишається", (tmp / "ga.map").exists() and read_map_lines(tmp / "ga.map") == [])

    g.OUT_FILE_GROUPS_APACHE = tmp / "нема_теки" / "ga.map"          # запис мапи груп неможливий (OSError) — мапа товарів цього не відчуває
    (tmp / "pa.map").unlink()
    raised = None
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            g.main()
    except Exception as e:  # noqa: BLE001
        raised = e
    chk("main(): збій ЗАПИСУ мапи груп (OSError) не валить генератор, мапа товарів записана, у stderr гучне попередження",
        raised is None and read_map_lines(tmp / "pa.map") == ["1001 /product-1.html"] and "мапу груп не записано" in err.getvalue())

    # ── 7. правило Apache (симулятор над реальним текстом скрипта) ───────────────────────────────────────────────────────────
    maps = {"grpredir": {"155145045": "/category-samokaty-trykolisni.html"}, "promredir": {"3122611441": "/product-1.html"}}
    sim = lambda p, q="": simulate(script_src, p, q, maps)       # noqa: E731
    cat = (301, "/category-samokaty-trykolisni.html")
    chk("/ua/g<gid>-slug → 301 на категорію", sim("/ua/g155145045-samokati-trikolisni") == cat)
    chk("/ru/g<gid>-slug → та сама категорія", sim("/ru/g155145045-samokati-trikolisni") == cat)
    chk("/g<gid>-slug БЕЗ префікса → та сама категорія", sim("/g155145045-samokati-trikolisni") == cat)
    chk("довільний хвіст slug (кирилиця, /page_2, порожній, крапки) не заважає", all(sim(p) == cat for p in (
        "/ua/g155145045-", "/ua/g155145045-x/page_2", "/ua/g155145045-%D0%BB%D1%8F%D0%BB%D1%8C%D0%BA%D0%B8", "/g155145045-a.b.c", "/ru/g155145045--x-")))
    chk("?query Prom не переноситься на сторінку категорії (QSD)", sim("/ua/g155145045-x", "product_items_per_page=48") == cat)
    chk("gid ПОЗА мапою (null-категорія, невідомий) → /catalog.html, як було", all(sim(p) == (301, "/catalog.html") for p in (
        "/ua/g155144964-vodnyj", "/g1-x", "/ru/g99999999999999-x", "/ua/g1551450450-x", "/ua/g15514504-x")))
    chk("gid — це ПОВНЕ число: префікс/суфікс чужого gid не збігається", sim("/ua/g1551450451-x") == (301, "/catalog.html") and sim("/ua/g155145045x-y") == (200, None))
    chk("адреса без дефіса після gid (/ua/g155145045) і чужі префікси (/uk/, /ua/ua/) — правило не чіпає", all(sim(p) == (200, None) for p in (
        "/ua/g155145045", "/uk/g155145045-x", "/ua/ua/g155145045-x", "/xg155145045-x", "/ug155145045-x")))
    chk("наявні правила не зламано: p<id> з мапи → 301 картка; p<id> без запису → 410; product_list → /catalog.html; /ua/ → /",
        sim("/ua/p3122611441-x.html") == (301, "/product-1.html") and sim("/ua/p1-x.html") == (410, None)
        and sim("/ua/product_list") == (301, "/catalog.html") and sim("/ua/") == (301, "/") and sim("/favicon.ico") == ("rewrite", "/assets/favicon.ico"))
    chk("наші адреси не зачеплено: /product-155145045.html, /category-x.html, /catalog.html, /gift.html, /g.html, /api/np/city",
        all(sim(p) == (200, None) for p in ("/product-155145045.html", "/category-samokaty-trykolisni.html", "/catalog.html", "/gift.html", "/g.html", "/api/np/city")))

    # ── 8. скрипт активації: мапа, підстановка, проби, відкат (статично) ────────────────────────────────────────────────────────
    chk("RewriteMap grpredir оголошено рівно один раз і вказує на @GROUPMAPFILE@", script_src.count('RewriteMap grpredir "txt:@GROUPMAPFILE@"') == 1)
    chk("sub() підставляє @GROUPMAPFILE@ з $GROUPMAP", 's#@GROUPMAPFILE@#$GROUPMAP#g' in script_src)
    chk("$GROUPMAP = $APP/prom_group_redirects_apache.map", 'GROUPMAP="$APP/prom_group_redirects_apache.map"' in script_src)
    chk("файл мапи груп гарантовано існує ДО запису vhost'а (RewriteMap без файла валить configtest) і читається Apache-користувачем",
        script_src.index("generate_prom_redirects.py") < script_src.index('if [ ! -e "$GROUPMAP" ]') < script_src.index("=== C/D. APACHE VHOST ===")
        and 'can_read "$GROUPMAP" || die' in script_src)
    probe_lines = [l for l in script_src.splitlines() if '🚨 gid групи Prom' in l]
    chk("проби груп у write_vhost: 3 перевірки (поза мапою → каталог; /ua/ → категорія; без префікса+?query → категорія), кожна ставить bad=1 (→ відкат)",
        len(probe_lines) == 3 and all("bad=1" in l for l in probe_lines))
    chk("проба бере gid із мапи (перший рядок), а gid-поза-мапою — 99999999999999", 'awk \'/^[0-9]/{print $1; exit}\' "$GROUPMAP"' in script_src and "g99999999999999-gone-probe" in script_src)
    chk("проба очікує точний Location: схема://домен + значення мапи (/category-….html), а поза мапою — /catalog.html",
        'gp_exp="301 $sch2://$DOMAIN$gp_val"' in script_src and '"301 $sch2://$DOMAIN/catalog.html"' in script_src)
    chk("фаза C (до перемикання DNS): групи Prom перевіряються з die", 'die "gid групи Prom поза мапою не веде 301 на /catalog.html' in script_src and 'die "301 групи Prom на категорію не працює' in script_src)

    # ── 9. дані SEO у репо ─────────────────────────────────────────────────────────────────────────────────────────────────
    chk("prom_group_to_category.json існує й читається", JSON_PATH.is_file())
    if JSON_PATH.is_file():
        raw = json.loads(JSON_PATH.read_text(encoding="utf-8-sig"))
        chk("JSON репо: об'єкт, не порожній", isinstance(raw, dict) and len(raw) > 0)
        chk("JSON репо: усі gid — цифри, всі category — null або [a-z0-9-]+, показів — ціле ≥0",
            all(g.GID_RE.fullmatch(k) and isinstance(v, dict) and (v.get("category") is None or (isinstance(v["category"], str) and g.SLUG_RE.fullmatch(v["category"])))
                and type(v.get("impressions_90d")) is int and v["impressions_90d"] >= 0 for k, v in raw.items()))
        cats_all = sorted({v["category"] for v in raw.values() if v.get("category")})
        rc, lines, err, _ = run_groups(g, raw, cats_all)
        n_ok = sum(1 for v in raw.values() if v.get("category"))
        chk("JSON репо + усі сторінки категорій існують → у мапі рівно стільки рядків, скільки gid з категорією; жодного попередження",
            rc == n_ok == len(lines) and "УВАГА" not in err and all(g.GROUP_LINE_RE.fullmatch(l) for l in lines))
        top = max((v for v in raw.values() if v.get("category")), key=lambda v: v["impressions_90d"])
        chk("JSON репо: перший рядок мапи — gid з найбільшими показами", lines and raw[lines[0].split()[0]]["impressions_90d"] == top["impressions_90d"])
    return F


# ══ базовий прогін ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
base_failures = suite(GEN_SRC, SCRIPT_SRC)
for f in base_failures:
    print(f"[FAIL] {f}")
print(f"[{'OK ' if not base_failures else 'FAIL'}] базовий набір (генератор, правило Apache, скрипт, дані SEO): {'усе гаразд' if not base_failures else str(len(base_failures)) + ' провалів'}")

# ══ МУТАЦІЇ: кожна має зробити набір червоним ════════════════════════════════════════════════════════════════════════════════
SWAP_CATCH = "    RewriteRule ^/(?:(?:ua|ru)/)?g[0-9]+- /catalog.html [R=301,L]\n"
MUTATIONS = [
    ("G1 генератор не перевіряє існування сторінки категорії", "gen", lambda s: mutate(s, "if not (site_dir / f\"category-{slug}.html\").is_file():", "if False:")),
    ("G2 slug-регекс послаблено до .+ (кирилиця/../ дійшли б до перевірки існування)", "gen", lambda s: mutate(s, 'SLUG_RE = re.compile(r"[a-z0-9-]{1,200}")', 'SLUG_RE = re.compile(r".+")')),
    ("G3 gid-регекс послаблено до .+ (літери/арабські цифри)", "gen", lambda s: mutate(s, 'GID_RE = re.compile(r"[0-9]{1,20}")', 'GID_RE = re.compile(r".+")')),
    ("G4 суперечливі/дубльовані gid не викидаються", "gen", lambda s: mutate(s, "        cand.pop(gid, None)\n", "")),
    ("G5 пробіли по краях не обрізаються", "gen", lambda s: mutate(mutate(s, "gid = key.strip() if isinstance(key, str) else \"\"", "gid = key if isinstance(key, str) else \"\""),
                                                                    "slug = cat.strip() if isinstance(cat, str) else \"\"", "slug = cat if isinstance(cat, str) else \"\"")),
    ("G6 сортування за показами навпаки (перший рядок — найменше)", "gen", lambda s: mutate(s, "rows.append((-impr, len(gid), gid, line))", "rows.append((impr, len(gid), gid, line))")),
    ("G7 зіпсований JSON затирає стару мапу порожньою", "gen", lambda s: mutate(s, "        _warn(f\"{path} нечитний ({type(e).__name__}: {str(e)[:120]}) — мапу груп не оновлюю\")\n        return None",
                                                                                "        _warn(f\"{path} нечитний ({type(e).__name__}: {str(e)[:120]}) — мапу груп не оновлюю\")\n        return {}")),
    ("G8 зіпсований JSON і мапи ще нема → порожню мапу не створено (configtest Apache упаде)", "gen", lambda s: mutate(s, "        if not OUT_FILE_GROUPS_APACHE.exists():", "        if False:")),
    ("G9 збій мапи груп не ізольовано в main() (виняток летить нагору)", "gen", lambda s: mutate(s, "    except Exception as e:  # noqa: BLE001\n        _warn(f\"мапу груп не записано", "    except ZeroDivisionError as e:  # noqa: BLE001\n        _warn(f\"мапу груп не записано")),
    ("A1 правило Apache без умови «ключ є в мапі» (gid поза мапою дав би порожній редірект)", "script", lambda s: mutate(s, "    RewriteCond ${grpredir:$1|-} !=-\n", "")),
    ("A2 правило Apache без опційного мовного префікса", "script", lambda s: mutate(s, "RewriteRule ^/(?:(?:ua|ru)/)?g([0-9]+)- ${grpredir:$1}", "RewriteRule ^/g([0-9]+)- ${grpredir:$1}")),
    ("A3 catch-all /catalog.html стоїть ПЕРЕД правилом з мапи", "script", lambda s: mutate(s, SWAP_CATCH, "").replace('    RewriteMap grpredir', SWAP_CATCH + '    RewriteMap grpredir')),
    ("A4 без QSD (?query Prom тягнеться на категорію)", "script", lambda s: mutate(s, "[R=301,L,QSD]", "[R=301,L]")),
    ("A5 gid не прив'язано до повного числа (g[0-9]+ без дефіса)", "script", lambda s: mutate(s, "RewriteRule ^/(?:(?:ua|ru)/)?g([0-9]+)- ${grpredir:$1}", "RewriteRule ^/(?:(?:ua|ru)/)?g([0-9]+) ${grpredir:$1}")),
    ("A6 sub() не підставляє @GROUPMAPFILE@", "script", lambda s: mutate(s, ' -e "s#@GROUPMAPFILE@#$GROUPMAP#g"', "")),
    ("A7 проба групи не ставить bad=1 (відкат не спрацює)", "script", lambda s: mutate(s, "(/ua/g${gp_key}-…) веде не на свою категорію ($gp_ua, очікую $gp_exp)\"; bad=1; }",
                                                                                     "(/ua/g${gp_key}-…) веде не на свою категорію ($gp_ua, очікую $gp_exp)\"; }")),
    ("A8 правило груп зачіпає p<id>-товари: нове правило перед 410-правилом", "script", lambda s: mutate(s, "    RewriteMap promredir", "    RewriteRule ^/(?:(?:ua|ru)/)?p([0-9]+)- /catalog.html [R=301,L]\n    RewriteMap promredir")),
]
survived = []
for name, kind, fn in MUTATIONS:
    gen_src, script_src = GEN_SRC, SCRIPT_SRC
    if kind == "gen":
        gen_src = fn(GEN_SRC)
    else:
        script_src = fn(SCRIPT_SRC)
    try:
        fails = suite(gen_src, script_src)
    except Exception as e:  # noqa: BLE001 — виняток у мутанта теж «червоний» (набір не пройшов)
        fails = [f"виняток {type(e).__name__}: {str(e)[:80]}"]
    print(f"[{'OK ' if fails else 'FAIL'}] мутація «{name}» → {'набір ЧЕРВОНИЙ (' + str(len(fails)) + ')' if fails else 'набір ЗЕЛЕНИЙ — тест нічого не ловить!'}")
    if not fails:
        survived.append(name)

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(base_failures + survived) if (base_failures or survived) else f'✅ групи Prom → категорії: генератор, правило Apache, проби, дані SEO — коректні; усі {len(MUTATIONS)} мутацій червоніють.'}")
sys.exit(1 if (base_failures or survived) else 0)
