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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import actions, config, normalize, store, triage, watcher

HOST, PORT = "127.0.0.1", 8095
WEB = Path(__file__).parent / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8")}
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
    if full:
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
                    rows = store.alerts(db, None if status == "all" else status, limit=500)
                    return self._send(200, [alert_view(db, a) for a in rows])
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
            m = re.fullmatch(r"/api/alerts/(\d{1,9})/label", urlparse(self.path).path)
            if not m:
                raise Refused(404, "not found")
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                raise Refused(413, "body too large or empty")
            try:
                body = json.loads(self.rfile.read(n))
            except json.JSONDecodeError:
                raise Refused(400, "invalid JSON")
            return self._send(200, classify(int(m.group(1)), body))
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
