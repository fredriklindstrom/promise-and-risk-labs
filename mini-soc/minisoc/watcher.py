"""One poll: pull (read-only) → store → rules → alerts → (maybe) model triage → notify → state.json.

  python -m minisoc.watcher              one poll, then exit (what launchd runs every 2 min)
  python -m minisoc.watcher --loop       keep polling in the foreground (development)
  python -m minisoc.watcher --no-triage --no-notify

The first poll is a bootstrap: it records every known device as the starting point and raises
no alerts except a summary, "identify this unnamed device" and "recently added device" items.
Notifications are rule-driven and go out before triage; triage then works through a backlog.
"""
import argparse
import json
import os
import tempfile
import time
import traceback
from collections import defaultdict

from . import actions, baseline, config, fingerprints, normalize, notify, rules, store, traffic, triage
from .unifi import UniFi

WHOAMI_EVERY = 3600
BOOTSTRAP_EVENT_DAYS = 7
TRIAGE_BUDGET_S = 240
FAILURES_BEFORE_NOTIFY = 3


def log(msg):
    config.STATE_DIR.mkdir(exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with open(config.LOG_PATH, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def rule_version():
    """Git commit of the detection code, +dirty when anything that changes decisions is uncommitted:
    the minisoc package, config.json or baseline_seed.json."""
    import subprocess
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=config.ROOT, capture_output=True,
                           text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "minisoc", "config.json",
                                "baseline_seed.json"], cwd=config.ROOT, timeout=5).returncode != 0
        return (h + ("+dirty" if dirty else "")) or "unversioned"
    except Exception:
        return "unversioned"


def networks(db):
    nets = defaultdict(lambda: {"active_clients": 0})
    for d in store.all_devices(db, active_only=True):
        n = nets[d.get("network") or "?"]
        n["active_clients"] += 1
        if d.get("vlan") is not None:
            n["vlan_id"] = d["vlan"]
    return [{"name": k, **v} for k, v in sorted(nets.items(), key=lambda kv: -kv[1]["active_clients"])]


def merged_rows(active, known):
    """Known-client table overlaid with the live record; live values win when present."""
    rows = {}
    for c in known:
        r = store.device_row(c)
        rows[r["mac"]] = r
    for c in active:
        r = store.device_row(c)
        base = rows.get(r["mac"], {})
        rows[r["mac"]] = {**base, **{k: v for k, v in r.items() if v is not None}}
        if r.get("uplink_mac"):  # the live connection is one fact: don't fill its gaps from an older one
            rows[r["mac"]].update({f: r.get(f) for f in store.UPLINK_FIELDS})
    return rows, {store.device_row(c)["mac"] for c in active}


def _skip_alert(what, ident, ex):
    return dict(rule="ingest_error", severity="medium", entity=str(ident),
                title=f"Skipped a {what} the monitor couldn't read ({type(ex).__name__}); coverage has a hole",
                detail={"what": what, "id": str(ident), "error": f"{type(ex).__name__}: {ex}"[:300]},
                dedup_key=f"ingest:{what}:{ident}")


DIGEST_EVERY_S = 3600  # repeats inside an open issue: at most one summary per issue per hour


def notify_pending(db, cfg):
    """Rule-driven and retried every poll until sent. Runs before triage, so a slow or dead model
    can't delay or lose a notification, and the model's view never gates one."""
    if not cfg.get("notify"):
        return
    rows = db.execute("SELECT id FROM alerts WHERE notified=0 AND status='open' "
                      "AND severity IN ('medium','high') ORDER BY id").fetchall()
    repeats = {}
    for r in rows:
        a = store.get_alert(db, r["id"])
        if store.issue_repeat(db, a):  # summarised per issue below, never dropped
            repeats.setdefault(a["issue_id"], []).append(a)
            continue
        issue = a.get("issue_id") or a["id"]
        head = f"{a['severity'].upper()} · #{issue} {a['rule']}" + ("" if issue == a["id"] else f" (occurrence #{a['id']})")
        if notify.mac(f"Qwen Mini SOC · {cfg['site_name']}", head, a["title"]):
            store.mark_notified(db, a["id"])
    now = int(time.time())
    for issue, occ in repeats.items():
        last = (db.execute("SELECT digest_at FROM alerts WHERE id=?", (issue,)).fetchone() or [None])[0] or 0
        if now - last < DIGEST_EVERY_S:
            continue  # stays pending: it goes out in the next summary for this issue
        first = store.get_alert(db, issue)
        if notify.mac(f"Qwen Mini SOC · {cfg['site_name']}",
                      f"{max((o['severity'] for o in occ), key=config.SEVERITIES.index).upper()} · #{issue}: "
                      f"{len(occ)} more occurrence{'s' if len(occ) > 1 else ''}", first["title"] if first else ""):
            for o in occ:
                store.mark_notified(db, o["id"], 2)
            db.execute("UPDATE alerts SET digest_at=? WHERE id=?", (now, issue))
    db.commit()


