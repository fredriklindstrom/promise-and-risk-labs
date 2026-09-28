"""Per-device traffic, summarised from the gateway's flow log (UniFi traffic-flows), for segmentation.

What the gateway sees is traffic it routes: everything to and from the internet, and everything
between networks (VLANs). It does NOT see two devices on the same network talking to each other:
the switch carries that. So on a flat network "no local peers seen" is not evidence of no local
traffic. It becomes evidence once a device sits on its own network, where every attempt to reach
another device crosses the gateway (and shows up as blocked if the firewall stops it).

Stored as daily per-device totals, never raw flows. Enrichment only: a failure here never stops a poll.
"""
import re
import time
from collections import defaultdict

from . import normalize, store

EVERY_S = 900                 # collect every 15 minutes
MAX_WINDOW_S = 6 * 3600       # after downtime, look back at most this far
KEEP_DAYS = 30
KEY_CAP = 200                 # distinct (peer, service) per device, day and kind: a device picks its own peers
SUMMARY_DAYS = 7
VISIBILITY = ("The gateway records traffic it routes: to and from the internet, and between networks. "
              "Traffic between two devices on the same network goes through the switch and is not recorded, "
              "so no local peers seen is only evidence of no local traffic when the device is on a network of its own.")
INCOMPLETE = " Some collection windows in this period hit the page limit, so some flows were not recorded."
MAC_RE = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}")


def _mac(v):
    v = v.lower() if isinstance(v, str) else ""
    return v if MAC_RE.fullmatch(v) else None


def _str(v, n):
    return v[:n] if isinstance(v, str) and v else None


