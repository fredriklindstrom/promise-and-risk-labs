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
-- Segmentation: one run = one model pass over the inventory; assignments per device; your corrections.
CREATE TABLE IF NOT EXISTS seg_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created INTEGER, status TEXT, model TEXT, quant TEXT,
  prompt_hash TEXT, prompt TEXT, raw_output TEXT, notes TEXT, seconds REAL, error TEXT, rule_version TEXT,
  observations TEXT);
CREATE TABLE IF NOT EXISTS seg_assignments (
  run_id INTEGER, mac TEXT, current_network TEXT, current_vlan INTEGER, rule_segment TEXT,
  model_segment TEXT, model_confidence TEXT, model_reason TEXT, final_segment TEXT, status TEXT, note TEXT,
  record TEXT, PRIMARY KEY (run_id, mac));
CREATE TABLE IF NOT EXISTS seg_labels (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, mac TEXT, segment TEXT, note TEXT, created INTEGER);
CREATE TABLE IF NOT EXISTS traffic (
  mac TEXT, day TEXT, kind TEXT, peer TEXT, service TEXT, flows INTEGER, bytes INTEGER,
  PRIMARY KEY (mac, day, kind, peer, service));
CREATE TABLE IF NOT EXISTS traffic_hours (
  mac TEXT, day TEXT, hour INTEGER, flows INTEGER, bytes INTEGER, PRIMARY KEY (mac, day, hour));
CREATE TABLE IF NOT EXISTS device_baselines (mac TEXT PRIMARY KEY, learned_at INTEGER, profile TEXT);
CREATE TABLE IF NOT EXISTS baseline_deviations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mac TEXT, day TEXT, kind TEXT, key TEXT, detail TEXT,
  first_at INTEGER, last_at INTEGER, UNIQUE (mac, day, kind, key));
CREATE TABLE IF NOT EXISTS deviation_labels (
  id INTEGER PRIMARY KEY AUTOINCREMENT, deviation_id INTEGER, verdict TEXT, note TEXT, created INTEGER);
