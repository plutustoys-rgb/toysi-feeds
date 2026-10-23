#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_health_check.py — машинна перевірка сайту очима покупця (site_health_check.py, 16.10.2026).
Інваріанти: (1) норма: усе 200, картки з JSON-LD і збіжною ціною, сертифікат 79 дн. → 0 порушень; (2) кожна поломка дає порушення: не-200 головної, index.json не JSON/порожній, картка 404, нема price/availability, ціна ≠ index.json (>1 ₴), сертифікат <14 дн./нечитабельний, api/np/city не 200;
(3) вибірка карток детермінована від дати; (4) запис у канал: один на добу, перед першим рядком, що ПОЧИНАЄТЬСЯ з «## » (блок-цитата не ламається), нема каналу → False. `python test_site_health_check.py` → exit 0/1."""
import json
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import site_health_check as sh  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


sh.time.sleep = lambda x: None      # без реальних пауз повтору
INDEX = [{"id": str(i), "pr": 100 + i, "n": f"т{i}"} for i in range(1, 40)]


def page(pid, price=None, avail="https://schema.org/InStock"):
    price = 100 + int(pid) if price is None else price
    ld = {"@context": "https://schema.org", "@type": "Product", "offers": {"@type": "Offer", "price": str(price), "availability": avail}}
    return "<html><script type=\"application/ld+json\">" + json.dumps(ld) + "</script></html>"


def make_fetcher(**over):
    def f(url, timeout=25):
        key = url.replace(sh.SITE, "")
        if key in over:
            return over[key]
        if key == "/index.json":
            return 200, json.dumps(INDEX), 0.1
        if key.startswith("/product-"):
            pid = key[len("/product-"):-len(".html")]
            return 200, page(pid), 0.1
        return 200, "ok", 0.1
    return f


today = date(2026, 10, 16)
row, probs = sh.run_checks(today, make_fetcher(), lambda h: 79)
chk("норма: 0 порушень, у CSV-рядку cert 79, index_n 39", probs == [] and row["cert_days"] == 79 and row["index_n"] == 39 and row["problems"] == 0)
_, p = sh.run_checks(today, make_fetcher(**{"/": (503, None, 1)}), lambda h: 79)
chk("головна 503 → порушення «/: HTTP 503»", p == ["/: HTTP 503"])
_, p = sh.run_checks(today, make_fetcher(**{"/index.json": (200, "не json", 0.1)}), lambda h: 79)
chk("index.json не JSON → порушення, картки не перевіряються", p == ["/index.json не JSON"])
_, p = sh.run_checks(today, make_fetcher(**{"/index.json": (200, "[]", 0.1)}), lambda h: 79)
chk("index.json порожній → порушення", p == ["/index.json порожній або не список"])
sel = sh.check_cards(INDEX, today, make_fetcher())
bad404 = sh.check_cards(INDEX, today, lambda u, timeout=25: (404, None, 0.1) if "/product-" in u else (200, "", 0.1))
chk("картки 404 → по порушенню на кожну з 10 вибраних", len(bad404) == 10 and all("HTTP 404" in x for x in bad404))
nold = sh.check_cards(INDEX, today, lambda u, timeout=25: (200, "<html>без ld</html>", 0.1))
chk("нема JSON-LD price/availability → порушення на кожну", len(nold) == 10 and all("нема price/availability" in x for x in nold))
mism = sh.check_cards(INDEX, today, lambda u, timeout=25: (200, page(u.rsplit("-", 1)[-1].split(".")[0], price=9999), 0.1))
chk("ціна сторінки ≠ index.json → порушення «ціна на сторінці»", len(mism) == 10 and all("ціна на сторінці 9999" in x for x in mism))
near = sh.check_cards(INDEX, today, lambda u, timeout=25: (200, page(u.rsplit("-", 1)[-1].split(".")[0], price=100 + int(u.rsplit("-", 1)[-1].split(".")[0]) + 1), 0.1))
chk("розбіжність у 1 ₴ (округлення) → не порушення", near == [])
_, p = sh.run_checks(today, make_fetcher(), lambda h: 10)
chk("сертифікат 10 днів (<14) → порушення", len(p) == 1 and "сертифікат спливає за 10" in p[0])
_, p = sh.run_checks(today, make_fetcher(), lambda h: None)
chk("сертифікат нечитабельний → порушення", p == ["сертифікат: не вдалось прочитати"])
_, p = sh.run_checks(today, make_fetcher(**{"/api/np/city?q=%D0%9A%D0%B8%D1%97%D0%B2": (500, None, 1)}), lambda h: 79)
chk("api/np/city 500 → порушення", p == ["/api/np/city: HTTP 500"])
chk("вибірка детермінована від дати (однаковий результат двічі; інша дата — інша вибірка)", [x for x in sh.check_cards(INDEX, today, lambda u, timeout=25: (404, None, 0))] == bad404 and sh.check_cards(INDEX, date(2026, 10, 17), lambda u, timeout=25: (404, None, 0)) != bad404)

