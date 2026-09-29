"""Mini SOC web UI: alert list, alert detail, classification, training-data export. Local only.

  .venv/bin/python -m minisoc.webui          # http://127.0.0.1:8095/

No login (Fredrik, 2026-09-26: "no auth for now"). What stands in for it, because any web page
open in the same browser can send requests to 127.0.0.1:
  - binds 127.0.0.1 only;
  - Host header must be 127.0.0.1:PORT or localhost:PORT (defeats DNS rebinding);
  - every write needs the custom X-MiniSOC header and a JSON body: a cross-site page can't send
    either without a CORS preflight, and this server never answers one;
  - Origin, when present, must be this server;
  - strict Content-Security-Policy (no inline script), and the front end only ever inserts data
    as text: device names are attacker-controlled and must never become markup.
Writes: classifications (labels table + a human verdict) and closing an alert. Nothing here can
touch the UniFi controller or change a rule.
"""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import actions, baseline, config, fingerprints, normalize, segments, segmentation, store, traffic, triage, watcher

HOST, PORT = "127.0.0.1", 8095
WEB = Path(__file__).parent / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/brand-mark.svg": ("brand-mark.svg", "image/svg+xml")}
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                               "img-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}
SHOULD_FIRE = ("yes", "no")
TRAINING_TARGET = 300  # a first LoRA run on this task needs a few hundred labelled examples
MAX_BODY = 8192