def triage_backlog(db, cfg, now, rv):
    """Triage open medium+ alerts that have no assessment yet, oldest first, within a time budget.
    What doesn't fit carries over to the next poll."""
    pending = db.execute("SELECT id FROM alerts WHERE triage IS NULL AND status='open' "
                         "AND severity IN ('medium','high') AND rule NOT IN ('ingest_error','event_gap') "
                         "ORDER BY id").fetchall()
    pending = [a for a in (store.get_alert(db, r["id"]) for r in pending)
               if config.sev_at_least(a["severity"], cfg["triage_min_severity"])]
    if not pending:
        return
    if not triage.server_up(cfg):
        log(f"triage: model server down, {len(pending)} alert(s) wait for the next poll")
        return
    notable = [normalize.typed_event(e) for e in store.events_since(db, (now - 86400) * 1000)][:40]
    known = [{k: b[k] for k in ("kind", "value", "note")} for b in store.baseline_list(db)]
    nets = networks(db)
    t_end = time.time() + TRIAGE_BUDGET_S
    for a in pending:
        if time.time() > t_end:
            log("triage: time budget spent, the rest carry over")
            break
        try:
            t = triage.assess(a, cfg, known, nets, notable, log)
        except Exception as ex:  # the alert stands without triage, and says so
            log(f"triage failed for alert {a['id']}: {ex!r}")
            t = {"assessment": "not_triaged", "confidence": "low", "escalated": True,
                 "reason": f"model unavailable: {type(ex).__name__}"}
        store.add_verdict(db, a, "L2", rv, verdict=t["assessment"], confidence=t.get("confidence"),
                          escalated=t["escalated"], reason=t.get("reason"), evidence=t.get("evidence"),
                          model=t.get("model"), quant=t.get("quant"), prompt_hash=t.get("prompt_hash"),
                          raw_output=t.get("raw_output"), latency_s=t.get("seconds"), prompt=t.get("prompt"))
        store.set_triage(db, a["id"], {k: v for k, v in t.items() if k not in ("raw_output", "prompt")})
        db.commit()


