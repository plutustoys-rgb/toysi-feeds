#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_removed_pages.py — 06.11.2026 (власник «хімію прибирай», завдання SEO): побутова хімія зникає з сайту, а її сторінки віддають 410 Gone, а не 404.

Що перевіряється (Apache тут не запускається — правила беруться З ТЕКСТУ activate_site_apache.sh, порядок/умови відтворює міні-симулятор mod_rewrite):
  • джерело правди site/removed_pages.json: формат, КОЖЕН запис заборонений forbidden_products.is_forbidden (410 не може пережити зняття заборони мовчки),
    slug = slugify(назви), НЕ-хімія (299076-8, rt-62870/62872/62873) відсутня;
  • конфіги сайту/Prom більше не посилаються на ці категорії й товари (групи, sitemap_priority, related/texts/ru_alt/overrides, мапи груп і перекриттів Prom);
  • генератор site_gone_pages.py: рядки мапи, жива сторінка НЕ йде в мапу, сміттєві ключі пропущено, зіпсований JSON не перезаписує мапу, мапа існує завжди;
  • правило Apache: /product-<id>.html і /category-<slug>[_N].html з мапи → 410; адреси поза мапою (живі товари/категорії, схожі id) і наявні правила не зачеплено;
  • скрипт активації: шлях мапи в шаблоні = змінна GONEMAP = файл генератора, генератор викликається, ACL, проби з bad=1; site-rebuild.service; .gitignore;
  • збірка сайту на синтетичному каталозі: жодної категорії/товару хімії в HTML, index*.json, sitemap, групах, чипах; старі сторінки хімії прибрано кроком 8;
  • МУТАЦІЇ правила/генератора/проб мають зробити набір червоним (інакше тест нічого не доводить).
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
os.environ["SITE_PICK_DATE"] = "2026-10-10"
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import forbidden_products as fp  # noqa: E402
import telegram_notify  # noqa: E402,F401 — генератор шле алерт через нього; тест підміняє send_throttled_alert

GEN_SRC = (HERE / "site_gone_pages.py").read_text(encoding="utf-8")
SCRIPT_SRC = (HERE / "deploy" / "activate_site_apache.sh").read_text(encoding="utf-8")
SRC = json.loads((HERE / "site" / "removed_pages.json").read_text(encoding="utf-8"))
CHEM_CATS = ["Засоби для прання", "Догляд за будинком", "Засоби для миття посуду", "NEW: ПОБУТОВА ХІМІЯ", "Прання та миття посуду"]
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def load_gen(src):
    m = types.ModuleType("sgp_under_test")
    m.__file__ = str(HERE / "site_gone_pages.py")
    exec(compile(src, "site_gone_pages.py", "exec"), m.__dict__)
    return m


def mutate(src, old, new):
    assert src.count(old) == 1, f"мутація не знайшла рівно одне входження: {old[:60]!r} ({src.count(old)})"
    return src.replace(old, new)


# ── міні-симулятор mod_rewrite (той самий, що в test_prom_group_redirects.py) ──────────────────────────────────────────────────────
def body_rules(script):
    body = script.split("body_tpl() {", 1)[1].split("\nEOF\n", 1)[0]
    return [l.strip() for l in body.splitlines() if l.strip().startswith(("RewriteCond ", "RewriteRule "))]


def simulate(script, path, maps):
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
            return 410
        if sub == "-":
            continue
        return 301 if any(f.startswith("R=") for f in flags) else "rewrite"
    return 200


