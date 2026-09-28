"""Per-device baselines: what normal looks like for each device, learned from its traffic.

  learning   the first LEARN_DAYS after traffic collection started: profiles build, nothing is flagged
             except the device-type check (a light bridge moving gigabytes is odd from day one)
  shadow     deviations are recorded and shown in the web UI, never notified and never sent to Qwen,
             so their false-alarm rate can be measured against your verdicts before any of them pages

A profile is re-learned once a day from the previous LEARN_DAYS, today excluded. Anything that deviated
stays out of later profiles until you mark it expected, so a new behaviour can't become "normal" by
repetition, and a day flagged for volume never raises the volume bar.
The same blind spot as the traffic it's built from: only traffic that crosses the gateway.
"""
import json
import time
from collections import defaultdict

from . import fingerprints, normalize, segments, store, traffic

LEARN_DAYS = 14          # learning period, and the window each profile is learned from
MIN_DAYS = 7             # a device needs this many days with traffic before it's compared at all
VOLUME_FACTOR = 3        # today's internet bytes above FACTOR x the second-busiest learned day...
VOLUME_FLOOR = 100_000_000  # ...and above this, so a quiet device's normal wobble doesn't count
TYPE_VOLUME = {"IOT": 1_000_000_000}  # a day's internet traffic that's odd for the type, from day one
KINDS = {
    "new_service": "internet service it hasn't used before",
    "new_domain": "domain it hasn't contacted before (by main domain)",
    "new_region": "country or region it hasn't reached before",
    "new_peer": "one of your devices on another network it hasn't talked to before",
    "first_blocked": "first flow from it the firewall blocked",
    "volume": "far more internet traffic than on its usual busy days",
    "odd_hour": "active at an hour it never was while learning",
    "type_volume": "more internet traffic in a day than its device type should need",
}
VERDICTS = ("expected", "suspicious")
MULTI_PART = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.jp", "ne.jp", "or.jp",
              "com.br", "com.cn", "com.mx", "co.nz", "co.za", "com.sg", "com.tr", "co.in", "co.kr", "com.tw", "com.hk"}


def main_domain(name):
    """Registrable part of a name ('e1.apple.com' -> 'apple.com'); CDNs rotate the rest constantly."""
    labels = [x for x in str(name).lower().rstrip(".").split(".") if x]
    if len(labels) <= 2:
        return ".".join(labels)
    return ".".join(labels[-3:] if ".".join(labels[-2:]) in MULTI_PART else labels[-2:])


def mode(db, now):
    since = store.get_meta(db, "traffic_since")
    if not since:
        return "off"
    return "learning" if now - since < LEARN_DAYS * 86400 else "shadow"


def _day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def observe(db, mac, first_day, last_day):
    """What a device did between two days (inclusive), from the stored daily totals."""
    seen = {"days": set(), "services": set(), "domains": set(), "regions": set(), "peers": set(),
            "blocked": 0, "bytes_by_day": defaultdict(int), "hours": set(), "hours_days": defaultdict(set)}
    for r in store.traffic_rows(db, mac, first_day, last_day):
        seen["days"].add(r["day"])
        kind, peer = r["kind"], r["peer"]
        if kind == "internet":
            seen["services"].add(r["service"])
            seen["bytes_by_day"][r["day"]] += r["bytes"]
            if not peer.startswith("region:") and peer not in ("internet", "other"):
                seen["domains"].add(main_domain(peer))
        elif kind == "region":
            seen["regions"].add(peer)
        elif kind in ("local_out", "local_in"):
            seen["peers"].add(peer)
        elif kind == "blocked":
            seen["blocked"] += r["flows"]
    for r in store.traffic_hour_rows(db, mac, first_day, last_day):
        seen["hours"].add(r["hour"])
        seen["hours_days"][r["hour"]].add(r["day"])
    return seen


def _reference_bytes(by_day, flagged_days):
    """The second-busiest learned day, flagged days left out. Not the busiest: a ramp that stays just under
    the line would make each day the next day's ceiling. A weekly backup still has two busy days in the window."""
    days = sorted((b for d, b in by_day.items() if d not in flagged_days), reverse=True)
    return days[1] if len(days) > 1 else (days[0] if days else 0)