CREATE INDEX IF NOT EXISTS alerts_status ON alerts(status, created);
CREATE INDEX IF NOT EXISTS alerts_rule ON alerts(rule);
"""

DEVICE_FIELDS = ("hostname", "name", "vendor", "ip", "network", "vlan", "link",
                 "controller_first_seen", "last_seen", "dev_id", "dev_vendor", "dev_family", "dev_cat", "os_name",
                 "uplink_mac", "uplink_name", "uplink_port", "link_mbps", "signal_dbm")
LABEL_FIELDS = ("hostname", "name")
STABILITY_POLLS = 3
QUIET_POLLS = 3


MIGRATIONS = [("devices", "pending_count", "INTEGER DEFAULT 0"), ("devices", "pending_quiet", "INTEGER DEFAULT 0"),
              ("verdicts", "prompt", "TEXT"),  # the exact messages sent to the model: a training example needs them
              ("devices", "dev_id", "INTEGER"), ("devices", "dev_vendor", "INTEGER"), ("devices", "dev_family", "INTEGER"),
              ("devices", "dev_cat", "INTEGER"), ("devices", "os_name", "INTEGER"),  # UniFi fingerprint ids
              ("seg_runs", "observations", "TEXT"),
              # where a client is plugged in: the UniFi switch/AP, the port (wired), negotiated speed, signal
              ("devices", "uplink_mac", "TEXT"), ("devices", "uplink_name", "TEXT"), ("devices", "uplink_port", "INTEGER"),
              ("devices", "link_mbps", "INTEGER"), ("devices", "signal_dbm", "INTEGER")]


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


UPLINK_FIELDS = ("uplink_mac", "uplink_name", "uplink_port", "link_mbps", "signal_dbm")


def _int(v, lo, hi):
    return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None


def _mac(v):
    return v.lower() if isinstance(v, str) and re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", v) else None


def uplink(c):
    """Where the client is connected. The live record (stat/sta) names the switch port or AP now; the
    known-client record keeps the last one, which is all there is for a device that's gone offline.
    A port only means something on a wired link; Wi-Fi rates arrive in kbps."""
    wired = bool(c.get("is_wired"))
    up = _mac(c.get("sw_mac") if wired else c.get("ap_mac")) or _mac(c.get("last_uplink_mac"))
    name = c.get("last_uplink_name")
    rate = c.get("tx_rate")
    return {"uplink_mac": up,
            # the name belongs to last_uplink_mac: after a roam it would name the previous AP
            "uplink_name": name[:128] if isinstance(name, str) and name and up == _mac(c.get("last_uplink_mac")) else None,
            "uplink_port": (_int(c.get("sw_port"), 1, 1024) or _int(c.get("last_uplink_remote_port"), 1, 1024))
                           if wired else None,
            "link_mbps": (_int(c.get("wired_rate_mbps"), 1, 400000) if wired
                          else _int(rate // 1000, 1, 100000) if _int(rate, 1, 10 ** 8) else None),
            "signal_dbm": None if wired else _int(c.get("signal"), -120, 0)}


def device_row(c):
    """Map a UniFi client record (active or known) to a stored device row."""
    return {**uplink(c),"mac": c["mac"].lower(), "hostname": c.get("hostname") or None, "name": c.get("name") or None,
            "vendor": c.get("oui") or None, "ip": c.get("ip") or c.get("last_ip"),
            "network": c.get("network") or c.get("last_connection_network_name"),
            "vlan": c.get("vlan"),
            "link": ("wired" if c.get("is_wired") else f"wifi ({c['essid']})" if c.get("essid") else "wifi"),
            "controller_first_seen": c.get("first_seen"), "last_seen": c.get("last_seen"),
            **{k: c.get(k) for k in ("dev_id", "dev_vendor", "dev_family", "dev_cat", "os_name")}}


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
    if row.get("uplink_mac"):  # the connection is one fact: never mix a new AP with an old switch port
        merged.update({f: row.get(f) for f in UPLINK_FIELDS})
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


_OWN_WIFI = {}


def own_wifi(db, limit=3000):
    """Your SSIDs and UniFi devices, as named in recent controller events (client connections name
    the WLAN; device and connection events name the UniFi device and its model). Cached per
    database state: a flood of rogue-AP events would otherwise re-read the same rows once per alert."""
    path = next((r[2] for r in db.execute("PRAGMA database_list") if r[1] == "main"), "") or id(db)
    state = tuple(db.execute("SELECT MAX(rowid), COUNT(*) FROM events").fetchone())
    hit = _OWN_WIFI.get((path, limit))
    if hit and hit[0] == state:
        return set(hit[1]), [dict(d) for d in hit[2]]
    ssids, devices = set(), {}
    for r in db.execute("SELECT raw FROM events ORDER BY ts DESC LIMIT ?", (limit,)):
        p = (json.loads(r["raw"]).get("parameters") or {})
        w = (p.get("WLAN") or {}).get("name")
        if w:
            ssids.add(w)
        d = p.get("DEVICE") or {}
        if d.get("id") and d.get("model"):
            devices.setdefault(d["id"].lower(), {"mac": d["id"].lower(), "name": d.get("name"), "model": d.get("model")})
    _OWN_WIFI[(path, limit)] = (state, frozenset(ssids), tuple(dict(d) for d in devices.values()))
    return ssids, list(devices.values())


def raw_event(db, event_id):
    r = db.execute("SELECT raw FROM events WHERE id=?", (event_id,)).fetchone()
    return json.loads(r["raw"]) if r else None


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


# segmentation --------------------------------------------------------------------------
def seg_run_start(db, model, quant, rule_version):
    cur = db.execute("INSERT INTO seg_runs(created,status,model,quant,rule_version) VALUES(?,?,?,?,?)",
                     (int(time.time()), "running", model, quant, rule_version))
    return cur.lastrowid


def seg_run_finish(db, run_id, **kw):
    cols = ("status", "prompt_hash", "prompt", "raw_output", "notes", "seconds", "error", "observations")
    vals = [json.dumps(kw[c]) if c in ("prompt", "notes", "observations") and kw.get(c) is not None else kw.get(c)
            for c in cols]
    db.execute(f"UPDATE seg_runs SET {', '.join(c + '=?' for c in cols)} WHERE id=?", (*vals, run_id))


def seg_fail_stale_runs(db):
    db.execute("UPDATE seg_runs SET status='failed', error=COALESCE(error, 'interrupted') WHERE status='running'")
    db.commit()


def seg_add_assignment(db, run_id, a):
    db.execute("INSERT OR REPLACE INTO seg_assignments VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
               (run_id, a["mac"], a.get("current_network"), a.get("current_vlan"), a["rule_segment"],
                a.get("model_segment"), a.get("model_confidence"), a.get("model_reason"), a["final_segment"],
                a["status"], a.get("note"), json.dumps(a["record"])))


def seg_latest_run(db, finished_only=False):
    q = "SELECT * FROM seg_runs" + (" WHERE status='done'" if finished_only else "") + " ORDER BY id DESC LIMIT 1"
    r = db.execute(q).fetchone()
    return dict(r) if r else None


def seg_assignments(db, run_id):
    out = []
    for r in db.execute("SELECT * FROM seg_assignments WHERE run_id=? ORDER BY final_segment, mac", (run_id,)):
        d = dict(r)
        d["record"] = json.loads(d["record"] or "{}")
        out.append(d)
    return out


def seg_add_label(db, run_id, mac, segment, note):
    db.execute("INSERT INTO seg_labels(run_id,mac,segment,note,created) VALUES(?,?,?,?,?)",
               (run_id, mac, segment, note, int(time.time())))


def seg_latest_labels(db, run_id):
    rows = db.execute("SELECT l.* FROM seg_labels l JOIN (SELECT mac, MAX(id) m FROM seg_labels WHERE run_id=? "
                      "GROUP BY mac) x ON l.id = x.m", (run_id,)).fetchall()
    return {r["mac"]: dict(r) for r in rows}


def seg_labels_latest_any(db):
    """Your most recent segment decision for each device, from any run."""
    rows = db.execute("SELECT l.* FROM seg_labels l JOIN (SELECT mac, MAX(id) m FROM seg_labels GROUP BY mac) x "
                      "ON l.id = x.m").fetchall()
    return {r["mac"]: dict(r) for r in rows}


# traffic (daily per-device totals from the gateway's flow log) ----------------------------
def traffic_add(db, rows, cap):
    """Add daily totals. A device chooses its own peers and ports, so each (device, day, kind) keeps at
    most `cap` distinct (peer, service) keys; anything new beyond that is folded into ("other", "other")."""
    for mac, day, kind, peer, service, flows, nbytes in rows:
        if not db.execute("SELECT 1 FROM traffic WHERE mac=? AND day=? AND kind=? AND peer=? AND service=?",
                          (mac, day, kind, peer, service)).fetchone():
            n = db.execute("SELECT COUNT(*) FROM traffic WHERE mac=? AND day=? AND kind=?", (mac, day, kind)).fetchone()[0]
            if n >= cap:
                # peers between your own devices are bounded already: keep the name, fold the port
                peer, service = (peer, "other") if kind in ("local_in", "local_out") else ("other", "other")
        db.execute("INSERT INTO traffic(mac,day,kind,peer,service,flows,bytes) VALUES(?,?,?,?,?,?,?) "
                   "ON CONFLICT(mac,day,kind,peer,service) DO UPDATE SET flows=flows+excluded.flows, "
                   "bytes=bytes+excluded.bytes", (mac, day, kind, peer, service, flows, nbytes))


def traffic_prune(db, before_day, before_ts):
    db.execute("DELETE FROM traffic WHERE day < ?", (before_day,))
    db.execute("DELETE FROM traffic_hours WHERE day < ?", (before_day,))
    # deviations you gave a verdict are kept: they're the false-alarm measurement and training data
    # an unreviewed deviation is pruned by when it was last seen: one still happening stays open, or a daily
    # behaviour nobody reviewed would quietly become "normal" once its first day aged out
    db.execute("DELETE FROM baseline_deviations WHERE last_at < ? AND id NOT IN "
               "(SELECT deviation_id FROM deviation_labels)", (before_ts,))


def traffic_hours_add(db, rows):
    db.executemany("INSERT INTO traffic_hours(mac,day,hour,flows,bytes) VALUES(?,?,?,?,?) ON CONFLICT(mac,day,hour) "
                   "DO UPDATE SET flows=flows+excluded.flows, bytes=bytes+excluded.bytes", rows)


def traffic_rows(db, mac, first_day, last_day):
    return [dict(r) for r in db.execute("SELECT kind, peer, service, day, flows, bytes FROM traffic "
                                        "WHERE mac=? AND day>=? AND day<=?", (mac, first_day, last_day))]


def traffic_hour_rows(db, mac, first_day, last_day):
    return [dict(r) for r in db.execute("SELECT day, hour FROM traffic_hours WHERE mac=? AND day>=? AND day<=? "
                                        "AND flows>0", (mac, first_day, last_day))]


# per-device baselines --------------------------------------------------------------------
def baseline_save(db, mac, now, profile):
    db.execute("INSERT INTO device_baselines(mac,learned_at,profile) VALUES(?,?,?) ON CONFLICT(mac) DO UPDATE "
               "SET learned_at=excluded.learned_at, profile=excluded.profile", (mac, now, json.dumps(profile)))


def baseline_get(db, mac):
    r = db.execute("SELECT profile FROM device_baselines WHERE mac=?", (mac,)).fetchone()
    return json.loads(r["profile"]) if r else None


def baseline_deviation(db, mac, day, kind, key, detail, now):
    """Record a deviation once per device, day, kind and key. Returns 1 when it's new."""
    # one open entry per device, kind and key: seen again (any day) while you haven't reviewed it, it's updated
    row = db.execute("SELECT id FROM baseline_deviations d WHERE mac=? AND kind=? AND key=? AND (day=? OR NOT EXISTS "
                     "(SELECT 1 FROM deviation_labels l WHERE l.deviation_id=d.id)) ORDER BY id DESC LIMIT 1",
                     (mac, kind, key, day)).fetchone()
    if row:
        db.execute("UPDATE baseline_deviations SET last_at=?, detail=? WHERE id=?", (now, json.dumps(detail), row[0]))
        return 0
    db.execute("INSERT INTO baseline_deviations(mac,day,kind,key,detail,first_at,last_at) VALUES(?,?,?,?,?,?,?)",
               (mac, day, kind, key, json.dumps(detail), now, now))
    return 1


