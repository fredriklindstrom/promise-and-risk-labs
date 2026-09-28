"""Segmentation catalogue, rule baseline, and the guards on the model's pick.

Recommendations only: nothing here changes the network. The model assigns each device a segment
from this catalogue; the guards then apply one principle:

  Evidence a device writes about itself (hostname, alias) can only lower trust, never raise it.

So Trusted and Infrastructure need UniFi's own fingerprint classification to agree with the
placement (and no MAC-vendor/fingerprint conflict), and both always wait for a person to confirm.
"""
import re

SEGMENTS = {
    "TRUSTED": {
        "title": "Trusted", "vlan": 10,
        "purpose": "Your own computers, phones, tablets and watches.",
        "policy": ["Can reach every other segment (to control, cast and print).",
                   "Nothing in another segment can start a connection to it; replies only."],
    },
    "MEDIA": {
        "title": "Media", "vlan": 30,
        "purpose": "TVs, streaming boxes, set-top boxes and game consoles.",
        "policy": ["Internet access.", "Trusted devices can reach it (casting and AirPlay need an mDNS reflector).",
                   "Can't start connections to Trusted, IoT or Infrastructure."],
    },
    "INTERNET": {
        "title": "Internet only", "vlan": 60,
        "purpose": "Devices that need the internet and nothing local: game consoles, and anything whose traffic "
                   "shows no local peers.",
        "policy": ["Internet access only.", "No connections to or from any other segment; client isolation inside it.",
                   "If a local feature stops working (remote play, casting to it), the blocked attempts show in "
                   "the traffic summary: move it to Media then."],
    },
    "IOT": {
        "title": "IoT", "vlan": 20,
        "purpose": "Smart-home devices, appliances, sensors, hubs and printers.",
        "policy": ["Internet access (tighten to the vendor's cloud where you can).",
                   "Trusted devices can reach it to control it.", "Can't start connections to any other segment."],
    },
    "CAMERAS": {
        "title": "Cameras", "vlan": 40,
        "purpose": "Security cameras, doorbells and recorders.",
        "policy": ["No internet unless the vendor's service needs it.",
                   "Only the recorder and Trusted devices can reach them."],
    },
    "INFRA": {
        "title": "Infrastructure", "vlan": 1,
        "purpose": "Network gear and servers others depend on: NAS, controllers, hubs that serve other devices.",
        "policy": ["Management access from Trusted only.", "No inbound from IoT, Media, Cameras or Guest."],
    },
    "GUEST": {
        "title": "Guest", "vlan": 50,
        "purpose": "Visitors' devices.",
        "policy": ["Internet only, with client isolation (guests can't see each other)."],
    },
    "IDENTIFY": {
        "title": "Identify first", "vlan": 99,
        "purpose": "Unknown devices, or evidence that contradicts itself: isolate until you know what it is.",
        "policy": ["Internet only, isolated from every other segment, until identified."],
    },
}

# Rule baseline from the UniFi fingerprint's family/type names (Ubiquiti's own classification).
BASELINE = [
    (r"smartphone|handheld|tablet|desktop|laptop|computer|smart watch|wearable", "TRUSTED"),
    (r"camera|doorbell|video recorder|nvr", "CAMERAS"),
    (r"game console", "INTERNET"),  # online play needs the internet, not your other devices
    (r"smart ?tv|set-?top|media player|streaming", "MEDIA"),
    (r"\bnas\b|storage|server|router|\bswitch\b|access point|firewall|gateway", "INFRA"),
    (r"printer|peripheral|home appliance|intelligent home|mattress|thermostat|sensor|lighting|bridge|plug|speaker|power supply", "IOT"),
]
NEEDS_CONTROLLER_EVIDENCE = {"TRUSTED", "INFRA"}
LOWEST_TRUST = {"IDENTIFY", "GUEST", "INTERNET"}  # moving a device here never needs a second opinion
ISOLATED = {"INTERNET", "GUEST", "IDENTIFY"}  # nothing local can reach a device in these


def _norm_vendor(v):
    m = re.match(r"[a-z0-9]+", (v or "").lower())
    return m.group(0) if m else ""


def evidence(dev_record):
    """What the controller itself vouches for, and whether its two sources disagree."""
    fp = dev_record.get("fingerprint") or {}
    oui = dev_record.get("vendor")
    fp_vendor = fp.get("vendor")
    conflict = bool(oui and fp_vendor and _norm_vendor(oui) != _norm_vendor(fp_vendor))
    return {"controller": bool(oui or fp), "conflict": conflict, "oui": oui, "fingerprint_vendor": fp_vendor}


def baseline(dev_record):
    fp = dev_record.get("fingerprint") or {}
    text = " ".join(str(fp.get(k) or "") for k in ("family", "type")).lower()
    for pattern, seg in BASELINE:
        if text and re.search(pattern, text):
            return seg
    return "IDENTIFY"


