#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_category_moves.py — 04.11.2026, злиття 23 тонких категорій сайту в приймачі + 301 зі старих сторінок категорій.

Що перевіряється (Apache тут не запускається — правила беруться З ТЕКСТУ deploy/activate_site_apache.sh, порядок/умови відтворює міні-симулятор mod_rewrite):
  1. генератор (generate_prom_redirects.write_catmoves_map): валідні/невалідні slug, приймач без зібраної сторінки, старий == приймач, ланцюжок, цикл, дубль ключа,
     службові «_»-ключі, порожній/зіпсований/відсутній JSON (стара мапа не затирається, а відсутня — створюється порожньою), стара сторінка знову зібрана → попередження,
     формат рядків «<slug> <slug>», LF, сортування; main() пише мапу й ізолює її збій від мапи товарів;
  2. шаблон Apache: RewriteMap catmoved + обидва правила (сторінка й пагінація) з RewriteCond, підстановка @CATMOVESMAPFILE@ у sub(), файл мапи існує до vhost'а,
     читається Apache-користувачем; симулятор: старі адреси → 301 на 1-шу сторінку приймача (без ?query), живі категорії й наявні правила Prom не зачеплено;
  3. дані репо: site/category_moves.json (23 переїзди, формат, без ланцюжків), жоден файл даних сайту/редиректів не посилається на зниклий slug;
  4. повна збірка build() на синтетичному каталозі з РЕАЛЬНИМИ id і назвами 23 старих категорій та реальними файлами даних репо: старі category-<slug>.html
     не генеруються, товари — на сторінках приймачів, на старі сторінки не лишилось внутрішніх посилань (HTML, sitemap, index.json), генератор дає 23 рядки;
  5. МУТАЦІЇ генератора/шаблону мають робити набір червоним.
Самодостатній, мережа не потрібна."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
for _k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID", "SITE_BUILD_LIMIT"):
    os.environ.pop(_k, None)
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))

GEN_SRC = (HERE / "generate_prom_redirects.py").read_text(encoding="utf-8")
SCRIPT_SRC = (HERE / "deploy" / "activate_site_apache.sh").read_text(encoding="utf-8")
MOVES_PATH = HERE / "site" / "category_moves.json"

# 23 злиті категорії: slug → (назва категорії Toysi, id товарів у наявності на момент злиття; рішення 04.11.2026)
OLD = {
    'shcheniachyy-patrul': ('Щенячий патруль', ['119890']),
    'chovny': ('Човни', ['297461']),
    'ihrashky-na-kolesakh': ('Іграшки на колесах', ['33664']),
    'tovary-dlia-tvorchosti': ('Товари для творчості', ['259739']),
    'klavishni': ('Клавішні', ['47165']),
    'ialynky-i-sosny': ('Ялинки і сосни', ['250489']),
    'lialky-i-pupsy': ('Ляльки і пупси', ['223559']),
    'ihrovi-nabory': ('Ігрові набори', ['132709']),
    'dereviani-mebli': ("Дерев'яні меблі", ['30490']),
    'khodunky': ('Ходунки', ['299397']),
    'zubni-shchitky-ta-mochalky': ('Зубні щітки та мочалки', ['283555', '283554']),
    'labirynty': ('Лабіринти', ['285037', '279171']),
    'hadzhety-ta-aksesuary': ('Гаджети та аксесуари', ['299133', '216586']),
    'monkarty-i-skrichery': ('Монкарти і Скрічери', ['222396', '222399']),
    'mashyny-hihanty': ('Машини гіганти', ['241050', '18204']),
    'chovny-i-katery-ru': ('Човни і катери РУ', ['302555', '302556']),
    'nastilni-ihry-pazly': ('Настільні ігри, пазли', ['174731', '174721']),
    'mashynky': ('Машинки', ['311080', '311079', '305581']),
    'cherepashky-nindzia': ('Черепашки ніндзя', ['212734', '212727', '270142']),
    'zoshyt-u-kosu-liniiu': ('Зошит у косу лінію', ['303331', '301620', '303323']),
    'rostomiry': ('Ростоміри', ['267253', '194379', '194378']),
    'matematyka': ('Математика', ['201903', '213128', '161107']),
    'naduvni-miachi': ("Надувні м'ячі", ['37009', '294372', '102268']),
}
# категорії, які власник вирішив НЕ чіпати (не мають потрапити в мапу переїздів)
UNTOUCHED = ("Для ванної", "Догляд за волоссям", "Прання та миття посуду", "NEW: ПОБУТОВА ХІМІЯ", "ПАТРІОТИЧНА ТЕМА", "НОВОРІЧНІ ТОВАРИ❄", "Хлопавки", "Фінгерборди")