def _n(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def _service(f):
    s = _str(f.get("service"), 40)
    if s and s != "OTHER":
        return s
    proto, port = _str(f.get("protocol"), 10), f.get("dst_port")
    port = port if isinstance(port, int) and not isinstance(port, bool) and 0 <= port <= 65535 else None
    return f"{proto or '?'}/{port}" if port is not None else (proto or "OTHER")


def classify(f, clients, gear):
    """Rows (device mac, kind, peer, service) for one flow. kinds:
    internet      the device reached the internet (peer: the domain it named, else the region)
    internet_in   the internet reached the device (a port forward, or a reply the gateway logs as incoming)
    gateway       the device talked to the gateway or other UniFi gear (DNS, DHCP, NTP, management)
    local_out     the device reached another of your devices across networks (peer: that device's MAC)
    local_in      another of your devices reached this one across networks
    blocked       the firewall stopped a flow from this device (peer: the destination)"""
    src, dst = _mac(f.get("src_mac")), _mac(f.get("dst_mac"))
    svc, direction = _service(f), f.get("direction")
    region = _str(f.get("region"), 8)
    remote = _str(f.get("domain"), 253) or (f"region:{region}" if region else "internet")
    out = []
    if f.get("action") == "blocked" and src in clients:
        out.append((src, "blocked", dst or _str(f.get("dst_ip"), 45) or remote, svc))
        return out
    if direction == "outgoing" and src in clients:
        out.append((src, "internet", remote, svc))
        if region:  # kept apart from the domain, so a baseline can learn countries too
            out.append((src, "region", region, "*"))
    elif direction == "incoming" and dst in clients:
        out.append((dst, "internet_in", f"region:{region}" if region else "internet", svc))
    elif direction == "local" and src in clients:
        if dst in clients and dst not in gear and dst != src:
            out += [(src, "local_out", dst, svc), (dst, "local_in", src, svc)]
        else:
            out.append((src, "gateway", "gateway", svc))
    return out


def collect(db, client, now=None):
    """Fetch flows since the last collection and add them to the daily per-device totals."""
    now = int(now or time.time())
    until = now * 1000
    # first run and after downtime: look back at most MAX_WINDOW_S (the page cap keeps it bounded)
    since = max(store.get_meta(db, "traffic_until_ms") or 0, (now - MAX_WINDOW_S) * 1000)
    flows = client.traffic_flows(since, until)
    clients = {d["mac"] for d in store.all_devices(db)}
    gear = {m for m in (_mac(d.get("mac")) for d in (store.get_meta(db, "own_radios") or {}).get("devices", [])
                        if isinstance(d, dict)) if m}
    totals = defaultdict(lambda: [0, 0])
    hours = defaultdict(lambda: [0, 0])
    for f in flows:
        t = f.get("time")
        if not isinstance(t, int) or not since <= t < until:  # a flow reported in two windows counts once
            continue
        lt = time.localtime(t / 1000)
        day = time.strftime("%Y-%m-%d", lt)
        for mac, kind, peer, svc in classify(f, clients, gear):
            k = totals[(mac, day, kind, peer, svc)]
            k[0] += 1
            k[1] += _n(f.get("bytes"))
            if kind in ("internet", "local_out"):  # the device's own activity, for its usual hours
                h = hours[(mac, day, lt.tm_hour)]
                h[0] += 1
                h[1] += _n(f.get("bytes"))
    store.traffic_add(db, [(*k, v[0], v[1]) for k, v in totals.items()], KEY_CAP)
    store.traffic_hours_add(db, [(*k, v[0], v[1]) for k, v in hours.items()])
    store.traffic_prune(db, time.strftime("%Y-%m-%d", time.localtime(now - KEEP_DAYS * 86400)), now - KEEP_DAYS * 86400)
    store.set_meta(db, "traffic_until_ms", until)
    store.set_meta(db, "traffic_at", now)
    if getattr(client, "flows_truncated", False):
        store.set_meta(db, "traffic_truncated_last", now)
    if not store.get_meta(db, "traffic_since"):
        store.set_meta(db, "traffic_since", now)
    return len(flows)


def _destination(peer, flows):
    if peer.startswith("region:"):
        return {"region": peer[7:], "flows": flows}
    if peer == "internet":
        return {"unnamed": True, "flows": flows}
    if peer == "other":
        return {"many_others": True, "flows": flows}  # past KEY_CAP distinct destinations that day
    return {"domain": normalize.label("domain", peer), "flows": flows}


def _window(db, now, days):
    first_day = time.strftime("%Y-%m-%d", time.localtime(now - days * 86400))
    cut = store.get_meta(db, "traffic_truncated_last")
    incomplete = bool(cut and cut >= now - days * 86400)
    return first_day, incomplete


def _peers(rows):
    peers = {}
    for _, peer, kind, flows in rows:
        p = peers.setdefault(peer, {"mac": peer, "reaches_it": 0, "it_reaches": 0})
        p["it_reaches" if kind == "local_out" else "reaches_it"] += flows
    return sorted(peers.values(), key=lambda p: -(p["reaches_it"] + p["it_reaches"]))


def summary(db, mac, now=None, days=SUMMARY_DAYS):
    """What the gateway saw this device do over the last `days`, aggregated in SQL (a device can't make
    this expensive: storage is capped per device and day). Domains are wrapped with who chose them."""
    now = int(now or time.time())
    since = store.get_meta(db, "traffic_since")
    if not since:
        return None
    first_day, incomplete = _window(db, now, days)
    tot = store.traffic_totals(db, first_day, mac)
    t = lambda kind: tot.get((mac, kind), (0, 0))
    peers = _peers(store.traffic_peers(db, first_day, mac))[:10]
    for p in peers:
        p["vendor"] = (store.get_device(db, p["mac"]) or {}).get("vendor")
    return {
        "observed_since": normalize.iso(since), "window_days": days,
        "internet": {"flows": t("internet")[0], "bytes": t("internet")[1],
                     "services": [{"service": s, "flows": f} for s, f, _ in
                                  store.traffic_top(db, mac, first_day, "internet", "service")],
                     "destinations": [_destination(p, f) for p, f, _ in
                                      store.traffic_top(db, mac, first_day, "internet", "peer")]},
        "from_internet_flows": t("internet_in")[0],
        "gateway_services": [s for s, _, _ in store.traffic_top(db, mac, first_day, "gateway", "service")],
        "local_peers_seen": peers,
        "blocked_flows": t("blocked")[0],
        "visibility": VISIBILITY + (INCOMPLETE if incomplete else ""),
    }


def briefs(db, now=None, days=SUMMARY_DAYS):
    """Per-device one-liners for the inventory, from three grouped queries for all devices together."""
    now = int(now or time.time())
    if not store.get_meta(db, "traffic_since"):
        return {}
    first_day, incomplete = _window(db, now, days)
    tot = store.traffic_totals(db, first_day)
    services = store.traffic_services_all(db, first_day, "internet")
    peer_rows = {}
    for row in store.traffic_peers(db, first_day):
        peer_rows.setdefault(row[0], []).append(row)
    out = {}
    for mac in {m for m, _ in tot} | set(peer_rows):
        t = lambda kind: tot.get((mac, kind), (0, 0))
        peers = _peers(peer_rows.get(mac, []))
        out[mac] = {"window_days": days, "internet_flows": t("internet")[0], "internet_bytes": t("internet")[1],
                    "services": [s for s, _ in services.get(mac, [])[:3]], "local_peers_seen": peers,
                    "blocked_flows": t("blocked")[0], "from_internet_flows": t("internet_in")[0],
                    "incomplete": incomplete}
    return out
