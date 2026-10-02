"""Flow evidence for UniFi security events: the blocked flow behind "Threat Detected and Blocked".

The event itself names only a client and an address. The gateway's flow log has the rest: the source and
destination address, ports, protocol, the rule that fired (category and name), and the risk. This finds the
flow(s) that match the event, describes each endpoint against your own inventory, and states plainly what
doesn't add up (a source address on none of your networks; a packet addressed to the device that sent it).

Read-only: one traffic-flows query per security event. Device names are wrapped with who wrote them.
"""
import ipaddress
import re

from . import normalize, store

BEFORE_MS, AFTER_MS = 120_000, 30_000
MAX_PAGES = 3
MAX_FLOWS = 5
MAC_RE = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}")


def _mac(v):
    v = v.lower() if isinstance(v, str) else ""
    return v if MAC_RE.fullmatch(v) else None


def _ip(v):
    try:
        return str(ipaddress.ip_address(str(v))) if v else None
    except ValueError:
        return None


def _port(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 65535 else None


def _text(v, n=60):
    return v[:n] if isinstance(v, str) and v else None


def endpoints(e):
    """What the event itself says: source/destination client MACs and addresses."""
    p = e.get("parameters") or {}
    get = lambda k, f="id": (p.get(k) or {}).get(f) if isinstance(p.get(k), dict) else None
    return {"src_mac": _mac(get("SRC_CLIENT")), "dst_mac": _mac(get("DST_CLIENT")),
            "src_ip": _ip(get("SRC_IP")), "dst_ip": _ip(get("DST_IP"))}


def _device(db, mac=None, ip=None):
    d = store.get_device(db, mac) if mac else None
    if not d and ip:
        r = db.execute("SELECT mac FROM devices WHERE ip=? ORDER BY active DESC, last_seen DESC LIMIT 1", (ip,)).fetchone()
        d = store.get_device(db, r[0]) if r else None
    if not d:
        return None
    return {"mac": d["mac"], "ip": d.get("ip"), "hostname": normalize.label("hostname", d.get("hostname")),
            "name": normalize.label("name", d.get("name")), "network": d.get("network")}


def _endpoint(db, mac, ip, port):
    out = {"ip": ip, "port": port, "mac": mac}
    dev = _device(db, mac=mac)
    out["mac_is"] = dev or ("not one of your client devices (a gateway or switch interface, or unknown)" if mac else None)
    by_ip = _device(db, ip=ip) if ip else None
    if by_ip and (not dev or by_ip["mac"] != dev["mac"]):
        out["ip_belongs_to"] = by_ip
    return {k: v for k, v in out.items() if v is not None}


def _in_networks(ip, subnets):
    try:
        a = ipaddress.ip_address(ip)
        return any(a in ipaddress.ip_network(s, strict=False) for s in subnets)
    except ValueError:
        return False


def find_flows(client, db, e):
    """Up to MAX_FLOWS flows matching the event, nearest in time first, each with plain facts."""
    ts = e.get("timestamp")
    if not isinstance(ts, int) or not hasattr(client, "traffic_flows"):
        return None
    ep = endpoints(e)
    rows = client.traffic_flows(ts - BEFORE_MS, ts + AFTER_MS, max_pages=MAX_PAGES)
    subnets = {r.get("src_subnet") for r in rows if isinstance(r.get("src_subnet"), str)}

    def side(r, pairs):
        named = [(k, v) for k, v in pairs if v]
        return bool(named) and any(_mac(r.get(k)) == v if k.endswith("mac") else _ip(r.get(k)) == v for k, v in named)
    src_pairs = (("src_mac", ep["src_mac"]), ("src_ip", ep["src_ip"]))
    dst_pairs = (("dst_mac", ep["dst_mac"]), ("dst_ip", ep["dst_ip"]))
    blocked = [r for r in rows if r.get("action") == "blocked"]
    # both ends when the event names both; one end only as a fallback (a busy destination matches many flows)
    both = [r for r in blocked if side(r, src_pairs) and side(r, dst_pairs)]
    blocked = both or [r for r in blocked if side(r, src_pairs) or side(r, dst_pairs)]
    ips = [r for r in blocked if any(pol.get("type") == "INTRUSION_PREVENTION" for pol in r.get("policies") or [])]
    picked = sorted(ips or blocked, key=lambda r: abs((r.get("time") or 0) - ts))[:MAX_FLOWS]
    out = []
    for r in picked:
        src_ip, dst_ip = _ip(r.get("src_ip")), _ip(r.get("dst_ip"))
        src = _endpoint(db, _mac(r.get("src_mac")), src_ip, _port(r.get("src_port")))
        dst = _endpoint(db, _mac(r.get("dst_mac")), dst_ip, _port(r.get("dst_port")))
        facts = []
        src_dev = src.get("mac_is") if isinstance(src.get("mac_is"), dict) else None
        if src_ip and subnets and not _in_networks(src_ip, subnets):
            facts.append("the source address is on none of your networks")
        if src_dev and src_ip and src_dev.get("ip") and src_dev["ip"] != src_ip:
            facts.append(f"the source MAC is your device {src_dev['mac']}, whose own address is {src_dev['ip']}, "
                         f"not {src_ip}: it sent a packet with someone else's source address (a VPN or tunnel "
                         "app on it, address spoofing, or a routing leak)")
        dst_dev = dst.get("ip_belongs_to") or (dst.get("mac_is") if isinstance(dst.get("mac_is"), dict) else None)
        if src_dev and dst_dev and dst_dev.get("mac") == src_dev.get("mac"):
            facts.append("the destination address belongs to the same device that sent it: the packet is addressed "
                         "back to its own sender")
        out.append({"time": normalize.iso(r.get("time")), "action": r.get("action"), "risk": _text(r.get("risk"), 12),
                    "direction": _text(r.get("direction"), 12), "protocol": _text(r.get("protocol"), 8),
                    "service": _text(r.get("service"), 30), "network": _text(r.get("network"), 60),
                    "rules": [{k: _text(pol.get(k)) for k in ("type", "category", "name") if _text(pol.get(k))}
                              for pol in (r.get("policies") or [])][:3],
                    "source": src, "destination": dst, "facts": facts})
    return out


def title_suffix(flows):
    """Controller data only (rule category, addresses): never a name a device chose."""
    if not flows:
        return ""
    f = flows[0]
    cat = next((r.get("name") or r.get("category") for r in f.get("rules") or [] if r.get("name") or r.get("category")), None)
    s, d = f["source"].get("ip"), f["destination"].get("ip")
    parts = [x for x in (cat, f"{s} → {d}" if s and d else None) if x]
    return f" ({': '.join(parts)})" if parts else ""
