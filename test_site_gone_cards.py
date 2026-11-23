# -*- coding: utf-8 -*-
"""Регрес (SEO 23.11): видалені власні картки (product-*.html, файлу нема у site/) → 410 Gone, а не 404. Apache тут не запускається:
правило береться З ТЕКСТУ activate_site_apache.sh, патерн перевіряється Python-regex; порядок — рядки скрипта."""
import re, sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

sh = (Path(__file__).parent / "deploy" / "activate_site_apache.sh").read_text(encoding="utf-8")
lines = sh.splitlines()
rule = next((i for i, l in enumerate(lines) if l.strip() == r"RewriteRule ^/(product-[A-Za-z0-9_-]+)\.html$ - [G,L]"), -1)
chk("правило 410 для видалених карток є в скрипті", rule > 0)
chk("перед ним умова «файлу нема»: RewriteCond %{DOCUMENT_ROOT}/$1.html !-f (жива сторінка, в т.ч. «немає в наявності», віддається як є)",
    lines[rule - 1].strip() == "RewriteCond %{DOCUMENT_ROOT}/$1.html !-f")
rt = next(i for i, l in enumerate(lines) if l.strip().startswith("RewriteRule ^/product-rt-"))
sg = next(i for i, l in enumerate(lines) if "RewriteRule ^/((?:product|category)-" in l)
chk("порядок: 301 product-rt-X→product-yX і 410 з мапи removed_pages — ДО загального 410 (інакше rt-адреса з готовою y-сторінкою дістала б 410 замість 301)", sg < rt < rule)
pat = re.compile(r"^/(product-[A-Za-z0-9_-]+)\.html$")
for u in ("/product-300946.html", "/product-x59309.html", "/product-x56639.html", "/product-yAB_1.html", "/product-rt-12.html", "/product-k123.html"):
    chk(f"патерн ловить {u}", bool(pat.match(u)))
for u in ("/product-1.html/extra", "/category-x.html", "/productx.html", "/assets/product-1.html", "/product-1.jpg"):
    chk(f"патерн НЕ ловить {u}", not pat.match(u))
chk("ErrorDocument 410 /404.html: людина за старим посиланням бачить нашу сторінку, статус лишається 410", re.search(r"^\s*ErrorDocument 410 /404\.html\s*$", sh, re.M) is not None)
chk("післяактиваційна проба: «чужа» адреса — категорія поза мапою → 404 (НЕ product-…, його бере загальне 410), а product-999999999999 очікує 410",
    re.search(r"sg_ctl=\$\(code[^\n]*category-kontrol", sh) is not None and not re.search(r"sg_ctl=\$\(code[^\n]*product-999999999999", sh)
    and "sg_del=$(code" in sh and '"$sg_del" = "410"' in sh)
# проба ЖИВОЇ картки (аудит #2042): без неї хибний `!-f` віддав би 410 усім карткам, а скрипт не побачив би й не відкотив
LIVE = r'lv_code=\$\(code[^\n]*\$lv_name"\)'
chk("проба живої картки: код береться тією ж функцією code з $lv_name", re.search(LIVE, sh) is not None)
chk("проба живої картки вимагає 200 і ставить bad=1", re.search(r'\[ "\$lv_code" = "200" \] \|\| \{[^\n]*bad=1;', sh) is not None)
chk("проба живої картки — product-[0-9]*.html (не rt-/kv-/y-: вони можуть бути 301/тінню)", 'site/product-[0-9]*.html' in sh)
chk("проба живої картки без ls|head (pipefail); коли файлів нема — лише попередження, не bad",
    "ls " not in sh[sh.index("local lv_name"):sh.index("жива картка: /$lv_name")] and "пробу пропущено" in sh)
chk("проба живої картки — у тій самій фазі, що й sg_del (ДО `if bad=1 → restore`, тобто автовідкат спрацює)",
    sh.index('sg_del=$(code') < sh.index("lv_code=$(code") < sh.index('if [ "$bad" = "1" ]; then restore; return 1; fi'))
chk("друк ручної команди відкату після успішної активації (cp $bak $VHOST && apachectl -t && graceful)",
    'ручний відкат: cp $bak $VHOST && $APACHECTL -t && $APACHECTL graceful' in sh
    and sh.index("ручний відкат: cp") > sh.index("✅ vhost ($mode) застосовано"))
# мутація: прибрати пробу (перейменувати змінну коду) — перевірка наявності мусить почервоніти
mut = sh.replace('lv_code=$(code', 'lv_gone=$(code')
chk("мутація «прибрати пробу живої картки» червоніє", re.search(LIVE, mut) is None and re.search(LIVE, sh) is not None)
print("\nALL OK" if not F else f"\nFAIL: {len(F)}")
sys.exit(1 if F else 0)