def poll(client, db, cfg, now=None, do_triage=True, do_notify=True):
    now = now or int(time.time())
    bootstrapped = store.get_meta(db, "bootstrapped", False)
    env = cfg.get("site_environment")
    store.set_meta(db, "site_environment", env if env in ("dense", "isolated") else "unknown")
    if do_notify:
        notify_pending(db, cfg)  # anything a previous poll committed but couldn't send

    if now - store.get_meta(db, "whoami_at", 0) > WHOAMI_EVERY:
        store.set_meta(db, "whoami", client.whoami())
        store.set_meta(db, "whoami_at", now)
        fingerprints.load(client)  # refreshes the cached name table when it's over a week old
        if hasattr(client, "own_radios"):
            try:
                radios = client.own_radios()
                store.set_meta(db, "own_radios", radios)
                # remember every name your radios have broadcast: a copy of a network that's off the air
                # (a scheduled guest WLAN) is the most effective evil twin there is
                known = set(store.get_meta(db, "known_ssids") or [])
                store.set_meta(db, "known_ssids", sorted(known | {v["essid"] for v in radios["vaps"] if v.get("essid")}))
            except Exception as ex:  # enrichment only: a failure here must not stop the poll
                log(f"own_radios refresh failed: {type(ex).__name__}")

    candidates = []
    rows, active_macs = merged_rows(client.clients_active(), client.clients_known())
    device_changes = []
    for mac, row in rows.items():
        try:
            is_new, changes = store.upsert_device(db, row, now, active=mac in active_macs)
        except Exception as ex:  # one bad record must not stop the poll
            candidates.append(_skip_alert("device record", mac, ex))
            continue
        if bootstrapped and (is_new or changes):
            device_changes.append((mac, is_new, changes))
    store.mark_inactive_except(db, active_macs)
    if hasattr(client, "traffic_flows") and now - (store.get_meta(db, "traffic_at") or 0) >= traffic.EVERY_S:
        try:  # enrichment for segmentation: a failure here must not stop the poll
            traffic.collect(db, client, now)
        except Exception as ex:
            log(f"traffic refresh failed: {type(ex).__name__}")
        try:  # shadow mode: records deviations for the web UI, never notifies, never calls the model
            baseline.update(db, now)
        except Exception as ex:
            log(f"baseline update failed: {type(ex).__name__}")

    cursor = store.get_meta(db, "event_cursor_ms")
    since = (cursor - cfg["event_overlap_seconds"] * 1000) if cursor else (now - BOOTSTRAP_EVENT_DAYS * 86400) * 1000
    fresh = []
    events = client.events_since(since)
    for e in sorted(events, key=lambda e: e.get("timestamp") or 0):
        try:
            routine = normalize.is_routine(e)
            if store.insert_event(db, e, routine) and not routine:
                fresh.append(e)
            # a controller clock running ahead must not push the window past real time
            cursor = max(cursor or 0, min(int(e["timestamp"]), now * 1000))
        except Exception as ex:
            candidates.append(_skip_alert("event", e.get("id") or normalize.event_fingerprint(e), ex))
    if cursor:
        store.set_meta(db, "event_cursor_ms", cursor)
    if getattr(client, "truncated", False):
        candidates.append(dict(rule="event_gap", severity="medium", entity="site",
                               title="Event log fetch hit its page limit: some older events in this window were not read",
                               detail={"since_ms": since, "fetched": len(events)}, dedup_key=f"gap:{since}"))

    if not bootstrapped:
        for kind, items in config.seed().items():
            if not kind.startswith("_"):
                for it in items:
                    store.baseline_add(db, kind, it["value"], it.get("note", ""))
        candidates += rules.bootstrap_alerts(store.all_devices(db), now)
        candidates += rules.collision_alerts(db, now)
        store.set_meta(db, "bootstrapped", True)
        store.set_meta(db, "bootstrapped_at", now)
    else:
        for mac, is_new, changes in device_changes:
            candidates += rules.device_alerts(db, mac, is_new, changes, now)
        candidates += rules.collision_alerts(db, now)
        for e in fresh:
            try:
                candidates += rules.event_alerts(db, e)
            except Exception as ex:
                candidates.append(_skip_alert("event", e.get("id") or normalize.event_fingerprint(e), ex))

    new_ids = [i for i in (store.add_alert(db, now=now, **a) for a in candidates) if i]
    new_alerts = [store.get_alert(db, i) for i in new_ids]
    rv = rule_version()
    for a in new_alerts:  # L1: the rule said yes
        store.add_verdict(db, a, "rules", rv, verdict="alert")
    store.set_meta(db, "last_poll_ok", now)
    db.commit()
    log(f"poll ok: {len(rows)} devices ({len(active_macs)} active), {len(fresh)} new notable events, "
        f"{len(new_alerts)} new alerts" + ("" if bootstrapped else " [bootstrap]"))

    if do_notify:
        notify_pending(db, cfg)
    if do_triage and bootstrapped:
        triage_backlog(db, cfg, now, rv)
    return new_alerts


LOCKED_ACTIONS = {"RESET_TAMPERED_LABEL"}  # a rule's tamper call can't be talked away by the tampered text


def recommended_action(alert):
    """The model's pick when it's valid and allowed, else the rule's default. Recommendation only.
    The model may not override a locked rule action, and may not answer NO_ACTION on a medium or
    high alert: the text it just read is exactly what an attacker would use to buy that answer."""
    t = alert.get("triage") or {}
    rule_default = (alert.get("detail") or {}).get("default_action") or actions.default_for(alert["rule"], alert.get("detail"))
    pick = t.get("action") if actions.allowed(alert["rule"], t.get("action"), rule_default) else None
    if pick == "NO_ACTION" and config.sev_at_least(alert["severity"], "medium"):
        pick = None
    if rule_default in LOCKED_ACTIONS:
        pick = None
    chosen, source = (pick, "model") if pick else (rule_default, "rule")
    return {"action_id": chosen, "action": actions.title(chosen), "action_source": source if chosen else None,
            "model_pick_rejected": t.get("action") if t.get("action") and not pick else None}


