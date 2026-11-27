# -*- coding: utf-8 -*-
"""Регрес (SEO 27.11, власник погодив «Google Відгуки клієнтів»): модуль опитування Google Customer Reviews на thanks.html.
1) Ворота SITE_GCR_LIVE (за замовчуванням ВИМКНЕНО): без них у cart.html/thanks.html нема window.PT_GCR і жодного сліду Google-скрипта (у HTML); з ворітьми — window.PT_GCR з merchant_id 5824264949.
2) app.js: email зберігається лише для накладеного платежу й лише коли є window.PT_GCR; на thanks.html зчитується й одразу видаляється; delivery_country UA; скрипт Google вантажиться лише в цьому блоці.
3) Очікувана дата доставки (gcrEta, Node): відправка того ж робочого дня до 12:00 (Київ), інакше наступного робочого; далі 3 робочі дні (верхня межа «1–3 робочі дні»); вихідні → з понеділка."""
import contextlib, io, json, re, subprocess, sys, tempfile
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

chk("ворота за замовчуванням ВИМКНЕНІ (env SITE_GCR_LIVE не заданий у тесті)", bs.SITE_GCR_LIVE is False)
chk("merchant_id із запиту SEO", bs.GCR_MERCHANT_ID == 5824264949)
chk("строки доставки з картки товару: до 12:00, 3 робочі дні", bs.GCR_CUTOFF_HOUR == 12 and bs.GCR_MAX_TRANSIT_DAYS == 3 and "до 12:00" in (HERE / "site" / "build_site.py").read_text(encoding="utf-8") and "1–3 робочі дні" in (HERE / "site" / "build_site.py").read_text(encoding="utf-8"))

def build_pages(live):
    bs.SITE_GCR_LIVE = live
    out = Path(tempfile.mkdtemp()); bs.OUT = str(out)
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        bs.write_cart(); bs.write_thanks()
    return (out / "cart.html").read_text(encoding="utf-8"), (out / "thanks.html").read_text(encoding="utf-8")

cart0, thanks0 = build_pages(False)
chk("вимкнено: у cart.html і thanks.html нема window.PT_GCR і посилань на Google-скрипт", all("PT_GCR" not in x and "apis.google.com" not in x and "surveyoptin" not in x for x in (cart0, thanks0)))
cart1, thanks1 = build_pages(True)
m = re.search(r"<script>window\.PT_GCR=(\{.*?\});</script>", thanks1)
cfg = json.loads(m.group(1)) if m else {}
chk("увімкнено: thanks.html має window.PT_GCR з merchant_id/cutoff/days", cfg == {"merchant_id": 5824264949, "cutoff": 12, "days": 3})
chk("увімкнено: cart.html теж має PT_GCR (щоб checkout зберіг email лише тоді)", "window.PT_GCR=" in cart1)
chk("thanks.html лишається noindex і без Google-скрипта в HTML (скрипт додає лише app.js за умов)", 'noindex' in thanks1 and "apis.google.com" not in thanks1)
bs.SITE_GCR_LIVE = False

js = (HERE / "site" / "assets" / "app.js").read_text(encoding="utf-8")
chk("app.js: email зберігається лише коли window.PT_GCR і не prepaid, інакше видаляється", re.search(r'window\.PT_GCR && payment!=="prepaid"[^;]*setItem\("pt_last_email"', js) is not None and 'removeItem("pt_last_email")' in js)
chk("app.js: на thanks.html email зчитується і одразу видаляється; країна UA; завантаження Google-скрипта лише тут", js.count("apis.google.com") == 1 and 'delivery_country:"UA"' in js and re.search(r'getItem\("pt_last_email"\)\|\|""; sessionStorage\.removeItem\("pt_last_email"\)', js) is not None)
chk("app.js: опитування лише з номером замовлення і email (умова gcrEm && gcrId)", "gcrEm && gcrId" in js)
chk("app.js: merchant_id перевіряється як число перед використанням", r'/^\d+$/.test(String(window.PT_GCR.merchant_id))' in js)

mm = re.search(r"/\*GCR-ETA-START\*/(.*?)/\*GCR-ETA-END\*/", js, re.S)
chk("app.js: функція gcrEta знайдена", mm is not None)
if mm:
    src = mm.group(1) + "\nconst T=[[2026,11,27,10],[2026,12,2,10],[2026,12,2,15],[2026,12,4,13],[2026,12,5,10],[2026,12,6,10],[2026,12,31,9],[2026,12,31,13]];\nconsole.log(JSON.stringify(T.map(a=>gcrEta(a[0],a[1],a[2],a[3],12,3))));"
    try:
        r = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
        got = json.loads(r.stdout.strip())
    except Exception as e:
        got = None
        print("node:", e)
    want = ["2026-12-02",   # пт 27.11 10:00: відправка пт, +3 роб. дні = ср 02.12
            "2026-12-07",   # ср 02.12 10:00: відправка ср, +3 = пн 07.12
            "2026-12-08",   # ср 02.12 15:00 (після 12:00): відправка чт 03.12, +3 = вт 08.12
            "2026-12-10",   # пт 04.12 13:00: відправка пн 07.12, +3 = чт 10.12
            "2026-12-10",   # сб 05.12: відправка пн 07.12, +3 = чт 10.12
            "2026-12-10",   # нд 06.12: те саме
            "2027-01-06",   # чт 31.12 09:00: відправка чт, +3 = пн 04.01 (пт 01.01 — робочий у календарі НП-логіки без свят: пт 01.01, пн 04.01, вт 05.01) → вт 05.01?
            "2027-01-06"]
    # перерахунок для 31.12 (чт) без урахування свят: чт 31.12 → +3 роб. = пт 01.01, пн 04.01, вт 05.01
    want[6] = "2027-01-05"
    # чт 31.12 13:00: відправка пт 01.01, +3 = пн 04.01, вт 05.01, ср 06.01
    want[7] = "2027-01-06"
    chk(f"gcrEta: дати очікуваної доставки (Node) {got}", got == want)

# ── Політика конфіденційності: пункт про Google (текст погоджено власником 27.11) — лише з воротами ──
priv_off = bs._privacy_with_gcr(False); priv_on = bs._privacy_with_gcr(True)
chk("privacy вимкнено: без пункту про Google і без службового маркера; Checkbox закінчується крапкою, як було", "Google Відгуки клієнтів" not in priv_off and "<!--GCR-->" not in priv_off and "якщо чек оформлюється.</li>\n</ul>" in priv_off)
GCR_TXT = "<b>Google</b> (програма «Google Відгуки клієнтів») — адреса електронної пошти, номер замовлення, країна та очікувана дата доставки, лише якщо ви вказали email і погодилися на опитування у вікні Google після оформлення замовлення. Google надішле лист із проханням оцінити магазин. Участь добровільна."
_gi = priv_on.find(GCR_TXT)
chk("privacy увімкнено: пункт дослівно, у списку «Кому ми передаємо дані», останнім перед «Ми не продаємо ваші дані…»", _gi > 0 and priv_on.index("Кому ми передаємо дані") < _gi and "<!--GCR-->" not in priv_on and priv_on.index("</ul>", _gi) < priv_on.index("Ми не продаємо ваші дані"))
_tp = {t[0]: t[2] for t in bs.TRUST_PAGES}
chk("TRUST_PAGES: privacy.html за замовчуванням (ворота вимкнені) без пункту про Google", "Google Відгуки клієнтів" not in _tp["privacy.html"])

print("\nALL OK" if not F else f"\nFAIL: {len(F)}")
sys.exit(1 if F else 0)
