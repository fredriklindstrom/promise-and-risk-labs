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
full = json.dumps({"should_fire": "yes", "assessment": "likely_benign", "action": "RENAME_DEVICE", "note": "😀" * 500,
                   "checks": "😀" * 2000, "action_reason": "😀" * 1000, "seen_ids": list(range(10**8, 10**8 + 1000))}).encode()
check("a classification at every limit, all of it escaped, still fits the body cap",
      len(full) > 50000 and req(f"/api/alerts/{aid}/label", "POST", full, good)[0] == 200)
check("a body past the cap is refused", req(f"/api/alerts/{aid}/label", "POST", b" " * 70000, good)[0] == 413)
code, body = req("/")
check("CSP forbids inline script", b"<script>" not in body)
st, alerts = req("/api/alerts")
check("classified alert is marked", json.loads(alerts)[0]["labelled"] is True)

# segmentation endpoints
db = store.connect()
rid = store.seg_run_start(db, "m", "8bit", "v")
store.seg_add_assignment(db, rid, {"mac": "aa:bb:cc:dd:ee:01", "rule_segment": "IOT", "final_segment": "IOT",
                                   "status": "ok", "record": {"mac": "aa:bb:cc:dd:ee:01"}})
store.seg_run_finish(db, rid, status="done")
db.commit(); db.close()
seg = json.dumps({"segment": "MEDIA", "note": "it's the TV"}).encode()
check("segment label without X-MiniSOC refused", req(f"/api/segmentation/{rid}/aa:bb:cc:dd:ee:01/label", "POST", seg,
      {"Content-Type": "application/json"})[0] == 403)
check("unknown segment refused", req(f"/api/segmentation/{rid}/aa:bb:cc:dd:ee:01/label", "POST",
      json.dumps({"segment": "ROOT"}).encode(), good)[0] == 400)
check("device not in the run refused", req(f"/api/segmentation/{rid}/aa:bb:cc:dd:ee:99/label", "POST", seg, good)[0] == 404)
check("malformed MAC in path refused", req(f"/api/segmentation/{rid}/aa:bb:cc:dd:ee/label", "POST", seg, good)[0] == 404)
check("valid segment label accepted", req(f"/api/segmentation/{rid}/aa:bb:cc:dd:ee:01/label", "POST", seg, good)[0] == 200)
st, body = req("/api/segmentation")
check("segmentation view shows your call", json.loads(body)["devices"][0]["label"]["segment"] == "MEDIA")
check("run start without X-MiniSOC refused", req("/api/segmentation/run", "POST", b"{}", {"Content-Type": "application/json"})[0] == 403)

db = store.connect()
store.upsert_device(db, store.device_row({"mac": "aa:bb:cc:dd:ee:07", "hostname": "<img src=x onerror=alert(1)>",
                                          "is_wired": True, "sw_mac": "aa:bb:cc:00:11:22", "sw_port": 4,
                                          "wired_rate_mbps": 1000, "last_seen": 1_700_000_000}), 1_700_000_000, active=True)
aid2 = store.add_alert(db, "new_device", "medium", "aa:bb:cc:dd:ee:07", "New device", {}, "new_device:aa:bb:cc:dd:ee:07")
db.commit(); db.close()
st, body = req("/api/inventory")
dev = next((x for x in json.loads(body)["devices"] if x["mac"] == "aa:bb:cc:dd:ee:07"), None) if st == 200 else None
check("inventory lists every device with where it was seen", dev is not None and dev["port"] == 4 and dev["link_mbps"] == 1000
      and dev["online"] is True)
check("inventory wraps device-written names with who wrote them", dev and dev["hostname"]["written_by"].startswith("reported by the device"))
check("inventory links the device's open alerts", dev and aid2 in dev["open_alerts"])
check("inventory is read-only", req("/api/inventory", "POST", b"{}", good)[0] == 404)
db = store.connect()
store.baseline_deviation(db, "aa:bb:cc:dd:ee:07", "2026-09-28", "new_domain", "<b>x</b>.example", {}, 1_700_000_000)
db.commit(); db.close()
st, body = req("/api/baseline")
bv = json.loads(body) if st == 200 else {}
dv = (bv.get("deviations") or [{}])[0]
check("baseline page lists deviations, domains wrapped", st == 200 and dv.get("key", {}).get("domain", {}).get("written_by"))
verdict = json.dumps({"verdict": "expected", "note": "new app"}).encode()
check("deviation verdict without X-MiniSOC refused",
      req(f"/api/baseline/deviations/{dv.get('id')}/label", "POST", verdict, {"Content-Type": "application/json"})[0] == 403)
check("unknown verdict refused", req(f"/api/baseline/deviations/{dv.get('id')}/label", "POST",
                                     json.dumps({"verdict": "ignore"}).encode(), good)[0] == 400)
