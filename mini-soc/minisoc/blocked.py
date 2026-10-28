"""Every connection the gateway blocked, one by one, with its exact time and the name the device looked up.

The daily traffic totals say *that* a device was blocked; this says *when, to what, and by which rule*, to the
second, so a blocked attempt can be matched to what was running on the device at that moment. The controller
keeps its own flow log for about three days; this keeps KEEP_DAYS.

Read-only: the same traffic-flows query, filtered to blocked flows. The domain is the name the device asked
DNS for, so the device chose it: it's shown wrapped, never trusted. Location and network come from the local
IP database at view time.
"""
import ipaddress
import re
import time

from . import geo, normalize, store

EVERY_S = 900              # its own 15-minute timer in the watcher
BACKFILL_S = 68 * 3600     # first run: what the controller still holds, with three hours of slack before FLOOR_S
FLOOR_S = 71 * 3600        # nothing older is asked for: an hour inside what the controller keeps (72 h)
KEEP_DAYS = 90
PER_DAY_CAP = 3000         # rows per device per day; beyond that, only counted
PAGE_BUDGET = 100          # controller queries (pages) per run, at most
DEADLINE_S = 20            # and at most this long: it runs inside the poll, before events are read
PAGES_PER_CALL = 10
OVERLAP_S = 300            # each run re-reads this much of the last one
GROUPS_PER_DEVICE = 50     # on the all-devices page: no device can push another off it
MAC_RE = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}")


def _mac(v):
    v = v.lower() if isinstance(v, str) else ""
    return v if MAC_RE.fullmatch(v) else None


def _ip(v):
    try:
        return str(ipaddress.ip_address(str(v))) if v else ""
    except ValueError:
        return ""