ch = Path(tempfile.mkdtemp()) / "EXECUTOR_CHANNEL.md"
ch.write_text("<!-- шапка -->" + chr(10) + "> ## 📇 Як написати" + chr(10) + ">" + chr(10) + chr(10) + "## [Аудитор → Код] старе" + chr(10) + "текст" + chr(10), encoding="utf-8")
now = datetime(2026, 10, 16, 12, 0)
a1 = sh.post_to_channel(["/: HTTP 503"], now, ch)
a2 = sh.post_to_channel(["/: HTTP 503"], datetime(2026, 10, 16, 18, 0), ch)
a3 = sh.post_to_channel(["/: HTTP 503", "сертифікат спливає за 10 днів (<14)"], datetime(2026, 10, 16, 19, 0), ch)
t = ch.read_text(encoding="utf-8")
chk("той самий набір за добу — один запис; ІНШИЙ набір того ж дня — новий запис; над першим «## »; блок-цитата «> ## 📇» ціла", a1 and not a2 and a3 and t.count(sh.MARK) == 2 and t.index(sh.MARK) < t.index("## [Аудитор → Код] старе") and "> ## 📇 Як написати" in t and "> ## [Автомат" not in t)
# повтор і «немає мережі»
calls = {"n": 0}


def flaky(url, timeout=25):
    if url.endswith("/robots.txt"):
        calls["n"] += 1
        return (503, None, 0.1) if calls["n"] == 1 else (200, "ok", 0.1)
    return make_fetcher()(url, timeout)


_, p = sh.run_checks(today, flaky, lambda h: 79)
chk("разовий збій (503 → 200 після повтору) НЕ є порушенням", p == [] and calls["n"] == 2)


def dead(url, timeout=25):
    return 0, None, 0.1


row, p = sh.run_checks(today, dead, lambda h: None)
chk("немає мережі на десктопі (усе код 0, нейтральний хост теж): порушень 0, примітка в CSV-рядку", p == [] and "немає мережі" in row["detail"])


def site_down(url, timeout=25):
    return (0, None, 0.1) if url.startswith(sh.SITE) else (204, "", 0.1)


_, p = sh.run_checks(today, site_down, lambda h: None)
chk("сайт мовчить, а нейтральний хост відповідає → ПОРУШЕННЯ (сайт справді недоступний)", len(p) >= 2 and "/: HTTP 0" in p)
chk("нема каналу → False без винятку", sh.post_to_channel(["x"], now, Path(tempfile.mkdtemp()) / "нема.md") is False)
# --- зовнішня перевірка портів VPS (23.10): закриті порти панелі/MySQL; успішне підключення = порушення
chk("порти під контролем: панель Webuzo 2002–2005, Apache-панель 8443, MySQL 3306/33060", sorted(sh.EXT_CLOSED_PORTS) == [2002, 2003, 2004, 2005, 3306, 8443, 33060])
chk("усі порти закриті → check_ext_ports порожній (нема порушень)", sh.check_ext_ports("h", lambda host, port: False) == [])
op = sh.check_ext_ports("h", lambda host, port: port in (2003, 3306))
chk("відкриті 2003 і 3306 → два порушення з назвами служб і підказкою про extblock", len(op) == 2 and "2003" in op[0] and "Webuzo" in op[0] and "3306" in op[1] and "MySQL" in op[1] and all("extblock" in x for x in op))
chk("усі відкриті → 7 порушень", len(sh.check_ext_ports("h", lambda host, port: True)) == 7)
asked = []
sh.check_ext_ports("1.2.3.4", lambda host, port: asked.append((host, port)) or False)
chk("перевіряється той самий хост, що й сайт; кожен порт рівно раз", sorted(asked) == sorted(("1.2.3.4", p_) for p_ in sh.EXT_CLOSED_PORTS))
_, p0 = sh.run_checks(today, make_fetcher(), lambda h: 79)
chk("run_checks БЕЗ portfn не робить мережевих перевірок портів (старі виклики незмінні) — 0 порушень", p0 == [])
_, p1 = sh.run_checks(today, make_fetcher(), lambda h: 79, portfn=lambda host, port: False)
chk("run_checks з portfn, усе закрито → 0 порушень", p1 == [])
_, p2 = sh.run_checks(today, make_fetcher(), lambda h: 79, portfn=lambda host, port: port == 2003)
chk("run_checks: відкритий 2003 → рівно одне порушення про порт, лічильник problems=1 у рядку CSV", len(p2) == 1 and "2003" in p2[0])
row2, _ = sh.run_checks(today, make_fetcher(), lambda h: 79, portfn=lambda host, port: port == 3306)
chk("рядок CSV фіксує кількість порушень і текст", row2["problems"] == 1 and "3306" in row2["detail"])
import socket as _s
srv = _s.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
chk("port_open: слухаючий порт → True", sh.port_open("127.0.0.1", srv.getsockname()[1], 2.0) is True)
free = srv.getsockname()[1]
srv.close()
chk("port_open: закритий порт → False (не виняток)", sh.port_open("127.0.0.1", free, 1.0) is False)
chk("port_open: неіснуючий хост (DNS) → False (не виняток)", sh.port_open("host.invalid", 22, 1.0) is False)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