def baseline_deviations(db, limit=300, per_device=20):
    """Unreviewed first, newest first, at most `per_device` per device so one noisy device can't push
    another's deviations off the page."""
    rows = db.execute(
        "SELECT * FROM (SELECT d.*, l.verdict, l.note, ROW_NUMBER() OVER (PARTITION BY d.mac ORDER BY l.verdict IS NOT "
        "NULL, d.first_at DESC, d.id DESC) rn FROM baseline_deviations d LEFT JOIN deviation_labels l ON l.id = "
        "(SELECT MAX(id) FROM deviation_labels WHERE deviation_id=d.id)) WHERE rn <= ? "
        "ORDER BY verdict IS NOT NULL, first_at DESC, id DESC LIMIT ?", (per_device, limit))
    out = []
    for r in rows:
        x = dict(r)
        x["detail"] = json.loads(x["detail"] or "{}")
        out.append(x)
    return out


def baseline_kind_stats(db):
    """Per kind: how many deviations, and your verdicts (the false-alarm measurement)."""
    return {r["kind"]: {"count": r["n"], "expected": r["e"] or 0, "suspicious": r["s"] or 0} for r in db.execute(
        "SELECT d.kind, COUNT(*) n, SUM(l.verdict='expected') e, SUM(l.verdict='suspicious') s FROM baseline_deviations d "
        "LEFT JOIN deviation_labels l ON l.id = (SELECT MAX(id) FROM deviation_labels WHERE deviation_id=d.id) "
        "GROUP BY d.kind")}