def run_gen(g, data=None, raw_text=None, live=(), preexisting=None):
    tmp = Path(tempfile.mkdtemp())
    site = tmp / "site"
    site.mkdir()
    for fn in live:
        (site / fn).write_text("x", encoding="utf-8")
    src = site / "removed_pages.json"
    if raw_text is not None or data is not None:
        src.write_text(raw_text if raw_text is not None else json.dumps(data, ensure_ascii=False), encoding="utf-8")
    g.SITE_DIR, g.SOURCE_JSON, g.OUT_FILE = site, src, tmp / "gone.map"
    if preexisting is not None:
        g.OUT_FILE.write_text(preexisting, encoding="utf-8")
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = g.main()
    lines = [l for l in g.OUT_FILE.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")] if g.OUT_FILE.exists() else None
    return rc, lines, err.getvalue(), g.OUT_FILE


def gone_map(lines):
    return {l.split()[0]: l.split()[1] for l in lines}


def suite(gen_src, script_src):
    E = []

    def c(name, cond):
        if not cond:
            E.append(name)
    g = load_gen(gen_src)
    # генератор
    rc, lines, err, out = run_gen(g, {"products": {"277303": {}, "rt-5": {}, "12": {}}, "categories": {"zasoby-dlia-prannia": "x", "lialky": "y"}},
                                  live=["product-12.html", "category-lialky.html"])
    c("генератор: рядки «product-<id> gone» / «category-<slug> gone», товари першими", lines == ["product-277303 gone", "product-rt-5 gone", "category-zasoby-dlia-prannia gone"])
    c("генератор: ЖИВА сторінка (product-12, category-lialky) у мапу не йде + гучне попередження", "product-12 " not in " ".join(lines) and "category-lialky" not in " ".join(lines)
      and "product-12.html" in err and "category-lialky.html" in err and "УВАГА" in err)
    c("генератор: rc=0, LF, права 0644", rc == 0 and b"\r" not in out.read_bytes() and (os.name == "nt" or (out.stat().st_mode & 0o777) == 0o644))
    rc, lines, err, _ = run_gen(g, {"products": {"27 73": {}, "../x": {}, "٣٣": {}, "5": {}}, "categories": {"Засоби": "", "a_b": "", "ok-slug": ""}})
    c("генератор: сміттєві ключі (пробіл, ../, не-ASCII цифри, кирилиця, _) пропущено з попередженням", lines == ["product-5 gone", "category-ok-slug gone"] and err.count("поза форматом") == 5)
    rc, lines, err, _ = run_gen(g, raw_text="{not json", preexisting="# old\nproduct-1 gone\n")
    c("генератор: зіпсований JSON → наявну мапу НЕ перезаписано, rc=1", rc == 1 and lines == ["product-1 gone"] and "нечитний" in err)
    rc, lines, err, _ = run_gen(g, raw_text="{}", preexisting="# old\nproduct-1 gone\n")
    c("генератор: ВАЛІДНИЙ порожній {} при непорожній наявній мапі → shrink-guard: мапу НЕ перезаписано, rc=1, попередження ПОРОЖНІЙ", rc == 1 and lines == ["product-1 gone"] and "ПОРОЖНІЙ" in err)
    rc, lines, err, _ = run_gen(g, data={"products": {}, "categories": {}}, preexisting="# old\nproduct-1 gone\n")
    c("генератор: {products:{}, categories:{}} при непорожній мапі → теж shrink-guard (rc=1, мапа ціла)", rc == 1 and lines == ["product-1 gone"])
    rc, lines, err, _ = run_gen(g, data={"products": {}, "categories": {}}, preexisting="# old\n")
    c("генератор: порожнє джерело при ПОРОЖНІЙ наявній мапі → не збій (rc=0, мапа порожня)", rc == 0 and lines == [])
    rc, lines, err, out = run_gen(g, raw_text="[1, 2]")
    c("генератор: JSON не того типу і мапи ще нема → порожня мапа існує (RewriteMap txt: без файлу валить configtest)", rc == 1 and out.exists() and lines == [])
    rc, lines, err, out = run_gen(g, preexisting="# old\nproduct-1 gone\n")
    c("генератор: removed_pages.json ВІДСУТНІЙ → наявну мапу НЕ перезаписано, rc≠0, гучне попередження", rc == 1 and lines == ["product-1 gone"] and "ВІДСУТНІЙ" in err)
    rc, lines, err, out = run_gen(g)
    c("генератор: джерела нема і мапи нема → порожня мапа існує (configtest), rc≠0", rc == 1 and out.exists() and lines == [])
    sent = []
    tn = sys.modules.get("telegram_notify")
    if tn is not None:
        orig = tn.send_throttled_alert
        tn.send_throttled_alert = lambda *a, **k: (sent.append(a) or True)
        run_gen(g)
        run_gen(g, {"products": {"9": {}}}, live=["product-9.html"])
        run_gen(g, {"products": {"9": {}}})
        tn.send_throttled_alert = orig
    c("генератор: збій (нема джерела) і пропуск живої сторінки → тротлений Telegram-алерт; чистий прогін — без алерту",
      tn is not None and len(sent) == 2 and all(a[0] == "site_gone_map" for a in sent) and "ВІДСУТНІЙ" in sent[0][1] and "product-9.html" in sent[1][1])
    # правило Apache
    maps = {"sitegone": gone_map(["product-277303 gone", "product-rt-5 gone", "category-zasoby-dlia-prannia gone"]), "promredir": {"777": "/product-1.html"}, "grpredir": {}}
    sim = lambda p: simulate(script_src, p, maps)  # noqa: E731
    c("правило: /product-277303.html → 410", sim("/product-277303.html") == 410)
    c("правило: /product-rt-5.html → 410", sim("/product-rt-5.html") == 410)
    c("правило: /category-zasoby-dlia-prannia.html і _2/_15 → 410", all(sim(p) == 410 for p in ("/category-zasoby-dlia-prannia.html", "/category-zasoby-dlia-prannia_2.html",
                                                                                                 "/category-zasoby-dlia-prannia_15.html")))
    c("правило: живі адреси поза мапою (товар, схожий id 2773030/27730, категорія, каталог) НЕ 410",
      all(sim(p) == 200 for p in ("/product-1.html", "/product-2773030.html", "/product-27730.html", "/category-lialky.html", "/category-lialky_2.html", "/catalog.html", "/")))
    c("правило: хвости поза шаблоном (.htmlx, /product-277303.html/x) не 410", sim("/product-277303.htmlx") == 200 and sim("/product-277303.html/x") == 200)
    c("правило: наявні Prom-правила не зачеплено (покритий p → 301, непокритий → 410, create_order_mobile → 410)",
      sim("/ua/p777-x.html") == 301 and sim("/ua/p999-x.html") == 410 and sim("/create_order_mobile") == 410)
    c("правило: порожня мапа sitegone → жодного 410 для /product-/category-", simulate(script_src, "/product-277303.html", {"promredir": {}}) == 200)
    # скрипт активації (статично)
    tpl = re.search(r'RewriteMap sitegone "txt:@APP@/([a-z_]+\.map)"', script_src)
    var = re.search(r'^GONEMAP="\$APP/([a-z_]+\.map)"$', script_src, re.M)
    c("скрипт: файл мапи в шаблоні vhost = GONEMAP = файл генератора", bool(tpl and var) and tpl.group(1) == var.group(1) == g.BASE.joinpath(tpl.group(1)).name
      and str(g.OUT_FILE) != "" and 'BASE / "' + tpl.group(1) + '"' in gen_src)
    c("скрипт: генератор викликається ПІСЛЯ build_site.py, ДО запису vhost'а", 0 < script_src.find('"$PY" "$APP/site/build_site.py"') < script_src.find('"$PY" "$APP/site_gone_pages.py"') < script_src.find("write_vhost http ||"))
    c("скрипт: мапи нема → кладемо порожню; Apache-користувач мусить її читати (інакше die)",
      'if [ ! -e "$GONEMAP" ]; then' in script_src and 'can_read "$GONEMAP" || die' in script_src)
    c("скрипт: проби 410 (товар, категорія, _2, прохід по всій мапі) і контроль 404 ставлять bad=1",
      "віддає $sg_p, а не 410\"; bad=1; }" in script_src and "(_2) віддає $sg_c ($sg_c2), а не 410\"; bad=1; }" in script_src
      and "зачепило чужу адресу?)\"; bad=1; }" in script_src and 'адрес(и) мапи 410 віддають не 410"; bad=1; fi' in script_src and 'done < "$GONEMAP"' in script_src)
    c("скрипт: прохід по всіх адресах мапи — по 3 спроби на адресу (тимчасовий збій не відкочує активацію)", "for sg_try in 1 2 3; do" in script_src)
    c("скрипт: 410 (sitegone) стоїть ПЕРЕД 301 злиття категорій (catmoved): знята назавжди категорія не редиректиться",
      "RewriteMap catmoved" not in script_src or script_src.find("RewriteMap sitegone") < script_src.find("RewriteMap catmoved"))
    return E


base_failures = suite(GEN_SRC, SCRIPT_SRC)
for n in base_failures:
    print(f"[FAIL] {n}")
chk(f"генератор + правило Apache + скрипт активації ({'усе зелене' if not base_failures else str(len(base_failures)) + ' провалів'})", not base_failures)

# ── дані: site/removed_pages.json ─────────────────────────────────────────────────────────────────────────────────────────────────
import build_site as bs  # noqa: E402
prods, cats = SRC.get("products", {}), SRC.get("categories", {})
chk("джерело: 161 товар хімії (50 у наявності + 111 сторінок «немає в наявності», живий каталог 06.11) і 5 категорій", len(prods) == 161 and len(cats) == 5)
chk("джерело: КОЖЕН товар заборонений is_forbidden (за сирою категорією Toysi або точковим pid)",
    all(fp.is_forbidden({"id": pid, "category_name": r.get("category")}) for pid, r in prods.items()))
chk("джерело: КОЖНА категорія — у FORBIDDEN_TOYSI_CATEGORIES, slug = slugify(назви)",
    all(n.strip().lower() in fp.FORBIDDEN_TOYSI_CATEGORIES and bs.slugify(n) == s for s, n in cats.items()) and sorted(cats.values()) == sorted(CHEM_CATS))
chk("джерело: є 277303 (контроль SEO) і три точкові 279732/310330/310331", {"277303", "279732", "310330", "310331"} <= set(prods))
chk("джерело: НЕ-хімії немає (освіжувач-«Літак» 299076-8, пігмент rt-62870/62872/62873)", not ({"299076", "299077", "299078", "rt-62870", "rt-62872", "rt-62873"} & set(prods)))
chk("джерело: звільнені FORBIDDEN_EXEMPT_PRODUCT_IDS (280754 — дитячий шампунь) відсутні (410 не переживе звільнення)",
    "280754" in fp.FORBIDDEN_EXEMPT_PRODUCT_IDS and not (set(fp.FORBIDDEN_EXEMPT_PRODUCT_IDS) & set(prods)))
_moves = HERE / "site" / "category_moves.json"
_mv = {k for k in json.loads(_moves.read_text(encoding="utf-8")) if not k.startswith("_")} if _moves.exists() else set()
_mv |= {v for k, v in (json.loads(_moves.read_text(encoding="utf-8")).items() if _moves.exists() else []) if not k.startswith("_") and isinstance(v, str)}
chk("джерело: жодна знята категорія не є ні старим slug, ні приймачем у site/category_moves.json", not (set(cats) & _mv))
chk("джерело: формат ключів прийме генератор (жоден запис не загубиться)", all(re.fullmatch(r"[0-9]+", p) for p in prods) and all(re.fullmatch(r"[a-z0-9-]+", s) for s in cats))

# ── конфіги більше не посилаються на зниклі категорії й товари ───────────────────────────────────────────────────────────────────
slugs, ids = set(cats), set(prods)
groups = json.loads((HERE / "site" / "category_groups.json").read_text(encoding="utf-8"))["groups"]
chk("category_groups.json: жодної з 5 категорій хімії серед підкатегорій", not ({s for g in groups for s in g["subs"]} & set(CHEM_CATS)))
pri = json.loads((HERE / "site" / "sitemap_priority.json").read_text(encoding="utf-8"))
chk("sitemap_priority.json: жодного товару хімії (було 281803, 283888)", not (set(map(str, pri.get("product_ids", []))) & ids))
for fn in ("category_related.json", "category_texts.json", "category_ru_alt.json", "category_overrides.json", "demand_featured.json", "ig_featured.json", "seasonal_halloween.json"):
    t = (HERE / "site" / fn).read_text(encoding="utf-8-sig")
    hit = [s for s in slugs if re.search(r"(?<![a-z0-9-])" + re.escape(s) + r"(?![a-z0-9-])", t)] + [n for n in CHEM_CATS if n in t] \
        + ([i for i in ids if re.search(r'"' + i + r'"', t)] if fn != "category_overrides.json" else [i for i in ids if re.search(r'"' + i + r'"\s*:', t)])
    chk(f"site/{fn}: посилань на 5 категорій / 161 товар хімії немає", not hit)
pg = json.loads((HERE / "prom_group_to_category.json").read_text(encoding="utf-8"))
chk("prom_group_to_category.json: жодна група Prom не веде на категорію хімії (інакше 301 на зниклу сторінку)", not any((v or {}).get("category") in slugs for v in pg.values() if isinstance(v, dict)))
ov = json.loads((HERE / "prom_redirect_overrides.json").read_text(encoding="utf-8"))
chk("prom_redirect_overrides.json: жодне перекриття не веде на категорію хімії", not any(isinstance(v, dict) and v.get("target") in {f"/category-{s}.html" for s in slugs} for v in ov.values()))

# ── деплой: site-rebuild.service, .gitignore ─────────────────────────────────────────────────────────────────────────────────────
svc = (HERE / "deploy" / "site-rebuild.service").read_text(encoding="utf-8")
chk("site-rebuild.service: після збірки оновлює мапу 410 (не-фатально)", "ExecStartPost=-/opt/plutustoys/venv/bin/python3 /opt/plutustoys/site_gone_pages.py" in svc
    and svc.find("build_site.py") < svc.find("site_gone_pages.py"))
chk(".gitignore: site_gone_apache.map не в git", "site_gone_apache.map" in (HERE / ".gitignore").read_text(encoding="utf-8").splitlines())

# ── збірка сайту на синтетичному каталозі ────────────────────────────────────────────────────────────────────────────────────────
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None
DESC = "Опис товару достатньої довжини для картки сайту, понад сорок символів."


def toysi(pid, name, cat, stock=5, price=300):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": stock, "category_name": cat, "category_id": "10",
            "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "description": DESC, "params": {}, "url": "x"}


catalog, chem_ids = {}, set()
n = 900001
for cat in CHEM_CATS:
    for i in range(4):
        catalog[str(n)] = toysi(n, f"{cat} засіб {i}", cat, stock=0 if i == 3 else 5)   # 3 у наявності + 1 «немає в наявності»
        chem_ids.add(str(n))
        n += 1
for pid, cat in (("279732", "Догляд за тілом та волоссям"), ("310330", "Уцінка"), ("310331", "Уцінка")):
    catalog[pid] = toysi(pid, f"MAXI POWER / SILA засіб {pid}", cat)
    chem_ids.add(pid)
keep = {}
for cat, cnt in (("Ляльки", 8), ("Машинки", 8), ("Догляд за тілом та волоссям", 6), ("Набори для прибирання", 6), ("Іграшки антистрес", 5), ("Уцінка", 5), ("Догляд за тілом", 6), ("Догляд за волоссям", 4)):
    for i in range(cnt):
        catalog[str(n)] = toysi(n, f"{cat} товар {i}", cat, price=200 + i)
        keep[str(n)] = cat
        n += 1
catalog["280754"] = toysi("280754", "Карапуз шампунь дитячий 250мл слоненя", "Догляд за будинком")   # виняток: косметика в категорії хімії лишається
keep["280754"] = "Догляд за будинком"
catalog["299076"] = toysi("299076", 'Освіжувач повітря "Літак" на сонячній батареї 10 см (ЧЕРВОНИЙ)', "Іграшки антистрес")
keep["299076"] = "Іграшки антистрес"
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
out = Path(tempfile.mkdtemp())
for pid in sorted(chem_ids)[:4]:                                   # залишки попередньої збірки (до заборони) — крок 8 мусить прибрати
    (out / f"product-{pid}.html").write_text("old", encoding="utf-8")
for c in CHEM_CATS:
    (out / f"category-{bs.slugify(c)}.html").write_text("old", encoding="utf-8")
(out / f"category-{bs.slugify(CHEM_CATS[0])}_2.html").write_text("old", encoding="utf-8")
bs.OUT = str(out)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()

chem_slugs = {bs.slugify(c) for c in CHEM_CATS}
files = {p.name: p for p in out.iterdir() if p.is_file()}
chk("збірка: жодного файлу product-<хімія>.html (ні в наявності, ні «немає в наявності»; старі прибрано кроком 8)", not any(f"product-{i}.html" in files for i in chem_ids))
chk("збірка: жодного файлу category-<хімія>[ _N].html (старі прибрано кроком 8)", not any(re.fullmatch(r"category-(.+?)(?:_\d+)?\.html", f) and re.fullmatch(r"category-(.+?)(?:_\d+)?\.html", f).group(1) in chem_slugs for f in files))
idx_all = []
for fn in ("index.json", "index-t.json", "index-rt.json"):
    if fn in files:
        idx_all += json.loads(files[fn].read_text(encoding="utf-8"))
chk("збірка: index.json / index-t.json — жодного товару хімії, жодної категорії хімії", not ({str(x.get("id")) for x in idx_all} & chem_ids) and not ({x.get("c") for x in idx_all} & set(CHEM_CATS)))
chk("збірка: суміжне лишилось (280754 шампунь-виняток, 299076 «Літак», «Набори для прибирання», «Догляд за тілом») — сторінки є", all(f"product-{p}.html" in files for p in keep))
big = ""
for fn, p in files.items():
    if fn.endswith((".html", ".xml", ".json", ".txt")):
        big += p.read_text(encoding="utf-8", errors="replace")
chk("збірка: шампунь-виняток 280754 у наявності переїхав у «Догляд за волоссям» (category_overrides) — категорія хімії через нього НЕ воскресає",
    any(str(x.get("id")) == "280754" and x.get("c") == "Догляд за волоссям" for x in idx_all))
chk("збірка: у ЖОДНОМУ HTML/XML/JSON нема посилання на сторінку товару хімії", not any(f"product-{i}.html" in big for i in chem_ids))
chk("збірка: у ЖОДНОМУ HTML/XML/JSON нема посилання на сторінку категорії хімії (групи, хаб, чипи, сайдбар, sitemap)", not any(f"category-{s}.html" in big or f"category-{s}_" in big for s in chem_slugs))
chk("збірка: назви 5 категорій хімії ніде не показано (фільтр, хлібні крихти, чипи)", not any(f">{c}<" in big or f'"{c}"' in big for c in CHEM_CATS))
# мапа 410 на зібраній теці: усі 161 + 5 (їх сторінок немає), а сторінка, що жива (299076), туди не пройшла б
g = load_gen(GEN_SRC)
g.SITE_DIR, g.SOURCE_JSON, g.OUT_FILE = out, HERE / "site" / "removed_pages.json", Path(tempfile.mkdtemp()) / "gone.map"
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    rc = g.main()
gl = [l for l in g.OUT_FILE.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
chk("мапа 410 з реального removed_pages.json: 161 товар + 5 категорій, rc=0", rc == 0 and len(gl) == 166 and sum(l.startswith("category-") for l in gl) == 5)
chk("мапа 410 → симулятор: кожна адреса мапи дає 410, жива сторінка (299076, Ляльки) — ні",
    all(simulate(SCRIPT_SRC, f"/{l.split()[0]}.html", {"sitegone": gone_map(gl)}) == 410 for l in gl)
    and simulate(SCRIPT_SRC, "/product-299076.html", {"sitegone": gone_map(gl)}) == 200
    and simulate(SCRIPT_SRC, f"/category-{bs.slugify('Ляльки')}.html", {"sitegone": gone_map(gl)}) == 200)

# ── МУТАЦІЇ ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
RULE = "    RewriteRule ^/((?:product|category)-[a-z0-9-]+)(?:_[0-9]+)?\\.html$ - [G,L]\n"
MUTATIONS = [
    ("R1 правило без умови «ключ є в мапі» (410 для ВСІХ товарів)", "script", lambda s: mutate(s, "    RewriteCond ${sitegone:$1|-} !=-\n", "")),
    ("R2 без пагінації _N (category-slug_2.html лишився б 404)", "script", lambda s: mutate(s, RULE, RULE.replace("(?:_[0-9]+)?", ""))),
    ("R3 правило без $-якоря (хвіст .htmlx теж 410)", "script", lambda s: mutate(s, RULE, RULE.replace("\\.html$", "\\.html"))),
    ("R4 лише product- (категорії → 404)", "script", lambda s: mutate(s, RULE, RULE.replace("(?:product|category)", "product"))),
    ("R5 інший файл мапи в шаблоні, ніж GONEMAP", "script", lambda s: mutate(s, 'RewriteMap sitegone "txt:@APP@/site_gone_apache.map"', 'RewriteMap sitegone "txt:@APP@/site_gone.map"')),
    ("R6 проба товару не ставить bad=1", "script", lambda s: mutate(s, "віддає $sg_p, а не 410\"; bad=1; }", "віддає $sg_p, а не 410\"; }")),
    ("R7 генератор не викликається у скрипті", "script", lambda s: mutate(s, '"$PY" "$APP/site_gone_pages.py" ||', 'true ||')),
    ("G1 генератор пускає живу сторінку в мапу", "gen", lambda s: mutate(s, '        if (site_dir / f"product-{pid}.html").exists():', "        if False:")),
    ("G2 генератор при зіпсованому JSON перезаписує мапу порожньою", "gen", lambda s: mutate(s, "        if not OUT_FILE.exists():\n            _write_atomic(OUT_FILE, HEADER)\n        _alert(",
                                                                                            "        _write_atomic(OUT_FILE, HEADER)\n        _alert(")),
    ("G4 відсутній removed_pages.json тихо стирає мапу", "gen", lambda s: mutate(s, '        _warn(f"{path} ВІДСУТНІЙ (не задеплоєно? видалено?) — мапу 410 не оновлюю, наявна лишається")\n        return None',
                                                                       "        return [], []")),
    ("G6 shrink-guard вимкнено (порожнє джерело стирає наявну мапу)", "gen", lambda s: mutate(s, "    if not src[0] and not src[1]:", "    if False:")),
    ("G5 генератор не шле алерт", "gen", lambda s: mutate(s, "        send_throttled_alert(\"site_gone_map\"", "        (lambda *a, **k: None)(\"site_gone_map\"")),
    ("R8 прохід по мапі без повторних спроб", "script", lambda s: mutate(s, "for sg_try in 1 2 3; do", "for sg_try in 3; do")),
    ("G3 генератор приймає будь-який id (без fullmatch)", "gen", lambda s: mutate(s, "if isinstance(pid, str) and PRODUCT_ID_RE.fullmatch(pid):", "if isinstance(pid, str):")),
]
survived = []
for name, kind, fn in MUTATIONS:
    gsrc, ssrc = GEN_SRC, SCRIPT_SRC
    if kind == "gen":
        gsrc = fn(GEN_SRC)
    else:
        ssrc = fn(SCRIPT_SRC)
    try:
        fails = suite(gsrc, ssrc)
    except Exception as e:  # noqa: BLE001 — виняток у мутанта теж «червоний»
        fails = [f"виняток {type(e).__name__}: {str(e)[:80]}"]
    print(f"[{'OK ' if fails else 'FAIL'}] мутація «{name}» → {'набір ЧЕРВОНИЙ (' + str(len(fails)) + ')' if fails else 'набір ЗЕЛЕНИЙ — тест нічого не ловить!'}")
    if not fails:
        survived.append(name)

allf = F + survived
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(allf) if allf else f'✅ зняті сторінки (хімія 06.11): джерело, конфіги, генератор, правило 410, проби, збірка — коректні; усі {len(MUTATIONS)} мутацій червоніють.'}")
sys.exit(1 if allf else 0)
