#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_photo_probe.py — детектор зникнення фото каталогу (site_photo_probe.py): будь-що, крім 200+image/*+непорожнє = порушення; самодіагностичний алерт;
без порушень — тиша; збій алерту/мережі не валить збірку. Самодостатній (мережа підмінена)."""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import site_photo_probe as sp  # noqa: E402
sp.RETRY_DELAY_SEC = 0

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


class R:
    def __init__(self, status=200, ctype="image/jpeg", body=b"x" * 100):
        self.status_code, self.headers, self._b = status, {"Content-Type": ctype}, body

    def iter_content(self, n):
        if self._b:
            yield self._b[:n]

    def close(self):
        pass


def getter(mapping, default=None):
    seen = []

    def g(url, headers=None, timeout=None, stream=None):
        seen.append((url, headers))
        v = mapping.get(url, default if default is not None else R())
        if isinstance(v, Exception):
            raise v
        return v
    g.seen = seen
    return g


idx = [{"id": str(i), "p": f"https://toysi.ua/p/{i}.jpg"} for i in range(100)] + [{"id": "x", "p": ""}, {"id": "y"}, {"id": "z", "p": None}]
sample = sp.pick_sample(idx, 20, random.Random(1))
chk("вибірка: 20 унікальних http-фото, порожні/без p пропущено", len(sample) == 20 and len(set(sample)) == 20 and all(u.startswith("https://toysi.ua/") for u in sample))
chk("вибірка детермінована за зерном (тест відтворюваний)", sp.pick_sample(idx, 20, random.Random(1)) == sample)
chk("порожній індекс → порожня вибірка", sp.pick_sample([], 20) == [] and sp.pick_sample(None, 20) == [])

alerts = []
g = getter({})
res, bad = sp.run(idx, notify=lambda k, t, c: alerts.append((k, t, c)), get=g, rng=random.Random(2))
chk("усе добре → нуль поганих і ТИША (жодного алерта)", len(res) == 20 and not bad and not alerts)
chk("запит іде з нашим Referer і User-Agent", all(h and h.get("Referer") == sp.REFERER and "PlutusToysPhotoProbe" in h.get("User-Agent", "") for _, h in g.seen))

u = sp.pick_sample(idx, 20, random.Random(3))
g = getter({u[0]: R(403, "text/html"), u[1]: R(200, "text/html; charset=utf-8", b"<html>"), u[2]: R(200, "image/png", b""), u[3]: TimeoutError("t"), u[4]: R(404, "text/html")})
alerts.clear()
res, bad = sp.run(idx, notify=lambda k, t, c: alerts.append((k, t, c)), get=g, rng=random.Random(3))
chk("5 різних порушень (403, 200-не-image, порожнє тіло, таймаут, 404) → 5 поганих і після повтору", len(bad) == 5)
chk("алерт один, ключ site_photo_probe, cooldown 6 год", len(alerts) == 1 and alerts[0][0] == "site_photo_probe" and alerts[0][2] == 6 * 3600)
t = alerts[0][1]
chk("текст самодіагностичний: скільки з вибірки, коди, приклад URL, що робити", "5 з 20" in t and "HTTP 403" in t and "HTTP 404" in t and "TimeoutError" in t and "toysi.ua/p/" in t and "Що робити" in t)
chk("«200, але не image» і «порожнє тіло» названо окремо; є ознаки блокування → «hotlink»", "не image" in t and "порожнє тіло" in t and "hotlink" in t)

# одиничні збої після повтору → ЛИШЕ лог, без алерта
for lone in (R(404, "text/html"), R(503, "text/html"), TimeoutError("t")):
    alerts.clear()
    res, bad = sp.run(idx, notify=lambda k, t, c: alerts.append((k, t, c)), get=getter({u[0]: lone}), rng=random.Random(3))
    chk(f"одиничний збій ({getattr(lone, 'status_code', type(lone).__name__)}) без ознак блокування → нема алерта", len(bad) == 1 and not alerts)
alerts.clear()
res, bad = sp.run(idx, notify=lambda k, t, c: alerts.append((k, t, c)), get=getter({u[0]: R(403, "text/html")}), rng=random.Random(3))
chk("одиничний 403 (ознака блокування) → алерт", len(alerts) == 1 and "hotlink" in alerts[0][1])
alerts.clear()
res, bad = sp.run(idx, notify=lambda k, t, c: alerts.append((k, t, c)), get=getter({u[0]: R(404, "text/html"), u[1]: R(404, "text/html")}), rng=random.Random(3))
chk("два 404 → алерт, але БЕЗ слова «hotlink» (це збій, не блокування)", len(alerts) == 1 and "hotlink" not in alerts[0][1] and "збій" in alerts[0][1])

# повтор знімає разовий збій
calls = {}


def flaky(url, headers=None, timeout=None, stream=None):
    calls[url] = calls.get(url, 0) + 1
    if url == u[0] and calls[url] == 1:
        return R(403, "text/html")
    return R()


alerts.clear()
res, bad = sp.run(idx, notify=lambda k, t, c: alerts.append((k, t, c)), get=flaky, rng=random.Random(3))
chk("разовий 403, що минув на повторі → нема алерта", not bad and not alerts and calls[u[0]] == 2)

# allowlist хостів
bad_idx = [{"p": "http://127.0.0.1/x.jpg"}, {"p": "file:///etc/passwd"}, {"p": "https://evil.example/x.jpg"}, {"p": "https://toysi.ua/p/1.jpg"}, {"p": "https://images.prom.ua/2.jpg"}, {"p": "https://toysi.ua.evil.com/3.jpg"}]
chk("лише toysi.ua/images.prom.ua (без SSRF на 127.0.0.1, file://, чужі хости, toysi.ua.evil.com)", sorted(sp.pick_sample(bad_idx, 20)) == ["https://images.prom.ua/2.jpg", "https://toysi.ua/p/1.jpg"])

g = getter({}, default=R(403, "text/html"))
alerts.clear()
res, bad = sp.run(idx, notify=lambda k, t, c: alerts.append((k, t, c)), get=g, rng=random.Random(4))
chk("усі фото заблоковані → 20 з 20 поганих, алерт", len(bad) == 20 and len(alerts) == 1 and "20 з 20" in alerts[0][1])


def boom(*a):
    raise RuntimeError("telegram down")


try:
    sp.run(idx, notify=boom, get=getter({}, default=R(403, "text/html")), rng=random.Random(5))
    ok = True
except Exception:
    ok = False
chk("збій каналу алертів НЕ кидає виняток (збірка сайту не падає)", ok)
print()
if F:
    print("FAILED:", F)
    sys.exit(1)
print("ALL OK")