def _port(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 65535 else -1


def _s(v, n):
    return v[:n] if isinstance(v, str) else ""


def row(f, clients):
    """One stored row for a blocked flow, credited to your device on either end; None if neither end is yours."""
    src, dst = _mac(f.get("src_mac")), _mac(f.get("dst_mac"))
    mac = src if src in clients else dst if dst in clients else None
    t = f.get("time")
    if not mac or not isinstance(t, int) or isinstance(t, bool) or not 0 < t < 2**42:
        return None
    pol = next((p for p in f.get("policies") or [] if isinstance(p, dict)), {})
    return {"time_ms": t, "day": time.strftime("%Y-%m-%d", time.localtime(t / 1000)), "mac": mac,
            "direction": _s(f.get("direction"), 12), "src_ip": _ip(f.get("src_ip")), "src_port": _port(f.get("src_port")),
            "dst_ip": _ip(f.get("dst_ip")), "dst_port": _port(f.get("dst_port")), "protocol": _s(f.get("protocol"), 8),
            "domain": _s(f.get("domain"), 253), "region": _s(f.get("region"), 8),
            "policy_type": _s(pol.get("type"), 40), "policy_name": _s(pol.get("name"), 80)}


def _ms(f):
    t = f.get("time")
    return t if isinstance(t, int) and not isinstance(t, bool) and 0 < t < 2**42 else None


def _walk(client, db, a, hi, cf, run, clients):
    """Read [a, hi) newest first, the order the controller returns flows in. A query cut short by the page cap
    has returned everything from its oldest flow up to hi, so the next query asks only for what's older: no page
    is read twice, whatever the flow rate. Returns (added, unread, error): unread is [a, hi, cf] for what's left
    when the page budget or deadline runs out or the controller fails (everything newer was read), or None when
    the window is done."""
    added = 0
    while hi > a:
        # every call gets its full page cap, so "cut short" means a busy moment, not a nearly spent budget
        if run["pages"] < PAGES_PER_CALL or time.monotonic() > run["deadline"]:
            return added, [a, hi, cf], None
        try:  # the controller's end time is inclusive: [a, hi - 1] is exactly [a, hi)
            flows = client.blocked_flows(a, hi - 1, max_pages=PAGES_PER_CALL, deadline=run["deadline"])
        except Exception as ex:
            return added, [a, hi, cf], ex
        run["pages"] -= max(1, getattr(client, "blocked_pages", 1))
        inside = [f for f in flows if _ms(f) is not None and a <= _ms(f) < hi]
        added += store.blocked_add(db, [r for r in (row(f, clients) for f in inside) if r], PER_DAY_CAP,
                                   count_from_ms=cf)
        if not getattr(client, "blocked_truncated", False):
            return added, None, None
        times = [_ms(f) for f in inside]
        if any(x < y for x, y in zip(times, times[1:])):  # not newest first: can't tell what's missing
            run["complete"] = False
        oldest = getattr(client, "blocked_oldest_ms", None)  # every row counts, blocked or not
        oldest = min([t for t in [oldest] + times if t is not None], default=None)
        if oldest is None or oldest >= hi:  # nothing to walk back from: keep the window for a later run
            run["complete"] = False
            return added, [a, hi, cf], None
        nxt = oldest + 1  # that millisecond may hold more; repeats are skipped when stored
        if nxt >= hi:     # a full query inside one millisecond: a flood; skip that millisecond
            run["complete"] = False
            nxt = oldest
        hi = nxt
    return added, None, None


def _gap(db, a_s, b_s, cause, now):
    """Record a stretch that will never be collected. Gaps with the same cause that touch are merged, and gaps
    are kept as long as the flows would have been (KEEP_DAYS), so none disappears from the record early."""
    if b_s <= a_s:
        return
    gaps = [g for g in store.get_meta(db, "blocked_gaps") or [] if len(g) >= 2 and g[1] >= now - KEEP_DAYS * 86400]
    gaps = [g if len(g) == 3 else [g[0], g[1], "off"] for g in gaps]
    for g in gaps:
        if g[2] == cause and g[0] <= b_s and a_s <= g[1]:
            g[0], g[1] = min(g[0], a_s), max(g[1], b_s)
            break
    else:
        gaps.append([a_s, b_s, cause])
    store.set_meta(db, "blocked_gaps", sorted(gaps)[-500:])


def _merge(windows):
    """Unread windows that overlap or touch become one; the earliest count_from wins (count, rather than miss)."""
    out = []
    for a, b, cf in sorted(windows):
        if out and a <= out[-1][1]:
            out[-1] = [out[-1][0], max(out[-1][1], b), min(out[-1][2], cf)]
        else:
            out.append([a, b, cf])
    return out


def collect(db, client, now=None):
    """Fetch blocked flows since the last collection and store them one by one. The newest window is read
    first, then anything an earlier run couldn't finish ("unread", kept in meta), newest first. Nothing is
    skipped: what doesn't fit in this run's page budget or deadline waits for the next one, until it's older
    than the controller keeps (then it's recorded as a gap). Each run re-reads OVERLAP_S, so a flow the
    controller reports late isn't lost; repeats are skipped when stored."""
    now = int(now or time.time())
    until, floor = now * 1000, (now - FLOOR_S) * 1000
    last = store.get_meta(db, "blocked_until_ms")
    if last and last < floor:  # off longer than the controller keeps its log
        _gap(db, last // 1000, floor // 1000, "off", now)
    todo = [[max(last - OVERLAP_S * 1000, floor), until, last] if last else [(now - BACKFILL_S) * 1000, until, 0]]
    for a, b, cf in sorted(store.get_meta(db, "blocked_unread") or [], key=lambda w: -w[1]):
        if b <= floor:
            _gap(db, a // 1000, b // 1000, "behind", now)
            continue
        if a < floor:
            _gap(db, a // 1000, floor // 1000, "behind", now)
        todo.append([max(a, floor), b, cf])
    clients = {d["mac"] for d in store.all_devices(db)}
    run = {"pages": PAGE_BUDGET, "deadline": time.monotonic() + DEADLINE_S, "complete": True}
    added, unread, error = 0, [], None
    for i, (a, b, cf) in enumerate(todo):
        if error:
            unread.append([a, b, cf])
            continue
        n, left, error = _walk(client, db, a, b, cf, run, clients)  # a failure keeps exactly what's unread
        added += n
        if left:
            unread.append(left)
    store.set_meta(db, "blocked_until_ms", until)
    unread = sorted(_merge(unread), key=lambda w: -w[1])  # an outage leaves overlapping windows: one each
    for a, b, cf in unread[50:]:  # more unread windows than kept: say so rather than drop them quietly
        _gap(db, a // 1000, b // 1000, "behind", now)
    unread = unread[:50]
    store.set_meta(db, "blocked_unread", unread)
    store.set_meta(db, "blocked_behind_since", min(w[0] for w in unread) // 1000 if unread else None)
    store.blocked_prune(db, (now - KEEP_DAYS * 86400) * 1000)
    if not run["complete"]:
        store.set_meta(db, "blocked_truncated_last", now)
    if not store.get_meta(db, "blocked_since"):
        store.set_meta(db, "blocked_since", todo[0][0] // 1000)
    if error:
        raise error
    return added


def _iso_s(ms):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ms / 1000))


def view(db, now=None, mac=None, days=7, recent_per_group=20):
    """Blocked connections in the last `days`, grouped by device, destination and rule, each group with its
    exact recent times. A group's destination is the domain the device asked for, else the address."""
    now = int(now or time.time())
    since_ms = (now - days * 86400) * 1000
    names = {d["mac"]: d for d in store.all_devices(db)}
    out = []
    groups = store.blocked_groups(db, since_ms, mac, per_device=None if mac else GROUPS_PER_DEVICE)
    recent = store.blocked_recent_all(db, since_ms, mac, recent_per_group)
    for g in groups:
        where = geo.lookup(g["an_address"]) if g["an_address"] else None
        where = where or {}
        d = names.get(g["mac"]) or {}
        times = [_iso_s(t) for t in recent.get(store.blocked_key(g), [])]
        ports = sorted({int(p) for p in str(g["ports"] or "").split(",") if p.strip().lstrip("-").isdigit() and int(p) >= 0})
        out.append({
            "mac": g["mac"], "device_name": normalize.label("name", d.get("name")),
            "device_hostname": normalize.label("hostname", d.get("hostname")),
            "destination": normalize.label("domain", g["dest"]) if g["named"] else {"address": g["dest"]},
            "country": where.get("country_code") or None, "network": where.get("network") or None,
            "asn": where.get("network_asn"), "protocol": g["protocol"] or None, "direction": g["direction"] or None,
            "ports": ports[:10], "addresses": g["addrs"], "rule": {"type": g["policy_type"] or None,
                                                                  "name": g["policy_name"] or None},
            "count": g["n"], "last": _iso_s(g["last_ms"]), "recent": times})
    first_day = time.strftime("%Y-%m-%d", time.localtime(now - days * 86400))
    cut = store.get_meta(db, "blocked_truncated_last")
    gaps = [x for x in (store.get_meta(db, "blocked_gaps") or []) if len(x) >= 2 and x[1] >= now - days * 86400]
    return {"days": days, "mac": mac, "groups": out, "group_total": store.blocked_group_count(db, since_ms, mac),
            "per_device_limit": None if mac else GROUPS_PER_DEVICE, "behind_since": store.get_meta(db, "blocked_behind_since"),
            "not_stored": store.blocked_overflow(db, first_day, mac),
            "since": store.get_meta(db, "blocked_since"), "collected_at": store.get_meta(db, "blocked_until_ms"),
            "incomplete": bool(cut and cut >= now - days * 86400),
            "gaps": [{"from": g[0], "to": g[1], "cause": g[2] if len(g) > 2 else "off"} for g in gaps],
            "about": ("Every connection the gateway blocked, to the second, kept for "
                      f"{KEEP_DAYS} days. The destination name is what the device asked DNS for (the device chose "
                      "it). Location and network owner come from a local IP database, for one of the group's "
                      "addresses: an estimate.")}
