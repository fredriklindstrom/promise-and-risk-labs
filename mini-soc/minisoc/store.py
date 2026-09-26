"""SQLite state: devices and their label history, events, alerts, known-normal baseline."""
import json
import pathlib
import re
import sqlite3
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS devices (
  mac TEXT PRIMARY KEY, hostname TEXT, name TEXT, vendor TEXT, ip TEXT, network TEXT,
  vlan INTEGER, link TEXT, controller_first_seen INTEGER, last_seen INTEGER,
  first_seen_here INTEGER, active INTEGER DEFAULT 0, pending_hostname TEXT);
CREATE TABLE IF NOT EXISTS label_history (
  mac TEXT, field TEXT, old TEXT, new TEXT, observed_at INTEGER);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, ts INTEGER, key TEXT, category TEXT, severity TEXT, routine INTEGER, raw TEXT);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created INTEGER, rule TEXT, severity TEXT,
  entity TEXT, title TEXT, detail TEXT, dedup_key TEXT UNIQUE,
  status TEXT DEFAULT 'open', triage TEXT, acked_at INTEGER, ack_note TEXT, notified INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS baseline (
  kind TEXT, value TEXT, note TEXT, added INTEGER, PRIMARY KEY (kind, value));
-- One row per decision about an alert, by tier: rules (L1), L2 (model), human.
CREATE TABLE IF NOT EXISTS verdicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id INTEGER, event_id TEXT, tier TEXT,
  rule TEXT, rule_version TEXT, model TEXT, quant TEXT, prompt_hash TEXT,
  verdict TEXT, confidence TEXT, escalated INTEGER, reason TEXT, evidence TEXT,
  raw_output TEXT, latency_s REAL, human_decision TEXT, created INTEGER);
CREATE INDEX IF NOT EXISTS verdicts_alert ON verdicts(alert_id);
-- Human classifications from the web UI: the training labels. Latest row per alert wins.
CREATE TABLE IF NOT EXISTS labels (
  id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id INTEGER, should_fire TEXT, assessment TEXT,
  action TEXT, note TEXT, closed INTEGER, created INTEGER);