def baseline_open_counts(db, since_ts):
    return {r[0]: r[1] for r in db.execute(
        "SELECT mac, COUNT(*) FROM baseline_deviations d WHERE last_at>=? AND NOT EXISTS "
        "(SELECT 1 FROM deviation_labels l WHERE l.deviation_id=d.id) GROUP BY mac", (since_ts,))}


def deviation_label(db, deviation_id, verdict, note, now):
    db.execute("INSERT INTO deviation_labels(deviation_id,verdict,note,created) VALUES(?,?,?,?)",
               (deviation_id, verdict, note, now))


def baseline_flagged(db, mac):
    """(kind, key) pairs kept out of this device's learned profile: everything that deviated and you
    haven't marked expected (unreviewed or suspicious). A new behaviour can't become normal by repetition."""
    return {(r[0], r[1]) for r in db.execute(
        "SELECT d.kind, d.key FROM baseline_deviations d LEFT JOIN deviation_labels l ON l.id = "
        "(SELECT MAX(id) FROM deviation_labels WHERE deviation_id=d.id) WHERE d.mac=? AND "
        "(l.verdict IS NULL OR l.verdict='suspicious')", (mac,))}


def baseline_flagged_days(db, mac, kind):
    """Days with a deviation of this kind you haven't marked expected: kept out of volume learning."""
    return {r[0] for r in db.execute(
        "SELECT d.day FROM baseline_deviations d LEFT JOIN deviation_labels l ON l.id = "
        "(SELECT MAX(id) FROM deviation_labels WHERE deviation_id=d.id) WHERE d.mac=? AND d.kind=? AND "
        "(l.verdict IS NULL OR l.verdict='suspicious')", (mac, kind))}


