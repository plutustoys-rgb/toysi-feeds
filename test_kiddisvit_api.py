#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_kiddisvit_api.py — клієнт B2B API KIDDISVIT (17.11.2026). Інваріанти: (1) власний User-Agent і Bearer у КОЖНОМУ запиті (Cloudflare 1010 без UA), ключ ніколи не потрапляє у виняток; (2) пауза між запитами ≥ MIN_INTERVAL_S (ліміт постачальника 25/10 с);
(3) 429/5xx → ретрай з паузою, далі KiddisvitError; не-200 / не-JSON / status≠success → KiddisvitError; (4) нема ключа → KiddisvitError до мережі; (5) шляхи й тіла: products(GET, locale/1/дата), prices_by_brand(POST без warehouses), clients_list."""
import http.client
import io
import json
import sys
import urllib.error
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import kiddisvit_api as api  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


class Resp:
    def __init__(self, body, status=200):
        self._b, self.status = body if isinstance(body, bytes) else body.encode("utf-8"), status

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, retry_after=None):
    hdr = {"Retry-After": retry_after} if retry_after else {}
    class H(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)
    return urllib.error.HTTPError("u", code, "x", H(hdr), io.BytesIO(b"SECRET-BODY"))


class Fake:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def __call__(self, req, timeout):
        self.calls.append((req.get_method(), req.full_url, dict(req.header_items()), req.data))
        x = self.script.pop(0)
        if isinstance(x, Exception):
            raise x
        return x


class Clock:
    def __init__(self):
        self.t, self.sleeps = 100.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def mk(script, key="K-SECRET-123"):
    f, c = Fake(script), Clock()
    return api.KiddisvitClient(key=key, base_url="https://x/api/v2", opener=f, sleep=c.sleep, clock=c.now), f, c


OK = lambda data: Resp(json.dumps({"status": "success", "data": data}))

cl, f, c = mk([OK({"clients": [{"ClientID": "C"}]})])
d = cl.clients_list()
m, url, hdr, body = f.calls[0]
hl = {k.lower(): v for k, v in hdr.items()}
chk("clients/list: GET на правильну адресу", m == "GET" and url == "https://x/api/v2/clients/list" and d["clients"][0]["ClientID"] == "C")
chk("заголовки: Bearer і власний User-Agent (Cloudflare 1010 без нього)", hl.get("authorization") == "Bearer K-SECRET-123" and hl.get("user-agent") == api.USER_AGENT and "python-urllib" not in hl.get("user-agent", ""))

cl, f, c = mk([OK({}), OK({}), OK({})])
for _ in range(3):
    cl.clients_list()
chk("пауза між запитами ≥ MIN_INTERVAL_S (≤ 25 запитів / 10 с з запасом)", len(c.sleeps) == 2 and all(abs(s - api.MIN_INTERVAL_S) < 1e-6 for s in c.sleeps) and api.MIN_INTERVAL_S >= 0.4)

cl, f, c = mk([http_error(429), http_error(429, "7"), OK({"products": [1]})])
chk("429 → ретрай із паузою (Retry-After враховано), потім успіх", cl.rests() == [1] and len(f.calls) == 3 and max(c.sleeps) >= 7)

cl, f, c = mk([http_error(503)] * (api.MAX_RETRIES + 1))
try:
    cl.clients_list()
    raised = None
except api.KiddisvitError as e:
    raised = str(e)
chk("постійний 503 → KiddisvitError після MAX_RETRIES ретраїв; у тексті нема ключа й тіла відповіді", raised is not None and "K-SECRET" not in raised and "SECRET-BODY" not in raised and len(f.calls) == api.MAX_RETRIES + 1)

for name, script in (("401 без ретраю", [http_error(401)]), ("не-JSON", [Resp("<html>Just a moment</html>")]), ("status≠success", [Resp(json.dumps({"status": "error", "data": None}))]), ("мережева помилка", [urllib.error.URLError("x")] * (api.MAX_RETRIES + 1))):
    cl, f, c = mk(script)
    try:
        cl.clients_list()
        r = False
    except api.KiddisvitError as e:
        r = "K-SECRET" not in str(e)
    chk(f"{name} → KiddisvitError без ключа в тексті", r)
cl, f, c = mk([http_error(401)])
try:
    cl.clients_list()
except api.KiddisvitError:
    pass
chk("401 → без ретраю (один запит)", len(f.calls) == 1)

cl, f, c = mk([], key="")
try:
    cl.clients_list()
    r = False
except api.KiddisvitError:
    r = True
chk("нема ключа → KiddisvitError до будь-якого запиту", r and not f.calls and not cl.has_key())

cl, f, c = mk([OK({"products": [{"productID": "1"}], "products_total_qty": 1})])
res = cl.products()
chk("catalog/products: GET /ua/1/<дата із %20>", f.calls[0][0] == "GET" and f.calls[0][1] == "https://x/api/v2/catalog/products/ua/1/2000-01-01%2000%3A00%3A00" and res == [{"productID": "1"}])
cl, f, c = mk([OK({"products": [{"ProductID": "1", "clientPrice": 10}]})])
res = cl.prices_by_brand("CL", "CT", "BR")
sent = json.loads(f.calls[0][3])
chk("products/rests/prices: POST, лише clientID+ContractID+brands (без warehouses/products: з ними відповідь порожня)", f.calls[0][0] == "POST" and sent == {"clientID": "CL", "ContractID": "CT", "brands": [{"brandID": "BR"}]} and res[0]["clientPrice"] == 10)
cl, f, c = mk([OK([{"ID": "g", "Name": "N", "brand_id": "X1"}]), OK([{"id": "c", "name": "Кат"}])])
chk("brands / categories(ua)", cl.brands()[0]["Name"] == "N" and cl.categories()[0]["name"] == "Кат" and f.calls[1][1].endswith("/catalog/categories/ua"))

# аудит #1691: усічена відповідь (IncompleteRead — HTTPException, не OSError) → ретрай, потім успіх; Retry-After має стелю
cl, f, c = mk([http.client.IncompleteRead(b"x"), OK({"products": [7]})])
chk("IncompleteRead (усічена відповідь 26 МБ) → ретрай, потім успіх", cl.rests() == [7] and len(f.calls) == 2)
cl, f, c = mk([http.client.IncompleteRead(b"x")] * (api.MAX_RETRIES + 1))
try:
    cl.rests()
    r = False
except api.KiddisvitError:
    r = True
chk("IncompleteRead постійно → KiddisvitError (а не сирий виняток)", r)
cl, f, c = mk([http_error(429, "86400"), OK({"products": []})])
cl.rests()
chk("Retry-After 86400 → пауза обмежена MAX_RETRY_AFTER_S", max(c.sleeps) <= max(api.MAX_RETRY_AFTER_S, 5.0 * (api.MAX_RETRIES + 1)) and sum(c.sleeps) < 200)
for bad in ("inf", "nan", "-5", "abc"):
    cl, f, c = mk([http_error(429, bad), OK({"products": []})])
    try:
        cl.rests()
        ok_ = sum(c.sleeps) < 200
    except Exception:  # noqa: BLE001
        ok_ = False
    chk(f"Retry-After «{bad}» не ламає ретрай", ok_)


print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