CREATE INDEX IF NOT EXISTS labels_alert ON labels(alert_id, id);
CREATE INDEX IF NOT EXISTS alerts_status ON alerts(status, created);
CREATE INDEX IF NOT EXISTS alerts_rule ON alerts(rule);
"""

DEVICE_FIELDS = ("hostname", "name", "vendor", "ip", "network", "vlan", "link",
                 "controller_first_seen", "last_seen")
LABEL_FIELDS = ("hostname", "name")
STABILITY_POLLS = 3
QUIET_POLLS = 3


MIGRATIONS = [("devices", "pending_count", "INTEGER DEFAULT 0"), ("devices", "pending_quiet", "INTEGER DEFAULT 0"),
              ("verdicts", "prompt", "TEXT")]  # the exact messages sent to the model: a training example needs them


def connect(path=None, readonly=False):
    """readonly=True for readers (MCP server): no schema writes, and SQLite refuses any write."""
    p = pathlib.Path(path or config.DB_PATH)
    if readonly:
        db = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        return db
    db = sqlite3.connect(str(p), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    for table, col, decl in MIGRATIONS:
        if col not in {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    return db


def device_row(c):
    """Map a UniFi client record (active or known) to a stored device row."""
    return {"mac": c["mac"].lower(), "hostname": c.get("hostname") or None, "name": c.get("name") or None,
            "vendor": c.get("oui") or None, "ip": c.get("ip") or c.get("last_ip"),
            "network": c.get("network") or c.get("last_connection_network_name"),
            "vlan": c.get("vlan"),
            "link": ("wired" if c.get("is_wired") else f"wifi ({c['essid']})" if c.get("essid") else "wifi"),
            "controller_first_seen": c.get("first_seen"), "last_seen": c.get("last_seen")}


# meta ---------------------------------------------------------------------------------
def get_meta(db, k, default=None):
    r = db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return json.loads(r["v"]) if r else default


def set_meta(db, k, v):
    db.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))


# devices ------------------------------------------------------------------------------
def get_device(db, mac):
    r = db.execute("SELECT * FROM devices WHERE mac=?", (mac.lower(),)).fetchone()
    return dict(r) if r else None


def all_devices(db, active_only=False):
    q = "SELECT * FROM devices" + (" WHERE active=1" if active_only else "") + " ORDER BY mac"
    return [dict(r) for r in db.execute(q)]


def upsert_device(db, row, now, active=None):
    """Insert or update. Returns (is_new, label_changes[(field, old, new)])."""
    old = get_device(db, row["mac"])
    if old is None:
        db.execute(f"INSERT INTO devices(mac,{','.join(DEVICE_FIELDS)},first_seen_here,active) "
                   f"VALUES(?,{','.join('?' * len(DEVICE_FIELDS))},?,?)",
                   (row["mac"], *[row.get(f) for f in DEVICE_FIELDS], now, int(bool(active))))
        return True, []
    merged = {f: (row[f] if row.get(f) is not None else old.get(f)) for f in DEVICE_FIELDS}
    changes = []
    pending, pending_count = old.get("pending_hostname"), old.get("pending_count") or 0
    quiet = old.get("pending_quiet") or 0
    for f in LABEL_FIELDS:
        new = row.get(f) or None
        if f == "hostname":
            if new is None:  # a device that stops reporting a hostname hasn't renamed itself
                new = old.get(f)
            # UniFi flips hostnames between DHCP and mDNS sources for a poll or two. A change counts
            # once the same new value is seen on two consecutive polls, OR once the hostname has
            # differed from the stored one for STABILITY_POLLS polls in a row whatever the values
            # (so alternating between two values can't hold a change off forever).
            if new == (old.get(f) or None):
                # back to the stored name: the hold only clears after QUIET_POLLS calm polls in a row,
                # so a name that alternates with the stored one every poll still gets counted
                quiet += 1
                if quiet >= QUIET_POLLS:
                    pending, pending_count, quiet = None, 0, 0
            else:
                quiet = 0
                pending_count += 1
                if new != pending and pending_count < STABILITY_POLLS:
                    pending, new = new, old.get(f)  # first sighting: hold it
                else:
                    pending, pending_count, quiet = None, 0, 0
        if new != (old.get(f) or None):
            changes.append((f, old.get(f), new))
            db.execute("INSERT INTO label_history VALUES(?,?,?,?,?)", (row["mac"], f, old.get(f), new, now))
        merged[f] = new  # an alias can be cleared; keep that
    sets = ",".join(f"{f}=?" for f in DEVICE_FIELDS)
    db.execute(f"UPDATE devices SET {sets}, pending_hostname=?, pending_count=?, pending_quiet=?"
               f"{', active=?' if active is not None else ''} WHERE mac=?",
               (*[merged[f] for f in DEVICE_FIELDS], pending, pending_count, quiet,
                *([int(active)] if active is not None else []), row["mac"]))
    return False, changes


def mark_inactive_except(db, active_macs):
    """Devices the controller no longer lists as connected (including forgotten ones) go inactive."""
    for r in db.execute("SELECT mac FROM devices WHERE active=1").fetchall():
        if r["mac"] not in active_macs:
            db.execute("UPDATE devices SET active=0 WHERE mac=?", (r["mac"],))


ACKED_SUFFIX = re.compile(r"#acked\d+$")


def acked_and_unresolved(db, key):
    """Exact match on '<key>#acked<id>' (no LIKE wildcards: keys are compared as whole strings)."""
    for r in db.execute("SELECT dedup_key FROM alerts WHERE substr(dedup_key, 1, ?) = ?", (len(key), key)):
        if ACKED_SUFFIX.fullmatch(r["dedup_key"][len(key):]):
            return True
    return False


def release_resolved_acks(db, prefix, live_keys):
    """Acked alerts for a condition that is no longer present stop suppressing it."""
    for r in db.execute("SELECT id, dedup_key FROM alerts WHERE substr(dedup_key, 1, ?) = ?",
                        (len(prefix), prefix)).fetchall():
        if ACKED_SUFFIX.search(r["dedup_key"]) and ACKED_SUFFIX.sub("", r["dedup_key"]) not in live_keys:
            db.execute("UPDATE alerts SET dedup_key=dedup_key||'#resolved' WHERE id=?", (r["id"],))


def hostname_groups(db):
    """Hostnames reported by more than one device connected right now. Connected-only on purpose:
    a phone or Mac that rotated its private address leaves its old address in the history under
    the same name, and that is one device, not two. Case-insensitive, like name resolution."""
    groups = {}
    for d in db.execute("SELECT mac, hostname FROM devices WHERE hostname IS NOT NULL AND active=1"):
        groups.setdefault(d["hostname"].lower(), []).append(d["mac"])
    return {h: sorted(m) for h, m in groups.items() if len(m) > 1}


def recent_label_values(db, mac, field, since, before):
    """Values this device's label held in [since, before): either side of an EARLIER change.
    `before` must be the current poll's time, so the change being judged can't vouch for itself
    (without it, every hostname change looked like a revert and dropped to low)."""
    vals = set()
    for r in db.execute("SELECT old, new FROM label_history WHERE mac=? AND field=? AND observed_at>=? "
                        "AND observed_at<?", (mac.lower(), field, since, before)):
        vals.update(v for v in (r["old"], r["new"]) if v)
    return vals


def label_history(db, mac):
    return [dict(r) for r in db.execute(
        "SELECT * FROM label_history WHERE mac=? ORDER BY observed_at", (mac.lower(),))]


# events -------------------------------------------------------------------------------
def insert_event(db, e, routine):
    cur = db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?)",
                     (e["id"], e["timestamp"], e.get("key"), e.get("category"), e.get("severity"),
                      int(routine), json.dumps(e)))
    return cur.rowcount == 1


def events_since(db, since_ms, include_routine=False):
    q = "SELECT raw FROM events WHERE ts>=?" + ("" if include_routine else " AND routine=0") + " ORDER BY ts DESC"
    return [json.loads(r["raw"]) for r in db.execute(q, (since_ms,))]


def audit_events_for(db, mac):
    rows = db.execute("SELECT raw FROM events WHERE category='AUDIT' AND raw LIKE ? ORDER BY ts",
                      (f'%"{mac.lower()}"%',))
    out = []
    for r in rows:
        e = json.loads(r["raw"])
        if ((((e.get("parameters") or {}).get("OBJECT") or {}).get("id")) or "").lower() == mac.lower():
            out.append(e)
    return out


# alerts -------------------------------------------------------------------------------
def add_alert(db, rule, severity, entity, title, detail, dedup_key, now=None):
    cur = db.execute("INSERT OR IGNORE INTO alerts(created,rule,severity,entity,title,detail,dedup_key) "
                     "VALUES(?,?,?,?,?,?,?)",
                     (now or int(time.time()), rule, severity, entity, title, json.dumps(detail), dedup_key))
    return cur.lastrowid if cur.rowcount == 1 else None


def alerts(db, status="open", limit=200):
    q = "SELECT * FROM alerts" + (" WHERE status=?" if status else "") + " ORDER BY created DESC, id DESC LIMIT ?"
    rows = db.execute(q, ((status, limit) if status else (limit,)))
    out = []
    for r in rows:
        a = dict(r)
        a["detail"] = json.loads(a["detail"] or "null")
        a["triage"] = json.loads(a["triage"] or "null")
        out.append(a)
    return out


def get_alert(db, alert_id):
    r = db.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not r:
        return None
    a = dict(r)
    a["detail"] = json.loads(a["detail"] or "null")
    a["triage"] = json.loads(a["triage"] or "null")
    return a


def set_triage(db, alert_id, triage):
    db.execute("UPDATE alerts SET triage=? WHERE id=?", (json.dumps(triage), alert_id))


def mark_notified(db, alert_id):
    db.execute("UPDATE alerts SET notified=1 WHERE id=?", (alert_id,))


def ack(db, alert_id, note=""):
    """Acknowledging frees the dedup key, so the same thing happening again raises a new alert
    (dedup only suppresses repeats while an alert is still open)."""
    cur = db.execute("UPDATE alerts SET status='acked', acked_at=?, ack_note=?, dedup_key=dedup_key||'#acked'||id "
                     "WHERE id=? AND status='open'", (int(time.time()), note, alert_id))
    return cur.rowcount == 1


# verdicts -----------------------------------------------------------------------------
def add_verdict(db, alert, tier, rule_version, **kw):
    ev = ((alert.get("detail") or {}).get("event") or {}).get("id")
    db.execute("INSERT INTO verdicts(alert_id,event_id,tier,rule,rule_version,model,quant,prompt_hash,verdict,"
               "confidence,escalated,reason,evidence,raw_output,latency_s,human_decision,created,prompt) "
               "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (alert["id"], ev, tier, alert["rule"], rule_version, kw.get("model"), kw.get("quant"),
                kw.get("prompt_hash"), kw.get("verdict"), kw.get("confidence"),
                None if kw.get("escalated") is None else int(kw["escalated"]), kw.get("reason"),
                json.dumps(kw.get("evidence")) if kw.get("evidence") is not None else None,
                kw.get("raw_output"), kw.get("latency_s"), kw.get("human_decision"), int(time.time()),
                json.dumps(kw["prompt"]) if kw.get("prompt") is not None else None))


def verdicts_for(db, alert_id):
    return [dict(r) for r in db.execute("SELECT * FROM verdicts WHERE alert_id=? ORDER BY id", (alert_id,))]


# labels (human classification) ---------------------------------------------------------
def add_label(db, alert_id, should_fire, assessment, action, note, closed):
    db.execute("INSERT INTO labels(alert_id,should_fire,assessment,action,note,closed,created) VALUES(?,?,?,?,?,?,?)",
               (alert_id, should_fire, assessment, action, note, int(bool(closed)), int(time.time())))


def latest_label(db, alert_id):
    r = db.execute("SELECT * FROM labels WHERE alert_id=? ORDER BY id DESC LIMIT 1", (alert_id,)).fetchone()
    return dict(r) if r else None


def latest_labels(db):
    """alert_id -> latest label row."""
    rows = db.execute("SELECT l.* FROM labels l JOIN (SELECT alert_id, MAX(id) m FROM labels GROUP BY alert_id) x "
                      "ON l.id = x.m").fetchall()
    return {r["alert_id"]: dict(r) for r in rows}


def latest_l2(db, alert_id):
    r = db.execute("SELECT * FROM verdicts WHERE alert_id=? AND tier='L2' ORDER BY id DESC LIMIT 1", (alert_id,)).fetchone()
    return dict(r) if r else None


# baseline -----------------------------------------------------------------------------
def baseline_add(db, kind, value, note=""):
    if kind == "admin_ip":
        from .normalize import canonical_ip
        value = canonical_ip(value)
    db.execute("INSERT OR IGNORE INTO baseline VALUES(?,?,?,?)", (kind, value, note, int(time.time())))


def baseline_has(db, kind, value):
    return db.execute("SELECT 1 FROM baseline WHERE kind=? AND value=?", (kind, value)).fetchone() is not None


def baseline_list(db):
    return [dict(r) for r in db.execute("SELECT * FROM baseline ORDER BY kind, value")]
