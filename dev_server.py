"""Run the whole app on this PC with an in-memory database (nothing is saved).

    python dev_server.py        then open http://localhost:8080
Staff PIN 1234, owner PIN 246810. WhatsApp sending is off (manual links).
"""
import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "api"))

from core import ApiError, Rewards          # noqa: E402
from store import MemoryStore               # noqa: E402

try:
    from router import dispatch             # needs azure-functions installed
except ImportError:
    import types
    fn = types.ModuleType("azure.functions")
    fn.HttpRequest = fn.HttpResponse = object
    az = sys.modules.setdefault("azure", types.ModuleType("azure"))
    az.functions = fn
    sys.modules["azure.functions"] = fn
    from router import dispatch             # noqa: E402

svc = Rewards(MemoryStore(), None, {
    "token_secret": "local-dev-secret-local-dev-secret-0000",
    "admin_pin": "246810", "staff_pin": "1234",
    "business": "Mighty Car-Wash", "location": "Bolobedu Matipane Molai",
    "public_url": "", "review_url": "",
})
# one sample WhatsApp reply so the Replies tab has something to show (local trial only)
import time as _t
svc.store.add_reply("sample", {"phone": "27820000009", "name": "Sample Customer", "customer": False,
                               "text": "Are you open on Sunday?", "kind": "message", "at": _t.time() - 600})
# five weeks of sample washes so the Reports tab has something to show (local trial only)
import random as _r
from core import sa_day as _sa_day
_rng = _r.Random(7)
for _d in range(1, 36):
    _ts = _t.time() - _d * 86400
    for _i in range(_rng.randint(2, 9)):
        _ty, _pr = _rng.choice([("Normal", 80), ("Premium", 100), ("Platinum", 110)])
        _kind = _rng.choice(["wash"] * 8 + ["new", "free"])
        svc.store.add_visit(_sa_day(_ts), "%013d-%d" % (int(_ts * 1000), _i), {
            "type": _kind, "phone": "2782000%04d" % _i, "name": "Sample", "count": 1, "at": _ts + _i,
            "washType": _ty, "price": 0 if _kind == "free" else _pr, "stamps": 1,
            "staff": _rng.choice(["Sipho", "Lerato", "Thabo"]), "plate": "", "void": False})


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT / "app"), **kw)

    def _api(self, method):
        path = self.path.split("?")[0][len("/api/"):].strip("/")
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            out, status = dispatch(svc, method, path, body, self.headers.get("X-Session"), self.client_address[0]), 200
        except ApiError as e:
            out, status = {"error": e.message}, e.status
        data = json.dumps(out).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._api("GET")
        return super().do_GET()

    def do_POST(self):
        self._api("POST")

    def do_PATCH(self):
        self._api("PATCH")

    def do_DELETE(self):
        self._api("DELETE")


if __name__ == "__main__":
    print("Mighty Rewards running at http://localhost:8080  (staff PIN 1234, owner PIN 246810)")
    ThreadingHTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