def learn(db, mac, now):
    """Profile from the LEARN_DAYS before today. Keys you marked suspicious are left out."""
    today = _day(now)
    seen = observe(db, mac, _day(now - LEARN_DAYS * 86400), _day(now - 86400))
    flagged = store.baseline_flagged(db, mac)
    drop = lambda kind, xs: sorted(x for x in xs if (kind, str(x)) not in flagged)
    days = sorted(d for d in seen["days"] if d < today)
    profile = {"days_seen": len(days), "services": drop("new_service", seen["services"]),
               "domains": drop("new_domain", seen["domains"]), "regions": drop("new_region", seen["regions"]),
               "peers": drop("new_peer", seen["peers"]), "blocked_before": seen["blocked"] > 0,
               "ref_day_bytes": _reference_bytes(seen["bytes_by_day"], store.baseline_flagged_days(db, mac, "volume")),
               "hours": drop("odd_hour", seen["hours"])}
    store.baseline_save(db, mac, now, profile)
    return profile


def check(db, mac, profile, fp_segment, now, current_mode):
    """Deviations of today's traffic from the profile, as (kind, key, detail)."""
    today = _day(now)
    seen = observe(db, mac, today, today)
    out = []
    day_bytes = seen["bytes_by_day"].get(today, 0)
    cap = TYPE_VOLUME.get(fp_segment)
    if cap and day_bytes > cap:
        # keyed by the day: volume is a daily measure, so each day over the line is its own entry
        out.append(("type_volume", today, {"bytes_today": day_bytes, "type": segments.SEGMENTS[fp_segment]["title"],
                                           "threshold": cap}))
    if current_mode != "shadow" or not profile or profile["days_seen"] < MIN_DAYS:
        return out
    for kind, key in (("new_service", "services"), ("new_domain", "domains"), ("new_region", "regions"),
                      ("new_peer", "peers")):
        for x in sorted(seen[key] - set(profile[key])):
            out.append((kind, str(x), {}))
    if seen["blocked"] and not profile["blocked_before"]:
        out.append(("first_blocked", "blocked", {"flows_today": seen["blocked"]}))
    ref = profile.get("ref_day_bytes", 0)
    if day_bytes > max(VOLUME_FACTOR * ref, VOLUME_FLOOR):
        out.append(("volume", today, {"bytes_today": day_bytes, "reference_day": ref}))
    for h in sorted(seen["hours"] - set(profile["hours"])):
        out.append(("odd_hour", str(h), {"hour": h}))
    return out


def update(db, now=None):
    """Called after each traffic collection: re-learn once a day, then record today's deviations."""
    now = int(now or time.time())
    m = mode(db, now)
    if m == "off":
        return 0
    relearn = store.get_meta(db, "baseline_learned_day") != _day(now)
    table = fingerprints.load()
    n = 0
    for d in store.all_devices(db):
        mac = d["mac"]
        profile = learn(db, mac, now) if relearn else store.baseline_get(db, mac)
        fp_segment = segments.baseline({"fingerprint": fingerprints.resolve(table, d)})
        for kind, key, detail in check(db, mac, profile, fp_segment, now, m):
            n += store.baseline_deviation(db, mac, _day(now), kind, key, detail, now)
    if relearn:
        store.set_meta(db, "baseline_learned_day", _day(now))
    return n


def status(db, mac, now, current_mode):
    p = store.baseline_get(db, mac)
    if current_mode == "off":
        return {"state": "off"}
    if not p or p["days_seen"] < MIN_DAYS:
        return {"state": "learning", "days_seen": (p or {}).get("days_seen", 0), "needs": MIN_DAYS}
    return {"state": current_mode, "days_seen": p["days_seen"], "services": len(p["services"]),
            "domains": len(p["domains"]), "regions": len(p["regions"]), "peers": len(p["peers"])}


def key_view(kind, key):
    """A deviation's key for display: domains wrapped with who chose them."""
    if kind == "new_domain":
        return {"domain": normalize.label("domain", key)}
    if kind == "new_region":
        return {"region": key}
    if kind == "new_peer":
        return {"device": key}
    return {"value": key}