def traffic_totals(db, since_day, mac=None):
    """{(mac, kind): (flows, bytes)}, summed in SQL."""
    q = "SELECT mac, kind, SUM(flows) f, SUM(bytes) b FROM traffic WHERE day>=?" + (" AND mac=?" if mac else "")
    rows = db.execute(q + " GROUP BY mac, kind", (since_day, mac) if mac else (since_day,))
    return {(r["mac"], r["kind"]): (r["f"], r["b"]) for r in rows}


def traffic_top(db, mac, since_day, kind, by, n=5):
    col = {"peer": "peer", "service": "service"}[by]  # fixed column names only
    return [(r[0], r[1], r[2]) for r in db.execute(
        f"SELECT {col}, SUM(flows) f, SUM(bytes) b FROM traffic WHERE mac=? AND day>=? AND kind=? "
        f"GROUP BY {col} ORDER BY f DESC LIMIT ?", (mac, since_day, kind, n))]


def traffic_services_all(db, since_day, kind):
    """{mac: [(service, flows)]} for every device, most flows first."""
    out = {}
    for r in db.execute("SELECT mac, service, SUM(flows) f FROM traffic WHERE day>=? AND kind=? "
                        "GROUP BY mac, service ORDER BY f DESC", (since_day, kind)):
        out.setdefault(r["mac"], []).append((r["service"], r["f"]))
    return out


def traffic_peers(db, since_day, mac=None):
    """[(mac, peer, kind, flows)] for cross-network device-to-device traffic."""
    q = ("SELECT mac, peer, kind, SUM(flows) f FROM traffic WHERE day>=? AND kind IN ('local_in','local_out')"
         + (" AND mac=?" if mac else "") + " GROUP BY mac, peer, kind")
    return [(r[0], r[1], r[2], r[3]) for r in db.execute(q, (since_day, mac) if mac else (since_day,))]


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
