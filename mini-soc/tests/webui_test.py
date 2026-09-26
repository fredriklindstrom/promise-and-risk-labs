"""Web UI guard tests: a throwaway server on a free port against a temp DB. No network beyond loopback.

  .venv/bin/python -m tests.webui_test
"""
import json
import pathlib
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from minisoc import config, store, webui

FAILS = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


tmp = pathlib.Path(tempfile.mkdtemp())
config.DB_PATH, config.STATE_DIR, config.STATE_JSON = tmp / "t.sqlite", tmp, tmp / "state.json"
config.load = lambda: {"site_name": "test", "poll_seconds": 120}  # no config.json needed
db = store.connect()
aid = store.add_alert(db, "hostname_collision", "medium", "a,b", "2 devices report the same hostname", {"default_action": "RENAME_DEVICE"}, "k")
db.commit(); db.close()

srv = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
port = srv.server_address[1]
webui.ALLOWED_HOSTS = {f"127.0.0.1:{port}", f"localhost:{port}"}
webui.ALLOWED_ORIGINS = {f"http://{h}" for h in webui.ALLOWED_HOSTS}
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"


def req(path, method="GET", body=None, headers=None):
    r = urllib.request.Request(base + path, method=method, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


good = {"X-MiniSOC": "1", "Content-Type": "application/json"}
label = json.dumps({"should_fire": "yes", "assessment": "likely_benign", "action": "RENAME_DEVICE", "note": "x"}).encode()

check("page loads", req("/")[0] == 200)
check("wrong Host refused (DNS rebinding)", req("/api/alerts", headers={"Host": "evil.example"})[0] == 421)
check("write without X-MiniSOC refused", req(f"/api/alerts/{aid}/label", "POST", label, {"Content-Type": "application/json"})[0] == 403)
check("write as text/plain refused", req(f"/api/alerts/{aid}/label", "POST", label, {"X-MiniSOC": "1", "Content-Type": "text/plain"})[0] == 415)
check("cross-origin write refused", req(f"/api/alerts/{aid}/label", "POST", label, {**good, "Origin": "https://evil.example"})[0] == 403)
check("preflight never granted", req(f"/api/alerts/{aid}/label", "OPTIONS")[0] == 405)
check("traversal refused", req("/../config.json")[0] == 404)
bad = json.dumps({"should_fire": "yes", "assessment": "likely_benign", "action": "NO_ACTION"}).encode()
check("action outside the rule's list refused", req(f"/api/alerts/{aid}/label", "POST", bad, good)[0] == 400)
check("valid classification accepted", req(f"/api/alerts/{aid}/label", "POST", label, good)[0] == 200)
code, body = req("/")
check("CSP forbids inline script", b"<script>" not in body)
st, alerts = req("/api/alerts")
check("classified alert is marked", json.loads(alerts)[0]["labelled"] is True)

srv.shutdown()
print("\nWEBUI", "PASS" if not FAILS else f"FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