def load_gen(src):
    m = types.ModuleType("gpr_catmoves_under_test")
    m.__file__ = str(HERE / "generate_prom_redirects.py")
    exec(compile(src, "generate_prom_redirects.py", "exec"), m.__dict__)
    return m


def mutate(src, old, new):
    assert src.count(old) == 1, f"мутація не знайшла рівно одне входження: {old[:60]!r} ({src.count(old)})"
    return src.replace(old, new)


# ── міні-симулятор mod_rewrite (той самий, що в test_prom_group_redirects.py) ──────────────────────────────────────────────────
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
                continue
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


def read_map_lines(path):
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]


def run_moves(g, data, pages, raw_text=None, preexisting=None, missing_source=False):
    """write_catmoves_map() генератора g на тимчасовій теці. -> (rc, рядки мапи | None, stderr, шлях мапи)."""
    tmp = Path(tempfile.mkdtemp())
    site = tmp / "site"
    site.mkdir()
    (site / "index.json").write_text("[]", encoding="utf-8")
    for c in pages:
        (site / f"category-{c}.html").write_text("x", encoding="utf-8")
    src = tmp / "category_moves.json"
    if not missing_source:
        src.write_text(raw_text if raw_text is not None else json.dumps(data, ensure_ascii=False), encoding="utf-8")
    g.CATMOVES_JSON = src
    g.INDEX_FILE = site / "index.json"
    out = tmp / "category_moves_apache.map"
    g.OUT_FILE_CATMOVES_APACHE = out
    if preexisting is not None:
        out.write_text(preexisting, encoding="utf-8")
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = g.write_catmoves_map()
    return rc, (read_map_lines(out) if out.exists() else None), err.getvalue(), out