class Refused(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def _ro():
    return store.connect(readonly=True)


# ---------------------------------------------------------------------------- views
def alert_view(db, a, full=False):
    t = a.get("triage") or {}
    v = {"id": a["id"], "created": a["created"], "rule": a["rule"], "severity": a["severity"], "status": a["status"],
         "title": a["title"], "entity": a["entity"], "recommended_action": watcher.recommended_action(a),
         "triage": ({k: t.get(k) for k in ("assessment", "confidence", "escalated", "model", "quant", "seconds")}
                    if t else None),
         "labelled": store.latest_label(db, a["id"]) is not None}
    ra = v["recommended_action"]
    if ra.get("action_id"):
        ra["steps"] = actions.ACTIONS[ra["action_id"]]["steps"]
    w = wifi_view(db, a)
    if w:
        v["wifi"] = w
    if full:
        v["connections"] = connection_view(db, a)
        issue = a.get("issue_id") or a["id"]
        v["issue"] = {"id": issue, "is_first": issue == a["id"], "macs": store.issue_macs(db, issue),
                      "occurrences": store.issue_occurrences(db, issue)}
        if t:
            v["triage"]["model_text"] = {k: t.get(k) for k in ("reason", "action_reason", "next_step")}
        v["detail"] = a["detail"]
        v["verdicts"] = [{k: r[k] for k in ("tier", "verdict", "confidence", "escalated", "rule_version", "model",
                                             "human_decision", "created")} for r in store.verdicts_for(db, a["id"])]
        v["label"] = store.latest_label(db, a["id"])
        default = (a.get("detail") or {}).get("default_action") or actions.default_for(a["rule"], a.get("detail"))
        allowed = sorted(actions.ALLOWED.get(a["rule"], set()) | ({default} if default else set()))
        v["options"] = {"assessments": list(triage.ASSESSMENTS),
                        "actions": [{"id": x, "title": actions.title(x)} for x in allowed]}
    return v


def connection_view(db, a):
    """Where each device in the alert is plugged in, as last seen: the UniFi switch or AP (name and
    model from the console's own device list when it's known), the port, the negotiated speed, and IP."""
    radios = store.get_meta(db, "own_radios") or {}
    gear = {d.get("mac"): d for d in radios.get("devices", []) if isinstance(d, dict)}
    out = []
    for mac in str(a.get("entity") or "").split(",")[:8]:
        d = store.get_device(db, mac.strip()) if re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", mac.strip()) else None
        if not d:
            continue
        g = gear.get(d.get("uplink_mac")) or {}
        out.append({"mac": d["mac"], "ip": d.get("ip"), "network": d.get("network"), "link": d.get("link"),
                    "active": bool(d.get("active")), "last_seen": d.get("last_seen"),
                    "uplink_mac": d.get("uplink_mac"), "uplink_name": g.get("name") or d.get("uplink_name"),
                    "uplink_model": g.get("model"), "port": d.get("uplink_port"), "link_mbps": d.get("link_mbps"),
                    "signal_dbm": d.get("signal_dbm")})
    return out


def inventory_view(db):
    """Every device the controller has ever reported: identity, when and where it was last seen, and
    UniFi's fingerprint. Names are wrapped with who wrote them; nothing here is a verdict."""
    table = fingerprints.load()
    gear = {d.get("mac"): d for d in (store.get_meta(db, "own_radios") or {}).get("devices", []) if isinstance(d, dict)}
    open_alerts = {}  # open issues per device, by the issue's first alert
    for a in store.alerts(db, "open", limit=5000):
        for mac in str(a.get("entity") or "").split(","):
            issue = a.get("issue_id") or a["id"]
            if issue not in open_alerts.setdefault(mac.strip().lower(), []):
                open_alerts[mac.strip().lower()].append(issue)
    devices = store.all_devices(db)
    net_vlan = {d["network"]: d["vlan"] for d in devices if d.get("network") and d.get("vlan") is not None}
    labels = store.seg_labels_latest_any(db)
    done = store.seg_latest_run(db, finished_only=True)
    planned = {a["mac"]: a for a in store.seg_assignments(db, done["id"])} if done else {}
    now = int(time.time())
    briefs = traffic.briefs(db, now)
    bmode = baseline.mode(db, now)
    unreviewed = store.baseline_open_counts(db, now - 7 * 86400)
    out = []
    for d in devices:
        fp = fingerprints.resolve(table, d) or {}
        g = gear.get(d.get("uplink_mac")) or {}
        vlan = d.get("vlan") if d.get("vlan") is not None else net_vlan.get(d.get("network"),
                                                                             1 if d.get("network") else None)
        t = briefs.get(d["mac"])
        rec = recommend(d["mac"], fp, labels, planned)
        rec["vlan"] = segments.SEGMENTS[rec["segment"]]["vlan"]
        rec["title"] = segments.SEGMENTS[rec["segment"]]["title"]
        rec["moves"] = vlan is not None and vlan != rec["vlan"]
        tnote, cuts_off = segments.traffic_note(rec["segment"], t)
        rec["traffic_note"], rec["check"] = tnote, cuts_off
        out.append({
            "mac": d["mac"], "hostname": normalize.label("hostname", d.get("hostname")),
            "name": normalize.label("name", d.get("name")), "vendor": d.get("vendor"),
            "fingerprint": {k: fp.get(k) for k in ("model", "vendor", "family", "type", "os") if fp.get(k)} or None,
            "mac_randomised": normalize.mac_randomised(d["mac"]),
            "online": bool(d.get("active")), "first_seen": d.get("controller_first_seen") or d.get("first_seen_here"),
            "last_seen": d.get("last_seen"), "ip": d.get("ip"), "network": d.get("network"), "link": d.get("link"),
            "uplink_mac": d.get("uplink_mac"), "uplink_name": g.get("name") or d.get("uplink_name"),
            "uplink_model": g.get("model"), "port": d.get("uplink_port"), "link_mbps": d.get("link_mbps"),
            "signal_dbm": d.get("signal_dbm"), "open_alerts": sorted(open_alerts.get(d["mac"], []))[-20:],
            "current_vlan": vlan, "recommended": rec,
            "baseline": {**baseline.status(db, d["mac"], now, bmode), "unreviewed": unreviewed.get(d["mac"], 0)},
            "traffic": {k: v for k, v in t.items() if k != "local_peers_seen"} | {"local_peers": len(t["local_peers_seen"])}
                       if t else None})
    out.sort(key=lambda x: (not x["online"], -(x["last_seen"] or 0)))
    return {"devices": out, "traffic_visibility": traffic.VISIBILITY,
            "traffic_since": store.get_meta(db, "traffic_since")}


def baseline_view(db):
    now = int(time.time())
    since = store.get_meta(db, "traffic_since")
    names = {d["mac"]: d for d in store.all_devices(db)}
    table = fingerprints.load()
    devs = []
    for x in store.baseline_deviations(db):
        d = names.get(x["mac"]) or {}
        fp = fingerprints.resolve(table, d) if d else None
        devs.append({"id": x["id"], "mac": x["mac"], "what": (fp or {}).get("model") or d.get("vendor") or "unknown device",
                     "name": normalize.label("name", d.get("name")), "day": x["day"], "kind": x["kind"],
                     "kind_means": baseline.KINDS.get(x["kind"], ""), "key": baseline.key_view(x["kind"], x["key"]),
                     "detail": x["detail"], "first_at": x["first_at"], "last_at": x["last_at"],
                     "verdict": x["verdict"], "note": x["note"]})
    return {"mode": baseline.mode(db, now), "learn_days": baseline.LEARN_DAYS, "min_days": baseline.MIN_DAYS,
            "learning_until": since + baseline.LEARN_DAYS * 86400 if since else None,
            "kinds": {k: {"means": v, **store.baseline_kind_stats(db).get(k, {"count": 0, "expected": 0, "suspicious": 0})}
                      for k, v in baseline.KINDS.items()},
            "verdicts": list(baseline.VERDICTS), "deviations": devs}


def label_deviation(deviation_id, body):
    if not isinstance(body, dict):
        raise Refused(400, "expected a JSON object")
    verdict, note = body.get("verdict"), body.get("note") or ""
    if verdict not in baseline.VERDICTS:
        raise Refused(400, "verdict must be expected or suspicious")
    if not isinstance(note, str) or len(note) > 500:
        raise Refused(400, "note must be text of at most 500 characters")
    db = store.connect()
    try:
        if not db.execute("SELECT 1 FROM baseline_deviations WHERE id=?", (deviation_id,)).fetchone():
            raise Refused(404, "no such deviation")
        store.deviation_label(db, deviation_id, verdict, note, int(time.time()))
        db.commit()
        return {"ok": True}
    finally:
        db.close()


def recommend(mac, fp, labels, planned):
    """Recommended segment: your own decision first, then the latest analysis (Qwen checked by the
    guards), then the device-type rule for devices that weren't online when the analysis ran."""
    if mac in labels and labels[mac]["segment"] in segments.SEGMENTS:
        return {"segment": labels[mac]["segment"], "source": "your call"}
    a = planned.get(mac)
    if a and a.get("final_segment") in segments.SEGMENTS:
        return {"segment": a["final_segment"], "status": a.get("status"),
                "source": "analysis (Qwen, checked by the guards)" if a.get("model_segment") else "analysis (rule)"}
    return {"segment": segments.baseline({"fingerprint": fp}), "source": "device type"}


def wifi_view(db, a):
    """SSID/BSSID for alerts about a Wi-Fi network. Older alerts stored the SSID as a bare string and
    no BSSID; both are recovered from the raw event, and the facts are computed at view time."""
    ev = (a.get("detail") or {}).get("event") or {}
    if not isinstance(ev, dict) or ("essid" not in ev and "bssid" not in ev):
        return None
    essid = ev.get("essid")
    ssid = essid.get("text") if isinstance(essid, dict) else essid if isinstance(essid, str) else None
    b = ev.get("bssid")
    bssid = b.get("value") if isinstance(b, dict) else b if isinstance(b, str) else None
    if not bssid and isinstance(ev.get("id"), str):
        params = (store.raw_event(db, ev["id"]) or {}).get("parameters")
        raw_b = (params.get("BSSID") if isinstance(params, dict) else None) or {}
        bssid = raw_b.get("name") if isinstance(raw_b, dict) and isinstance(raw_b.get("name"), str) else None
    from . import rules
    return {"ssid": normalize.label("ssid", ssid), "bssid": bssid, "bssid_note": normalize.BSSID_SOURCE,
            "channel": ev.get("channel"), "rssi": ev.get("rssi"), "nearest_ap": ev.get("nearest_ap"),
            "facts": rules.wifi_facts(db, ssid, bssid)}


def training_examples(db):
    """(ready examples, stats). An example is the exact prompt Qwen saw plus the answer you said
    it should have given, in the chat format mlx_lm.lora trains on."""
    labels = store.latest_labels(db)
    ready, no_prompt, no_reason, agreed, compared = [], 0, 0, 0, 0
    for alert_id, lab in labels.items():
        l2 = store.latest_l2(db, alert_id)
        if not l2 or not l2.get("prompt"):
            no_prompt += 1
            continue
        model_agrees = l2["verdict"] == lab["assessment"] and (store.get_alert(db, alert_id).get("triage") or {}).get("action") == lab["action"]
        compared += 1
        agreed += model_agrees
        # the reason in a training answer is always yours: the model's own reason was written from
        # attacker-influenced input, so it never becomes a target
        reason = (lab["note"] or "").strip()
        if not reason:
            no_reason += 1
            continue
        # Qwen's evidence list is attacker-influenced; it only enters the label when you agreed with Qwen
        target = {"assessment": lab["assessment"], "confidence": "high", "reason": reason,
                  "evidence": json.loads(l2["evidence"] or "[]") if model_agrees else [], "action": lab["action"],
                  "action_reason": reason, "next_step": actions.ACTIONS.get(lab["action"] or "", {}).get("title", "")}
        ready.append({"messages": json.loads(l2["prompt"]) + [{"role": "assistant", "content": json.dumps(target)}]})
    stats = {"labelled": len(labels), "ready": len(ready), "no_prompt": no_prompt, "no_reason": no_reason,
             "model_agreed": agreed, "model_compared": compared, "target": TRAINING_TARGET,
             "lora_command": ("mlx_lm.lora --model lmstudio-community/Qwen3.8-27B-MLX-8bit --train "
                              "--data <folder with train.jsonl + valid.jsonl> --iters 600 --batch-size 1 "
                              "--num-layers 8 --adapter-path adapters/minisoc-v1")}
    return ready, stats


# ---------------------------------------------------------------------------- segmentation
SEG_LOCK = threading.Lock()  # one analysis at a time: it holds the model for a few minutes


def segmentation_view(db):
    run = store.seg_latest_run(db)
    base = {"segments": [{"id": k, **{x: v[x] for x in ("title", "vlan", "purpose", "policy")}}
                         for k, v in segments.SEGMENTS.items()],
            "running": SEG_LOCK.locked(), "run": None, "devices": []}
    done = store.seg_latest_run(db, finished_only=True)
    if not done:
        return base
    labels = store.seg_latest_labels(db, done["id"])
    devices = []
    for a in store.seg_assignments(db, done["id"]):
        rec = a["record"]
        fp = rec.get("fingerprint") or {}
        seg = segments.SEGMENTS[a["final_segment"]]
        devices.append({
            "mac": a["mac"], "what": fp.get("model") or rec.get("vendor") or "unknown device",
            "fingerprint": fp or None, "vendor": rec.get("vendor"),
            "hostname": rec.get("hostname"), "name": rec.get("name"), "link": rec.get("link"),
            "current_network": a["current_network"], "current_vlan": a["current_vlan"],
            "moves": a["current_vlan"] != seg["vlan"],
            "rule_segment": a["rule_segment"], "model_segment": a["model_segment"],
            "model_confidence": a["model_confidence"], "model_reason": a["model_reason"],
            "final_segment": a["final_segment"], "status": a["status"], "note": a["note"],
            "label": labels.get(a["mac"])})
    return {**base, "running": SEG_LOCK.locked(),  # the lock is the truth: a crashed run can't hold the button
            "run": {"id": done["id"], "created": done["created"], "model": done["model"], "seconds": done["seconds"],
                    "error": done["error"], "notes": json.loads(done["notes"] or "[]"),
                    "observations": json.loads(done.get("observations") or "[]")},
            "devices": devices}


def start_segmentation():
    if not SEG_LOCK.acquire(blocking=False):
        raise Refused(409, "an analysis is already running")

    def work():
        db = None
        try:
            db = store.connect()
            store.seg_fail_stale_runs(db)  # a run that crashed earlier must not look "running" forever
            segmentation.run(db, config.load(), log=lambda m: print(m, flush=True))
        except Exception as ex:
            print(f"webui: segmentation run failed: {ex!r}", flush=True)
        finally:
            if db is not None:
                db.close()
            SEG_LOCK.release()
    try:
        threading.Thread(target=work, daemon=True).start()
    except Exception:
        SEG_LOCK.release()  # the thread never ran, so it can't release the lock itself
        raise
    return {"ok": True, "started": True}


def label_segment(run_id, mac, body):
    if not isinstance(body, dict):
        raise Refused(400, "expected a JSON object")
    seg, note = body.get("segment"), body.get("note") or ""
    if seg not in segments.SEGMENTS:
        raise Refused(400, "unknown segment")
    if not isinstance(note, str) or len(note) > 500:
        raise Refused(400, "note must be text of at most 500 characters")
    db = store.connect()
    try:
        if not db.execute("SELECT 1 FROM seg_assignments WHERE run_id=? AND mac=?", (run_id, mac)).fetchone():
            raise Refused(404, "no such device in that run")
        store.seg_add_label(db, run_id, mac, seg, note)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ---------------------------------------------------------------------------- write path
def classify(alert_id, body):
    if not isinstance(body, dict):
        raise Refused(400, "expected a JSON object")
    db = store.connect()
    try:
        a = store.get_alert(db, alert_id)
        if not a:
            raise Refused(404, "no such alert")
        should_fire = body.get("should_fire")
        assessment = body.get("assessment")
        action = body.get("action")
        note = body.get("note") or ""
        close = body.get("close") is True
        if should_fire not in SHOULD_FIRE:
            raise Refused(400, "should_fire must be yes or no")
        if assessment not in triage.ASSESSMENTS:
            raise Refused(400, "unknown assessment")
        default = (a.get("detail") or {}).get("default_action") or actions.default_for(a["rule"], a.get("detail"))
        if action is not None and not (action in actions.ACTIONS and
                                       (action == default or action in actions.ALLOWED.get(a["rule"], set()))):
            raise Refused(400, "that action isn't available for this rule")
        if not isinstance(note, str) or len(note) > 500:
            raise Refused(400, "note must be text of at most 500 characters")
        store.add_label(db, alert_id, should_fire, assessment, action, note, close)
        store.add_verdict(db, a, "human", watcher.rule_version(), verdict=assessment,
                          human_decision=f"should_fire={should_fire}; action={action}; {note}"[:600])
        if close and a["status"] == "open":
            store.ack(db, alert_id, note or "classified in web UI")
        db.commit()
        try:
            watcher.write_state(db, config.load())
        except Exception as ex:  # the label is saved; the watcher rewrites the state file on its next poll
            print(f"webui: state refresh after classify failed: {ex!r}", flush=True)
        return {"ok": True}
    finally:
        db.close()


# ---------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "MiniSOC"
    sys_version = ""

    def log_message(self, fmt, *args):  # keep request lines out of the watcher log and the terminal
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _guard(self, write=False):
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            raise Refused(421, "unexpected Host")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            raise Refused(403, "cross-origin request refused")
        if write:
            if self.headers.get("X-MiniSOC") != "1":
                raise Refused(403, "missing X-MiniSOC header")
            if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                raise Refused(415, "JSON only")

    def do_GET(self):
        try:
            self._guard()
            u = urlparse(self.path)
            if u.path in STATIC:
                name, ctype = STATIC[u.path]
                return self._send(200, (WEB / name).read_bytes(), ctype)
            if u.path == "/api/state":
                db = _ro()
                try:
                    return self._send(200, watcher.build_state(db, config.load()))
                finally:
                    db.close()
            if u.path == "/api/alerts":
                status = (parse_qs(u.query).get("status") or ["open"])[0]
                if status not in ("open", "acked", "all"):
                    raise Refused(400, "status must be open, acked or all")
                db = _ro()
                try:
                    rows = watcher.issues(db, None if status == "all" else status)
                    return self._send(200, [{**alert_view(db, a), **{k: a[k] for k in (
                        "severity", "occurrences", "open_occurrences", "last_at", "kinds", "macs")}} for a in rows])
                finally:
                    db.close()
            m = re.fullmatch(r"/api/alerts/(\d{1,9})", u.path)
            if m:
                db = _ro()
                try:
                    a = store.get_alert(db, int(m.group(1)))
                    if not a:
                        raise Refused(404, "no such alert")
                    return self._send(200, alert_view(db, a, full=True))
                finally:
                    db.close()
            if u.path == "/api/inventory":
                db = _ro()
                try:
                    return self._send(200, inventory_view(db))
                finally:
                    db.close()
            if u.path == "/api/baseline":
                db = _ro()
                try:
                    return self._send(200, baseline_view(db))
                finally:
                    db.close()
            if u.path == "/api/segmentation":
                db = _ro()
                try:
                    return self._send(200, segmentation_view(db))
                finally:
                    db.close()
            if u.path == "/api/training":
                db = _ro()
                try:
                    return self._send(200, training_examples(db)[1])
                finally:
                    db.close()
            if u.path == "/api/training/export.jsonl":
                db = _ro()
                try:
                    lines = "".join(json.dumps(x) + "\n" for x in training_examples(db)[0])
                finally:
                    db.close()
                return self._send(200, lines.encode(), "application/x-ndjson; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="minisoc-train.jsonl"'})
            raise Refused(404, "not found")
        except Refused as r:
            self._send(r.code, {"error": str(r)})
        except Exception as ex:
            self._send(500, {"error": type(ex).__name__})

    def do_POST(self):
        try:
            self._guard(write=True)
            path = urlparse(self.path).path
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                raise Refused(413, "body too large or empty")
            try:
                body = json.loads(self.rfile.read(n))
            except json.JSONDecodeError:
                raise Refused(400, "invalid JSON")
            m = re.fullmatch(r"/api/alerts/(\d{1,9})/label", path)
            if m:
                return self._send(200, classify(int(m.group(1)), body))
            m = re.fullmatch(r"/api/baseline/deviations/(\d{1,9})/label", path)
            if m:
                return self._send(200, label_deviation(int(m.group(1)), body))
            if path == "/api/segmentation/run":
                return self._send(202, start_segmentation())
            m = re.fullmatch(r"/api/segmentation/(\d{1,9})/([0-9a-f]{2}(?::[0-9a-f]{2}){5})/label", path)
            if m:
                return self._send(200, label_segment(int(m.group(1)), m.group(2), body))
            raise Refused(404, "not found")
        except Refused as r:
            self._send(r.code, {"error": str(r)})
        except Exception as ex:
            self._send(500, {"error": type(ex).__name__})

    def do_OPTIONS(self):  # never grant a CORS preflight
        self._send(405, {"error": "no cross-origin access"})


def main():
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