def guard(dev_record, model_pick):
    """(final segment, status, note). status: 'confirm' (Trusted and Infrastructure always wait for
    you), 'review' (the guard overrode the model), or 'ok'.

    Trusted and Infrastructure need more than *some* controller evidence: UniFi's own fingerprint
    classification (the rule baseline) has to say the same thing. Otherwise a device with an
    ordinary MAC vendor could talk the model into Infrastructure through its hostname."""
    ev = evidence(dev_record)
    rule = baseline(dev_record)
    pick = model_pick if model_pick in SEGMENTS else None
    note = None
    if pick is None:
        pick, note = rule, "no valid pick from the model: rule baseline used"
    if pick in NEEDS_CONTROLLER_EVIDENCE:
        if ev["conflict"]:
            why = "the controller's MAC vendor and fingerprint disagree"
        elif not ev["controller"]:
            why = "only the device's own name speaks for it"
        elif rule != pick:
            why = (f"UniFi's fingerprint doesn't support it (the fingerprint points to {SEGMENTS[rule]['title']})"
                   if dev_record.get("fingerprint") else "UniFi hasn't fingerprinted this device")
        else:
            why = None
        if why:
            return "IDENTIFY", "review", f"model suggested {SEGMENTS[pick]['title']}, but {why}"
        return pick, "confirm", note or f"{SEGMENTS[pick]['title']} placement waits for your confirmation"
    notes = [note] if note else []
    status = "review" if note else "ok"
    if rule != pick and pick not in LOWEST_TRUST and not note:
        # the model placed it higher than UniFi's own evidence does: that's a person's call, not "ok"
        notes.append(f"model's pick; UniFi's fingerprint points to {SEGMENTS[rule]['title']}"
                     if dev_record.get("fingerprint") else "model's pick; UniFi hasn't fingerprinted this device")
        status = "review"
    if pick in LOWEST_TRUST and rule in NEEDS_CONTROLLER_EVIDENCE and pick != rule:
        # isolating a device UniFi says is yours or infrastructure could break things: a person decides
        notes.append(f"model isolates a device UniFi's fingerprint puts in {SEGMENTS[rule]['title']}")
        status = "review"
    if ev["conflict"]:  # a mismatch alone is usually a third-party network chip: say so, don't block
        notes.append(f"MAC vendor ({ev['oui']}) and fingerprint vendor ({ev['fingerprint_vendor']}) differ; "
                     "common when the network chip comes from another maker")
    return pick, status, "; ".join(notes) or None


def observations(recs):
    """Deterministic findings about the plan as a whole (the model's notes are separate)."""
    out = []
    by_model = {}
    for r in recs:
        m = (r.get("fingerprint") or {}).get("model")
        if m:
            by_model.setdefault(m, []).append(r)
    for model, rs in by_model.items():
        links = {("wired" if (x.get("link") or "") == "wired" else "wifi") for x in rs}
        if len(rs) > 1 and links == {"wired", "wifi"}:
            out.append(f"{model}: seen on a wired address ({', '.join(x['mac'] for x in rs if x.get('link') == 'wired')}) "
                       f"and a Wi-Fi address ({', '.join(x['mac'] for x in rs if x.get('link') != 'wired')}) at once. If it's "
                       "one machine on both, it can bridge segments: turn off the interface you don't need.")
    return out


def keep_isolated(rec, final):
    """Device type says Internet only (a game console): a placement that lets your other devices reach it
    needs traffic showing they actually do. Returns (segment, note) when the device type wins, else None."""
    if baseline(rec) != "INTERNET" or final in ISOLATED:
        return None
    if any(p.get("reaches_it") for p in (rec.get("traffic") or {}).get("local_peers_seen") or []):
        return None  # only others reaching it counts: the device can make its own outbound flows
    return "INTERNET", (f"suggested {SEGMENTS[final]['title']}, but no traffic shows your other devices using it: "
                        "the device type says Internet only")


def traffic_note(final, traffic):
    """(note, needs_review) about a placement. Only visible cross-network peers count as evidence;
    silence on a shared network proves nothing (see traffic.VISIBILITY). Isolating a device that your
    other devices are seen using is a person's call."""
    if not traffic:
        return None, False
    peers = traffic.get("local_peers_seen") or []
    notes, cuts_off = [], final in ISOLATED and bool(peers)
    if cuts_off:
        n = sum(p["reaches_it"] + p["it_reaches"] for p in peers)
        notes.append(f"{n} flows with {len(peers)} of your devices across networks in the last "
                     f"{traffic['window_days']} days: this segment cuts them off, check they aren't needed")
    if traffic.get("blocked_flows"):
        notes.append(f"{traffic['blocked_flows']} flows from it were blocked in the last {traffic['window_days']} days")
    return "; ".join(notes) or None, cuts_off


def catalogue_text():
    return "\n".join(f"- {k}: {v['purpose']}" for k, v in SEGMENTS.items())
