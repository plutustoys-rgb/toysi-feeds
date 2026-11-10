#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_atomic_write.py — 10.11.2026 (SEO): сторінки/службові файли збірки пишуться АТОМАРНО.
open(path, "w") обнуляє файл ще до запису: у цю мить Apache віддає порожню/обірвану сторінку з кодом 200 (для краулера гірше за 404).
Інваріанти:
  • цільовий файл збірки НІКОЛИ не відкривається на запис напряму ("w") — лише тимчасовий сусідній файл + os.replace;
  • під час запису новий вміст з'являється цілим, старий до того лишається цілим (читач не бачить порожнього файлу);
  • збій запису або заміни: старий вміст на місці, тимчасового файлу не лишається;
  • тимчасовий файл не закінчується на .html/.xml/.json (не потрапить у sitemap, чистку збірки чи віддачу);
  • те саме для HTML-гілки _write (з lastmod-станом) і для решти файлів (index.json, sitemap*.xml, robots.txt)."""
import builtins
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "site"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import build_site as b  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


out = tempfile.mkdtemp()
b.OUT = out


def read(name):
    with open(os.path.join(out, name), encoding="utf-8") as f:
        return f.read()


# ── цільовий файл не відкривається на запис напряму
opened_w = []
real_open = builtins.open
real_replace = os.replace
replaces = []


def spy_open(file, mode="r", *a, **k):
    if "w" in mode or "a" in mode or "+" in mode:
        opened_w.append(os.path.abspath(str(file)))
    return real_open(file, mode, *a, **k)


def spy_replace(src, dst):
    replaces.append((os.path.abspath(str(src)), os.path.abspath(str(dst))))
    return real_replace(src, dst)


builtins.open = spy_open
os.replace = spy_replace
try:
    b._write("product-1.html", "<html>A</html>")          # новий HTML
    b._write("product-1.html", "<html>B</html>")          # змінений HTML
    b._write("product-1.html", "<html>B</html>")          # без змін
    b._write("sitemap-x.xml", "<urlset>1</urlset>")       # не HTML
    b._write("sitemap-x.xml", "<urlset>2</urlset>")
    b._write_text_atomic(os.path.join(out, "index.json"), "[1]")
finally:
    builtins.open = real_open
    os.replace = real_replace

targets = {os.path.abspath(os.path.join(out, n)) for n in ("product-1.html", "sitemap-x.xml", "index.json")}
chk("жоден цільовий файл не відкривався на запис напряму", not (set(opened_w) & targets))
chk("кожен запис — через os.replace(тимчасовий -> цільовий)", len(replaces) == 5 and all(dst in targets and src != dst for src, dst in replaces))
chk("тимчасові імена не закінчуються на .html/.xml/.json і лежать у тій самій теці (os.replace атомарний лише в межах тому)",
    all(not src.endswith((".html", ".xml", ".json")) and os.path.dirname(src) == os.path.dirname(dst) for src, dst in replaces))
chk("вміст після записів правильний (HTML: останній; xml: останній; json)", read("product-1.html") == "<html>B</html>" and read("sitemap-x.xml") == "<urlset>2</urlset>" and read("index.json") == "[1]")
chk("тимчасових файлів не лишилось", sorted(os.listdir(out)) == ["index.json", "product-1.html", "sitemap-x.xml"])

# ── під час запису старий файл цілий (читаємо цільовий у момент, коли тимчасовий уже записано, а заміни ще нема)
seen = []


def replace_probe(src, dst):
    seen.append((read_target_raw(dst), real_open(src, encoding="utf-8").read()))
    return real_replace(src, dst)


def read_target_raw(dst):
    with real_open(dst, encoding="utf-8") as f:
        return f.read()


os.replace = replace_probe
try:
    b._write("sitemap-x.xml", "<urlset>3</urlset>")
finally:
    os.replace = real_replace
chk("у момент заміни цільовий файл ще має СТАРИЙ вміст цілим, а новий уже повністю записаний", seen == [("<urlset>2</urlset>", "<urlset>3</urlset>")] and read("sitemap-x.xml") == "<urlset>3</urlset>")

# ── збій заміни: старий на місці, тимчасового нема
def boom(src, dst):
    raise OSError("диск")


os.replace = boom
raised = False
try:
    b._write("sitemap-x.xml", "<urlset>4</urlset>")
except OSError:
    raised = True
finally:
    os.replace = real_replace
chk("збій os.replace: виняток піднято, старий вміст цілий, тимчасового файлу нема", raised and read("sitemap-x.xml") == "<urlset>3</urlset>" and sorted(os.listdir(out)) == ["index.json", "product-1.html", "sitemap-x.xml"])

# ── збій під час запису вмісту (напр. не-UTF-8 символ): старий цілий, тимчасового нема
raised = False
try:
    b._write("product-1.html", "<html>\ud800</html>")   # одиночний сурогат не кодується в UTF-8
except UnicodeEncodeError:
    raised = True
chk("збій кодування під час запису: старий HTML цілий, тимчасового файлу нема", raised and read("product-1.html") == "<html>B</html>" and sorted(os.listdir(out)) == ["index.json", "product-1.html", "sitemap-x.xml"])

# ── чистка залишків: свіжий тимчасовий файл (паралельна збірка) лишається, старий (>10 хв) прибирається, легітимні файли з крапкою — ні
_fresh = os.path.join(out, ".sitemap-y.xml.tmp111")
_old = os.path.join(out, ".sitemap-z.xml.tmp222")
_keep = os.path.join(out, ".htaccess")
for _f in (_fresh, _old, _keep):
    with real_open(_f, "w") as _h:
        _h.write("x")
os.utime(_old, (os.path.getmtime(_old) - 3600, os.path.getmtime(_old) - 3600))
_n = b._remove_stale_tmp(out)
chk("чистка залишків: старий (>10 хв) прибрано, свіжий (паралельна збірка) і .htaccess лишились", _n == 1 and not os.path.exists(_old) and os.path.exists(_fresh) and os.path.exists(_keep))
os.remove(_fresh); os.remove(_keep)

# ── у вихідному коді не лишилось прямих open(<файл збірки>, "w")
src_txt = real_open(os.path.join(HERE, "site", "build_site.py"), encoding="utf-8").read()
import re  # noqa: E402
_code_lines = [ln for ln in src_txt.splitlines() if not ln.lstrip().startswith(("#", '"""'))]   # без коментарів і рядків докстрінгів
direct = re.findall(r'open\(\s*(?:path|os\.path\.join\(OUT[^)]*\))\s*,\s*"w"', chr(10).join(_code_lines))
chk("у build_site.py нема прямого open(path | OUT/…, \"w\") — лишився лише тимчасовий файл у _write_text_atomic", not direct)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