def suite(gen_src, script_src):
    """Список назв провалених перевірок генератора й шаблону (порожній = усе гаразд)."""
    F = []

    def chk(name, cond):
        if not cond:
            F.append(name)
    g = load_gen(gen_src)

    # ── 1. генератор ──────────────────────────────────────────────────────────────────────────────────────────────────────
    rc, lines, err, out = run_moves(g, {"_що це": "службовий", "mashynky": "plastykovi-mashynky", "chovny": "naduvni-matrasy"}, ["plastykovi-mashynky", "naduvni-matrasy"])
    chk("валідні переїзди → рядки «старий приймач», за алфавітом; службовий «_»-ключ ігнорується без шуму",
        rc == 2 and lines == ["chovny naduvni-matrasy", "mashynky plastykovi-mashynky"] and err == "")
    raw_bytes = out.read_bytes()
    chk("файл мапи: LF, без CR, перший рядок — коментар, закінчується \\n", b"\r" not in raw_bytes and raw_bytes.startswith(b"#") and raw_bytes.endswith(b"\n"))
    chk("кожен рядок відповідає CATMOVE_LINE_RE", all(g.CATMOVE_LINE_RE.fullmatch(l) for l in lines))

    rc, lines, err, _ = run_moves(g, {"a": "nema-storinky", "b": "rcv"}, ["rcv"])
    chk("приймач без зібраної сторінки → пропущено, ГУЧНО (slug приймача в stderr), решта живе",
        lines == ["b rcv"] and "category-nema-storinky.html" in err and "УВАГА" in err)

    bad = {"Машинки": "rcv", "Upper": "rcv", "a b": "rcv", "../etc": "rcv", "x.html": "rcv", "a/b": "rcv", "": "rcv", " pad": "rcv", "x" * 201: "rcv",
           "ok1": "Приймач", "ok2": "rcv ", "ok3": 5, "ok4": None, "ok5": ["rcv"], "ok6": "", "ok7": "../rcv", "ok8": "rcv\nevil x"}
    rc, lines, err, _ = run_moves(g, dict(bad, good="rcv"), ["rcv"])
    chk("невалідні slug (кирилиця, верхній регістр, пробіли, ../, /, .html, порожній, >200) і невалідні приймачі (не рядок, пробіл, \\n) — відхилено; валідний живе",
        lines == ["good rcv"] and err.count("не відповідає") == len(bad))
    chk("кожне відхилення названо в stderr", all(f"{k[:20]}" in err for k in ("ok1", "ok2", "ok3", "ok8")))

    rc, lines, err, _ = run_moves(g, {"same": "same", "x": "rcv"}, ["same", "rcv"])
    chk("старий == приймач → пропущено як петля 301 (окремою перевіркою, не лише через виявлення циклу)", lines == ["x rcv"] and "same" in err and "петля" in err)

    rc, lines, err, _ = run_moves(g, {"a": "b", "b": "c"}, ["b", "c"])
    chk("ланцюжок a→b→c: a пропущено (приймач сам є ключем), b→c лишається", lines == ["b c"] and "ланцюжок" in err and "a -> b" in err)

    rc, lines, err, _ = run_moves(g, {"a": "b", "b": "a", "c": "d"}, ["a", "b", "d"])
    chk("цикл a↔b: обидва пропущено (ЦИКЛ у stderr), незалежний c→d живе", lines == ["c d"] and err.count("ЦИКЛ") == 2)
    rc, lines, err, _ = run_moves(g, {"a": "b", "b": "c", "c": "a"}, ["a", "b", "c"])
    chk("цикл із трьох → усі пропущено", lines == [] and err.count("ЦИКЛ") == 3)

    rc, lines, err, _ = run_moves(g, None, ["r1", "r2"], raw_text='{"a": "r1", "a": "r2", "b": "r1"}')
    chk("дубль ключа в JSON → неоднозначно, пропущено; решта живе", lines == ["b r1"] and "дубль" in err)

    rc, lines, err, _ = run_moves(g, {"old": "rcv"}, ["rcv", "old"])
    chk("стара сторінка ЗНОВУ зібрана → рядок лишається, але ГУЧНЕ попередження", lines == ["old rcv"] and "ЗНОВУ зібрана" in err and "category_overrides.json" in err)

    rc, lines, err, out = run_moves(g, {}, [])
    chk("порожній {} → файл мапи існує, без рядків, rc=0", rc == 0 and lines == [] and out.read_text(encoding="utf-8").startswith("#"))
    rc, lines, err, out = run_moves(g, {"_що це": "лише службовий"}, [])
    chk("лише службові ключі → порожня мапа без шуму", rc == 0 and lines == [] and err == "")
    old = "# h\nkeep rcv\n"
    for label, raw in (("зіпсований JSON", "{not json"), ("масив замість об'єкта", '["a"]'), ("порожній файл", ""), ("глибоко вкладений", "[" * 100000)):
        rc, lines, err, out = run_moves(g, None, ["rcv"], raw_text=raw, preexisting=old)
        chk(f"{label}: стара мапа без змін, rc=-1, попередження", rc == -1 and out.read_text(encoding="utf-8") == old and "УВАГА" in err)
    rc, lines, err, out = run_moves(g, None, [], raw_text="{bad")
    chk("зіпсований JSON і мапи ще нема → створено порожню мапу (RewriteMap без файла валить configtest)", out.exists() and lines == [])
    rc, lines, err, out = run_moves(g, None, [], missing_source=True)
    chk("джерела нема → порожня мапа створена, rc=-1, попередження", rc == -1 and out.exists() and lines == [] and "нема" in err)
    rc, lines, err, out = run_moves(g, None, [], missing_source=True, preexisting=old)
    chk("джерела нема, мапа є → стара мапа не затерта", out.read_text(encoding="utf-8") == old)
    rc, lines, err, _ = run_moves(g, {"a": "r"}, [])
    chk("жоден переїзд не дав рядка (сайт не зібрано?) → окреме гучне попередження", lines == [] and "жоден" in err)

    # main(): мапа переїздів пишеться разом з іншими; її збій не валить генератор і не чіпає мапу товарів
    tmp = Path(tempfile.mkdtemp())
    site = tmp / "site"
    site.mkdir()
    (site / "index.json").write_text(json.dumps([{"id": "1"}]), encoding="utf-8")
    (site / "category-rcv.html").write_text("x", encoding="utf-8")
    (tmp / "cache.json").write_text(json.dumps({"1": {"prom_id": 1001}}), encoding="utf-8")
    (tmp / "moves.json").write_text(json.dumps({"old": "rcv"}), encoding="utf-8")
    g.CACHE_FILE, g.INDEX_FILE, g.OUT_FILE, g.OUT_FILE_APACHE = tmp / "cache.json", site / "index.json", tmp / "p.map", tmp / "pa.map"
    g.GROUP_JSON, g.OUT_FILE_GROUPS_APACHE = tmp / "no_groups.json", tmp / "ga.map"
    g.OVERRIDES_JSON = tmp / "no_overrides.json"
    g.CATMOVES_JSON, g.OUT_FILE_CATMOVES_APACHE = tmp / "moves.json", tmp / "cm.map"
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        g.main()
    chk("main(): пише і мапу товарів, і мапу переїздів", read_map_lines(tmp / "pa.map")[-1:] == ["1001 /product-1.html"] and read_map_lines(tmp / "cm.map") == ["old rcv"])
    g.OUT_FILE_CATMOVES_APACHE = tmp / "нема_теки" / "cm.map"
    (tmp / "pa.map").unlink()
    raised, err = None, io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            g.main()
    except Exception as e:  # noqa: BLE001
        raised = e
    chk("main(): збій ЗАПИСУ мапи переїздів (OSError) не валить генератор, мапа товарів записана, у stderr гучне попередження",
        raised is None and (tmp / "pa.map").exists() and "мапу переїздів категорій не записано" in err.getvalue())

    # ── 2. шаблон Apache ─────────────────────────────────────────────────────────────────────────────────────────────────
    chk("RewriteMap catmoved оголошено рівно один раз на @CATMOVESMAPFILE@", script_src.count('RewriteMap catmoved "txt:@CATMOVESMAPFILE@"') == 1)
    chk("sub() підставляє @CATMOVESMAPFILE@ з $CATMOVESMAP", 's#@CATMOVESMAPFILE@#$CATMOVESMAP#g' in script_src.split("sub() {", 1)[1].split("\n", 1)[0])
    chk("$CATMOVESMAP = $APP/category_moves_apache.map (той самий шлях, що пише генератор за замовчуванням на VPS)",
        'CATMOVESMAP="$APP/category_moves_apache.map"' in script_src and 'BASE / "category_moves_apache.map"' in gen_src)
    body = script_src.split("body_tpl() {", 1)[1].split("\nEOF\n", 1)[0]
    placeholders = set(re.findall(r"@[A-Z]+@", body))
    sub_line = script_src.split("sub() {", 1)[1].split("\n", 1)[0]
    chk("кожен @…@ у body_tpl підставляється в sub() (у vhost не лишиться сирого плейсхолдера)", all(f"s#{p}#" in sub_line for p in placeholders))
    chk("файл мапи гарантовано існує ДО запису vhost'а і читається Apache-користувачем (die)",
        script_src.index("generate_prom_redirects.py") < script_src.index('if [ ! -e "$CATMOVESMAP" ]') < script_src.index("=== C/D. APACHE VHOST ===")
        and 'can_read "$CATMOVESMAP" || die' in script_src)
    rules = body_rules(script_src)
    r1 = "RewriteRule ^/category-([a-z0-9-]+)\\.html$ /category-${catmoved:$1}.html [R=301,L,QSD]"
    r2 = "RewriteRule ^/category-([a-z0-9-]+)_[0-9]+\\.html$ /category-${catmoved:$1}.html [R=301,L,QSD]"
    chk("обидва правила є, і перед КОЖНИМ стоїть RewriteCond ${catmoved:$1|-} !=-",
        r1 in rules and r2 in rules and rules[rules.index(r1) - 1] == "RewriteCond ${catmoved:$1|-} !=-" and rules[rules.index(r2) - 1] == "RewriteCond ${catmoved:$1|-} !=-")
    chk("RewriteMap оголошено ДО правил, що його використовують", body.index("RewriteMap catmoved") < body.index("${catmoved:$1|-}"))
    probe_lines = [l for l in script_src.splitlines() if "🚨" in l and ("злитої категорії" in l or "сторінка приймача" in l)]
    chk("проби злиття у write_vhost: 3 перевірки (стара, пагінація, приймач = 200), кожна ставить bad=1 (→ відкат); фаза C — die",
        len(probe_lines) == 3 and all("bad=1" in l for l in probe_lines) and 'die "301 злитої категорії на приймач не працює' in script_src)

    maps = {"catmoved": {"mashynky": "plastykovi-mashynky", "khodunky": "katalky-tolokar"},
            "grpredir": {"155145045": "/category-samokaty-trykolisni.html"}, "promredir": {"3122611441": "/product-1.html"}}
    sim = lambda p, q="": simulate(script_src, p, q, maps)      # noqa: E731
    chk("/category-<старий>.html → 301 на 1-шу сторінку приймача", sim("/category-mashynky.html") == (301, "/category-plastykovi-mashynky.html"))
    chk("/category-<старий>_N.html → 301 на 1-шу сторінку приймача", sim("/category-mashynky_2.html") == (301, "/category-plastykovi-mashynky.html")
        and sim("/category-khodunky_17.html") == (301, "/category-katalky-tolokar.html"))
    chk("?query не переноситься (QSD)", sim("/category-mashynky.html", "sort=price") == (301, "/category-plastykovi-mashynky.html")
        and sim("/category-mashynky_3.html", "utm_source=x") == (301, "/category-plastykovi-mashynky.html"))
    chk("живі категорії (поза мапою) і сам приймач НЕ редиректяться", all(sim(p) == (200, None) for p in (
        "/category-plastykovi-mashynky.html", "/category-plastykovi-mashynky_2.html", "/category-katalky-tolokar.html", "/category-mashynky-ru.html",
        "/category-mashynky-na-batarey-kakh_4.html", "/category-mashynky-2.html")))
    chk("схожі на старий slug адреси не зачеплено (префікс/суфікс, інший регістр, підтека, без .html)", all(sim(p) == (200, None) for p in (
        "/category-mashynkyx.html", "/category-xmashynky.html", "/category-Mashynky.html", "/ua/category-mashynky.html", "/category-mashynky",
        "/category-mashynky.htm", "/category-mashynky_.html", "/category-mashynky_2x.html", "/xcategory-mashynky.html")))
    chk("наявні правила не зламано: p<id> з мапи → картка; без запису → 410; g<gid> → категорія/каталог; helovin; /ua/ → /; favicon",
        sim("/ua/p3122611441-x.html") == (301, "/product-1.html") and sim("/ua/p1-x.html") == (410, None)
        and sim("/ua/g155145045-x") == (301, "/category-samokaty-trykolisni.html") and sim("/g1-x") == (301, "/catalog.html")
        and sim("/helovin_2.html") == (301, "/halloween_2.html") and sim("/ua/") == (301, "/") and sim("/favicon.ico") == ("rewrite", "/assets/favicon.ico"))
    chk("наші адреси не зачеплено: /catalog.html, /product-1.html, /group-x.html, /index.json, /api/np/city",
        all(sim(p) == (200, None) for p in ("/catalog.html", "/product-1.html", "/group-mashynky.html", "/index.json", "/api/np/city", "/categories.html")))
    chk("порожня мапа → жодна /category-… не редиректиться", simulate(script_src, "/category-mashynky.html", "", {}) == (200, None))
    return F