def build_state(db, cfg):
    """Pure read: the MCP server calls this too, so it must not write anything."""
    open_alerts = issues(db, "open")
    worst = max((config.SEVERITIES.index(a["severity"]) for a in open_alerts), default=0)
    last_ok = store.get_meta(db, "last_poll_ok")
    stale = not last_ok or time.time() - last_ok > 5 * cfg["poll_seconds"]
    status = "unknown" if stale else {3: "red", 2: "amber"}.get(worst, "green")
    who = store.get_meta(db, "whoami", {})
    # severity decides order; the model's view is only a tiebreak, so it can't push an alert out of view
    ordered = sorted(open_alerts, key=lambda a: (-config.SEVERITIES.index(a["severity"]),
                                                 not (a.get("triage") or {}).get("escalated"), -a["last_at"]))
    shown = [a for a in ordered if a["severity"] == "high"] + [a for a in ordered if a["severity"] != "high"][:20]
    return {
        "site": cfg["site_name"], "generated": int(time.time()), "status": status,
        "last_poll_ok": last_ok, "last_error": store.get_meta(db, "last_error"),
        "credential_warning": ("API key has owner rights; replace with a View Only key"
                               if who.get("is_owner") or who.get("is_super") else None),
        "counts": {s: sum(a["severity"] == s for a in open_alerts) for s in config.SEVERITIES},
        "devices_active": len(store.all_devices(db, active_only=True)),
        "escalated": sum(bool((a.get("triage") or {}).get("escalated")) for a in open_alerts),
        "open_alerts": [{"id": a["id"], "severity": a["severity"], "rule": a["rule"],
                         "title": a["title"] + (f" · {a['occurrences']} occurrences" if a["occurrences"] > 1 else ""),
                         "created": a["created"], "assessment": (a.get("triage") or {}).get("assessment"),
                         "confidence": (a.get("triage") or {}).get("confidence"),
                         "escalated": (a.get("triage") or {}).get("escalated"),
                         **recommended_action(a)} for a in shown],
    }


def issues(db, status="open", limit=500):
    """One row per issue: its first alert, with severity raised to the worst open occurrence, the
    occurrence count, when it last happened and how many of each kind. Pure read."""
    q = ("SELECT * FROM alerts WHERE (issue_id IS NULL OR issue_id=id)" + (" AND status=?" if status else "")
         + " ORDER BY created DESC, id DESC LIMIT ?")
    firsts = [store.get_alert(db, r["id"]) for r in db.execute(q, (status, limit) if status else (limit,))]
    out = []
    for a in firsts:
        occ = store.issue_occurrences(db, a["id"])
        live = [o for o in occ if o["status"] == "open"] or occ
        kinds = {}
        for o in occ:
            kinds[o["rule"]] = kinds.get(o["rule"], 0) + 1
        if any(o["escalated"] for o in live):  # any open occurrence the model escalated escalates the issue
            a = {**a, "triage": {**(a.get("triage") or {}), "escalated": True}}
        out.append({**a, "severity": max((o["severity"] for o in live), key=config.SEVERITIES.index, default=a["severity"]),
                    "occurrences": len(occ), "open_occurrences": sum(o["status"] == "open" for o in occ),
                    "last_at": max((o["created"] for o in occ), default=a["created"]), "kinds": kinds,
                    "macs": store.issue_macs(db, a["id"])})
    return out


def write_state(db, cfg):
    state = build_state(db, cfg)
    fd, tmp = tempfile.mkstemp(dir=config.STATE_DIR, prefix=".state-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=1)
    os.chmod(tmp, 0o644)  # the sandboxed widget reads it; it carries no labels, only rule titles
    os.replace(tmp, config.STATE_JSON)
    return state


def once(args, cfg):
    db = store.connect()
    try:
        poll(UniFi(cfg), db, cfg, do_triage=not args.no_triage, do_notify=not args.no_notify)
        store.set_meta(db, "last_error", None)
        store.set_meta(db, "consecutive_failures", 0)
    except Exception as ex:
        db.rollback()
        err = f"{type(ex).__name__}: {ex}"
        log(f"poll failed: {err}\n{traceback.format_exc()}")
        fails = (store.get_meta(db, "consecutive_failures", 0) or 0) + 1
        store.set_meta(db, "last_error", err[:300])
        store.set_meta(db, "consecutive_failures", fails)
        if fails == FAILURES_BEFORE_NOTIFY and cfg.get("notify") and not args.no_notify:
            notify.mac(f"Qwen Mini SOC · {cfg['site_name']}", f"Polling has failed {fails} times in a row", err[:200])
    db.commit()
    write_state(db, cfg)
    db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--no-triage", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    args = ap.parse_args()
    cfg = config.load()
    os.umask(0o077)  # the store, logs and raw events stay readable by this account only
    config.STATE_DIR.mkdir(exist_ok=True)
    os.chmod(config.STATE_DIR, 0o700)
    while True:
        once(args, cfg)
        if not args.loop:
            break
        time.sleep(cfg["poll_seconds"])


if __name__ == "__main__":
    main()