check("verdict on a missing deviation refused", req("/api/baseline/deviations/999999/label", "POST", verdict, good)[0] == 404)
check("valid verdict accepted", req(f"/api/baseline/deviations/{dv.get('id')}/label", "POST", verdict, good)[0] == 200)
db = store.connect()
first = store.add_alert(db, "label_changed", "medium", "aa:bb:cc:dd:ee:08", "Hostname changed", {}, "iss1")
again = store.add_alert(db, "label_changed", "medium", "aa:bb:cc:dd:ee:08", "Hostname changed", {}, "iss2")
db.commit(); db.close()
row = next((x for x in json.loads(req("/api/alerts")[1]) if x["id"] == first), None)
check("alert list shows the issue once, with its occurrence count", row and row["occurrences"] == 2
      and not any(x["id"] == again for x in json.loads(req("/api/alerts")[1])))
det = json.loads(req(f"/api/alerts/{again}")[1])
check("an occurrence's page points to its issue and lists every occurrence",
      det["issue"]["id"] == first and not det["issue"]["is_first"] and len(det["issue"]["occurrences"]) == 2)
db = store.connect()
f1 = store.add_alert(db, "label_changed", "medium", "aa:bb:cc:dd:ee:09", "Hostname changed", {}, "seen1")
f2 = store.add_alert(db, "label_changed", "medium", "aa:bb:cc:dd:ee:09", "Hostname changed", {}, "seen2")
db.commit(); db.close()
closing = json.dumps({"should_fire": "yes", "assessment": "likely_benign", "action": "RENAME_DEVICE", "note": "x",
                      "close": True, "seen_ids": [f1], "confidence": "high",
                      "action_reason": "rename it in the console"}).encode()
check("malformed seen_ids are refused", req(f"/api/alerts/{f1}/label", "POST",
      json.dumps({"should_fire": "yes", "assessment": "likely_benign", "close": True, "seen_ids": [True, "x"]}).encode(), good)[0] == 400)
check("closing with seen_ids accepted", req(f"/api/alerts/{f1}/label", "POST", closing, good)[0] == 200)
check("an occurrence that wasn't shown stays open", json.loads(req(f"/api/alerts/{f2}")[1])["status"] == "open")
check("drawing a sample without X-MiniSOC is refused", req("/api/baseline/sample", "POST", json.dumps({"size": 50}).encode(),
      {"Content-Type": "application/json"})[0] == 403)
check("a sample size out of range is refused", req("/api/baseline/sample", "POST", json.dumps({"size": 5000}).encode(), good)[0] == 400)
check("drawing a sample works and shows on the page", req("/api/baseline/sample", "POST", json.dumps({"size": 10}).encode(), good)[0] == 200
      and json.loads(req("/api/baseline")[1])["sample"]["size"] == 10)
# the investigation record: closing medium/high needs it; the report exports private or scrubbed
db = store.connect()
inv_id = store.add_alert(db, "new_device", "medium", "aa:bb:cc:dd:ee:0a", "New device on the network",
                         {"default_action": "IDENTIFY_DEVICE"}, "inv1")
db.commit(); db.close()
inv_body = {"should_fire": "yes", "assessment": "likely_benign", "action": "IDENTIFY_DEVICE", "note": "my laptop's dock",
            "close": True, "seen_ids": [inv_id]}
check("closing a medium issue without confidence or the action's reason is refused",
      req(f"/api/alerts/{inv_id}/label", "POST", json.dumps(inv_body).encode(), good)[0] == 400)
check("an unknown confidence is refused", req(f"/api/alerts/{inv_id}/label", "POST",
      json.dumps({**inv_body, "confidence": "certain", "action_reason": "x"}).encode(), good)[0] == 400)
full = {**inv_body, "confidence": "high", "action_reason": "it's mine; nothing to do",
        "checks": "Looked at the dock's MAC aa:bb:cc:dd:ee:0a and its address 10.0.0.9 in the console"}
check("closing with the full record is accepted", req(f"/api/alerts/{inv_id}/label", "POST", json.dumps(full).encode(), good)[0] == 200)
st, body = req(f"/api/alerts/{inv_id}")
rec = json.loads(body)["investigation"]
check("the alert page carries the four-part record with your answers", st == 200 and rec["seen"] and rec["yours"]["confidence"] == "high"
      and rec["yours"]["action_reason"].startswith("it's mine"))
st, priv = req(f"/api/alerts/{inv_id}/report.md")
st2, pub = req(f"/api/alerts/{inv_id}/report.md?public=1")
check("the private report has all four sections", st == 200 and all(f"## {i}." in priv.decode() for i in (1, 2, 3, 4)))
check("the public report has no MAC or address, even in your own notes", st2 == 200 and "aa:bb:cc:dd:ee:0a" not in pub.decode()
      and "10.0.0.9" not in pub.decode() and "a private address" in pub.decode())
check("verdict shows in the false-alarm table", json.loads(req("/api/baseline")[1])["kinds"]["new_domain"]["expected"] == 1)

st, body = req("/api/blocked?days=7")
check("blocked connections answer with groups", st == 200 and "groups" in json.loads(body))
check("blocked: a bad MAC or day count is refused",
      req("/api/blocked?mac=nope")[0] == 400 and req("/api/blocked?days=0")[0] == 400 and req("/api/blocked?days=999")[0] == 400)
check("blocked is read-only", req("/api/blocked", "POST", b"{}", good)[0] == 404)

srv.shutdown()
print("\nWEBUI", "PASS" if not FAILS else f"FAIL ({len(FAILS)})")
sys.exit(1 if FAILS else 0)