def data_and_build_checks():
    """Дані репо + повна збірка на синтетичному каталозі. -> список провалів."""
    F = []

    def chk(name, cond):
        if not cond:
            F.append(name)
    import build_site as bs
    g = load_gen(GEN_SRC)

    # ── 3. дані репо ─────────────────────────────────────────────────────────────────────────────────────────────────────
    moves_raw = json.loads(MOVES_PATH.read_text(encoding="utf-8-sig"))
    moves = {k: v for k, v in moves_raw.items() if not k.startswith("_")}
    chk("category_moves.json: є службовий «_що це» з поясненням", isinstance(moves_raw.get("_що це"), str) and len(moves_raw["_що це"]) > 50)
    chk("category_moves.json: рівно 23 переїзди, ключі = 23 злиті slug", set(moves) == set(OLD) and len(moves) == 23)
    chk("category_moves.json: усі slug [a-z0-9-]{1,200}, старий ≠ приймач, без ланцюжків (приймач не є ключем)",
        all(g.SLUG_RE.fullmatch(k) and isinstance(v, str) and g.SLUG_RE.fullmatch(v) and k != v and v not in moves for k, v in moves.items()))
    chk("slug старих категорій = slugify(назва) збірки", all(bs.slugify(name) == s for s, (name, _ids) in OLD.items()))
    ov = json.loads((HERE / "site" / "category_overrides.json").read_text(encoding="utf-8-sig"))
    moved_ids = [i for _s, (_n, ids) in OLD.items() for i in ids]
    chk("category_overrides.json: кожен товар злитої категорії має перенесення", all(i in ov for i in moved_ids))
    # виняток (рішення 04.11, дані як є): «Машинки» — товари розійшлися в «Металеві моделі» ×2 і «Машинки на батарейках», а стара адреса широкого запиту веде на «Пластикові машинки»
    REDIRECT_ONLY = {"mashynky"}
    chk("category_overrides.json: приймач з category_moves.json — це категорія, куди перенесено хоч один товар старої категорії (slugify збігається; виняток — REDIRECT_ONLY)",
        all(s in REDIRECT_ONLY or any(bs.slugify(ov[i]) == moves[s] for i in ids) for s, (_n, ids) in OLD.items())
        and all(not any(bs.slugify(ov[i]) == moves[s] for i in OLD[s][1]) for s in REDIRECT_ONLY))
    chk("category_overrides.json: жоден товар не переноситься У злиту категорію", not ({bs.slugify(v) for k, v in ov.items() if not k.startswith("_")} & set(moves)))
    chk("відкладені власником категорії не злиті", not ({bs.slugify(n) for n in UNTOUCHED} & set(moves)))

    def strings(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from strings(v)
        elif isinstance(o, list):
            for v in o:
                yield from strings(v)
        elif isinstance(o, str):
            yield o
    gone_rx = re.compile(r"(?:^|category-)(" + "|".join(map(re.escape, moves)) + r")(?:_[0-9]+)?(?:\.html)?$")
    for rel in ("site/category_texts.json", "site/category_ru_alt.json", "site/category_related.json", "site/sitemap_priority.json",
                "prom_group_to_category.json", "prom_redirect_overrides.json"):
        p = HERE / rel
        if not p.is_file():
            continue
        hits = sorted({s for s in strings(json.loads(p.read_text(encoding="utf-8-sig"))) if gone_rx.search(s.strip("/"))})
        chk(f"{rel}: жодного посилання на зниклий slug ({hits[:5]})", not hits)

    # ── 4. повна збірка на синтетичному каталозі ──────────────────────────────────────────────────────────────────────────
    catalog = {}

    def add(pid, name, cat, price, stock=5):
        catalog[str(pid)] = {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": stock, "category_name": cat, "category_id": "10",
                             "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "barcode": "",
                             "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів."}
    receivers = set()
    for s, (name, ids) in OLD.items():
        for i in ids:
            add(i, f"Товар {i} зі старої категорії", name, 150 + int(i) % 97)
            receivers.add(ov[i])
    for n, rname in enumerate(sorted(receivers)):            # приймачі — живі категорії з власними товарами
        for j in range(3):
            add(900000 + n * 10 + j, f"Наповнення {rname} {j}", rname, 120 + j)
    add(990001, "Старий товар без залишку", "Машинки", 199, stock=0)   # OOS у старій категорії: сторінка картки жива, але категорію не створює
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    bs.tp.fetch_toysi_catalog = lambda: catalog
    bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
    bs.price_of = lambda it: int(it["price"])
    bs.LIMIT = 0
    import site_photo_probe
    site_photo_probe.run = lambda idx: None
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        bs.build()
    err = err.getvalue()
    files = sorted(os.listdir(out))
    old_pages = [f for f in files if re.fullmatch(r"category-(" + "|".join(map(re.escape, moves)) + r")(_[0-9]+)?\.html", f)]
    chk(f"збірка: жодної сторінки category-<старий>[_N].html ({old_pages[:5]})", not old_pages)
    chk("збірка: сторінки всіх приймачів зібрано", all((out / f"category-{v}.html").is_file() for v in set(moves.values())))
    misplaced = []
    for s, (_n, ids) in OLD.items():
        for i in ids:
            page = (out / f"category-{bs.slugify(ov[i])}.html").read_text(encoding="utf-8")
            if f"product-{i}.html" not in page:
                misplaced.append(i)
    chk(f"збірка: кожен перенесений товар — на сторінці свого приймача ({misplaced[:5]})", not misplaced)
    link_rx = re.compile(r"category-(" + "|".join(map(re.escape, moves)) + r")(?:_[0-9]+)?\.html")
    linked = sorted({f for f in files if f.endswith((".html", ".xml", ".json", ".txt", ".js")) and link_rx.search((out / f).read_text(encoding="utf-8", errors="replace"))})
    chk(f"збірка: жодного внутрішнього посилання на старі сторінки (HTML, sitemap, index.json, …) — у файлах {linked[:5]}", not linked)
    idx = json.loads((out / "index.json").read_text(encoding="utf-8"))
    old_names = {n for n, _ in OLD.values()}
    chk("index.json: жоден товар не лишився в старій категорії", not [e for e in idx if e.get("c") in old_names])
    chk("OOS-товар старої категорії: картка жива й веде на catalog.html (не на зниклу сторінку)",
        (out / "product-990001.html").is_file() and "category-mashynky.html" not in (out / "product-990001.html").read_text(encoding="utf-8"))
    warn_lines = [l for l in err.splitlines() if "slug без сторінки" in l or "без сторінки категорії" in l]
    chk(f"збірка: попередження «slug без сторінки» не згадують злитих slug ({[l[:120] for l in warn_lines if link_rx.search(l) or any(s in l for s in moves)][:3]})",
        not [l for l in warn_lines if any(re.search(r"'" + re.escape(s) + r"'", l) for s in moves)])
    chk("збірка: жоден із 42 нових записів category_overrides.json не відкинуто", not [i for i in moved_ids if f"'{i}'" in "".join(l for l in err.splitlines() if "category_overrides.json" in l)])

    # генератор на зібраній теці: 23 рядки без попереджень; симулятор веде кожну стару адресу на живий приймач
    g.INDEX_FILE = out / "index.json"
    g.CATMOVES_JSON = MOVES_PATH
    g.OUT_FILE_CATMOVES_APACHE = out.parent / (out.name + "_cm.map")
    e2 = io.StringIO()
    with contextlib.redirect_stderr(e2), contextlib.redirect_stdout(io.StringIO()):
        rc = g.write_catmoves_map()
    lines = read_map_lines(g.OUT_FILE_CATMOVES_APACHE)
    chk(f"генератор на зібраному сайті: 23 рядки, без попереджень (rc={rc}, {e2.getvalue()[:200]!r})", rc == 23 and len(lines) == 23 and "УВАГА" not in e2.getvalue())
    cm = dict(l.split(" ") for l in lines)
    bad_sim = [s for s in moves if simulate(SCRIPT_SRC, f"/category-{s}.html", "", {"catmoved": cm}) != (301, f"/category-{moves[s]}.html")
               or simulate(SCRIPT_SRC, f"/category-{s}_2.html", "", {"catmoved": cm}) != (301, f"/category-{moves[s]}.html")
               or not (out / f"category-{moves[s]}.html").is_file()]
    chk(f"кожна стара адреса (і _2) → 301 на існуючу сторінку приймача ({bad_sim[:5]})", not bad_sim)

    # новий товар у наявності з'явився в старій категорії → сторінка знову збирається, генератор попереджає
    add(990002, "Новий товар у старій категорії", "Ходунки", 250)
    out2 = Path(tempfile.mkdtemp())
    bs.OUT = str(out2)
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        bs.build()
    g.INDEX_FILE = out2 / "index.json"
    e3 = io.StringIO()
    with contextlib.redirect_stderr(e3), contextlib.redirect_stdout(io.StringIO()):
        g.write_catmoves_map()
    chk("новий товар у старій категорії: сторінка знову зібрана, генератор ГУЧНО попереджає (редирект її сховає)",
        (out2 / "category-khodunky.html").is_file() and "khodunky" in e3.getvalue() and "ЗНОВУ зібрана" in e3.getvalue())
    return F


# ══ базовий прогін ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
base = suite(GEN_SRC, SCRIPT_SRC)
for f in base:
    print(f"[FAIL] {f}")
print(f"[{'OK ' if not base else 'FAIL'}] генератор + шаблон Apache: {'усе гаразд' if not base else str(len(base)) + ' провалів'}")
db = data_and_build_checks()
for f in db:
    print(f"[FAIL] {f}")
print(f"[{'OK ' if not db else 'FAIL'}] дані репо + повна збірка на синтетичному каталозі: {'усе гаразд' if not db else str(len(db)) + ' провалів'}")

# ══ МУТАЦІЇ: кожна має зробити набір червоним ════════════════════════════════════════════════════════════════════════════════
MUTATIONS = [
    ("G1 генератор не перевіряє сторінку приймача", "gen", lambda s: mutate(s, 'if not (site_dir / f"category-{new}.html").is_file():', "if False:")),
    ("G2 ланцюжки/цикли не відсікаються", "gen", lambda s: mutate(s, "        if new in cand:                                # приймач теж", "        if False:                                # приймач теж")),
    ("G3 петля старий == приймач пропускається", "gen", lambda s: mutate(s, "        if key == val:\n", "        if False:\n")),
    ("G4 slug-регекс приймача не перевіряється", "gen", lambda s: mutate(s, "if not (isinstance(val, str) and SLUG_RE.fullmatch(val)):", "if not isinstance(val, str):")),
    ("G5 зіпсований JSON затирає стару мапу", "gen", lambda s: mutate(s, "{str(e)[:120]}) — мапу переїздів категорій не оновлюю\")\n        return None", "{str(e)[:120]}) — мапу переїздів категорій не оновлюю\")\n        return {}")),
    ("G6 джерела нема і мапи нема → порожню мапу не створено", "gen", lambda s: mutate(s, "        if not OUT_FILE_CATMOVES_APACHE.exists():", "        if False:")),
    ("G7 збій мапи переїздів не ізольовано в main()", "gen", lambda s: mutate(s, "    except Exception as e:  # noqa: BLE001\n        _warn_cm(", "    except ZeroDivisionError as e:  # noqa: BLE001\n        _warn_cm(")),
    ("A1 правило сторінки без RewriteCond (поза мапою — порожній редирект)", "script", lambda s: mutate(s, "    RewriteCond ${catmoved:$1|-} !=-\n    RewriteRule ^/category-([a-z0-9-]+)\\.html$", "    RewriteRule ^/category-([a-z0-9-]+)\\.html$")),
    ("A2 правило пагінації відсутнє", "script", lambda s: mutate(s, "    RewriteRule ^/category-([a-z0-9-]+)_[0-9]+\\.html$ /category-${catmoved:$1}.html [R=301,L,QSD]\n", "")),
    ("A3 без QSD", "script", lambda s: mutate(s, "    RewriteRule ^/category-([a-z0-9-]+)\\.html$ /category-${catmoved:$1}.html [R=301,L,QSD]", "    RewriteRule ^/category-([a-z0-9-]+)\\.html$ /category-${catmoved:$1}.html [R=301,L]")),
    ("A4 sub() не підставляє @CATMOVESMAPFILE@", "script", lambda s: mutate(s, ' -e "s#@CATMOVESMAPFILE@#$CATMOVESMAP#g"', "")),
    ("A5 файл мапи не створюється, якщо його нема", "script", lambda s: mutate(s, 'if [ ! -e "$CATMOVESMAP" ]; then', 'if false; then')),
    ("A6 правило без якоря кінця (зачіпає /category-x.htmlX)", "script", lambda s: mutate(s, "RewriteRule ^/category-([a-z0-9-]+)\\.html$ ", "RewriteRule ^/category-([a-z0-9-]+)\\.html ")),
    ("A7 проба пагінації не ставить bad=1", "script", lambda s: mutate(s, "очікую $cm_exp; QSD не діє?)\"; bad=1; }", "очікую $cm_exp; QSD не діє?)\"; }")),
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
    except Exception as e:  # noqa: BLE001 — виняток у мутанта теж «червоний»
        fails = [f"виняток {type(e).__name__}: {str(e)[:80]}"]
    print(f"[{'OK ' if fails else 'FAIL'}] мутація «{name}» → {'набір ЧЕРВОНИЙ (' + str(len(fails)) + ')' if fails else 'набір ЗЕЛЕНИЙ — тест нічого не ловить!'}")
    if not fails:
        survived.append(name)

bad_all = base + db + survived
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(bad_all) if bad_all else f'✅ злиття категорій: генератор, правило Apache, дані, повна збірка — коректні; усі {len(MUTATIONS)} мутацій червоніють.'}")
sys.exit(1 if bad_all else 0)
