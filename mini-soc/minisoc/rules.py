"""Deterministic detection. These fire without the model and the model can never close them.

None of these rules reads what a label *says*. They fire on change (a new device, a changed
label, a configuration change, a login from an unknown address) and on UniFi's own security
classifications. An alias that carries a prompt injection is caught by `label_changed` because
an alias changed, not because of its words.
"""
import hashlib
import functools
import re

from . import actions, geo, normalize, store

# Categories UniFi uses for day-to-day operational noise. Anything outside these (and outside
# AUDIT, handled separately) is unfamiliar and gets surfaced rather than silently dropped.
OPERATIONAL = {"CLIENT_DEVICES", "INTERNET_AND_WAN", "SOFTWARE_UPDATES", "UNIFI_DEVICES"}
SECURITY_KEY_MARKERS = {"ROGUE", "THREAT", "IPS", "IDS", "INTRUSION", "HONEYPOT", "BLOCKED"}
RECENT_DEVICE_DAYS = 30
REVERT_WINDOW_S = 7 * 86400      # a hostname going back to a value it had this week is low


def _h(text):
    return hashlib.sha256((text or "").encode()).hexdigest()  # full hash: dedup keys must not collide


def _who(dev):
    return dev.get("name") or dev.get("hostname") or dev.get("vendor") or "unknown device"


def bootstrap_alerts(devices, now):
    """First run: nothing is 'new' yet. Summarise, and ask about devices the controller itself
    only started seeing recently, since there's no history here to vouch for them."""
    out = [dict(rule="monitoring_started", severity="info", entity="site",
                title=f"Monitoring started: {len(devices)} known devices recorded as the starting point",
                detail={"devices": len(devices)}, dedup_key="bootstrap")]
    for d in devices:
        fs = d.get("controller_first_seen")
        if d.get("active") and not d.get("hostname") and not d.get("name"):
            out.append(dict(rule="unidentified_device", severity="low", entity=d["mac"],
                            title=f"Connected device with no hostname or alias, identify and name it: "
                                  f"{d.get('vendor') or 'unknown vendor'} {d['mac']}",
                            detail={"device": normalize.client_record(d, now)},
                            dedup_key=f"unidentified:{d['mac']}"))
        elif d.get("active") and fs and now - fs < RECENT_DEVICE_DAYS * 86400:
            out.append(dict(rule="recent_device", severity="low", entity=d["mac"],
                            title=f"Recently added device, confirm you recognise it: {d.get('vendor') or 'unknown vendor'} {d['mac']}",
                            detail={"device": normalize.client_record(d, now)},
                            dedup_key=f"recent_device:{d['mac']}"))
    return out


def reads_like_text(label, field="hostname"):
    """Shape only, never what the words say. A hostname that isn't a valid DNS label, or an alias
    that's sentence-shaped (six or more words, or sentence punctuation), is the shape of text
    addressing a reader. Ordinary aliases ("Fredrik's iPhone") have spaces, so they get a looser test."""
    if not label:
        return False
    if field == "hostname":
        return not normalize.shape(label)["valid_hostname"]
    return (normalize.shape(label)["words"] >= 6
            or any(c in label for c in ":;!?\n\r\u2028\u2029\uff1a\uff1b\uff01\uff1f"))


def collision_key(host, macs):
    """The hostname is attacker text: hash it, so the key can't carry '#', ':', '%' or '_' structure."""
    return f"collision:{_h(host)}:{','.join(macs)}"


def shared_with(db, dev, now):
    if not dev.get("hostname"):
        return []
    group = store.hostname_groups(db).get(dev["hostname"].lower(), [])
    return [m for m in group if m != dev["mac"]]


def device_record(db, dev, now):
    return normalize.client_record(dev, now, [normalize.typed_event(e) for e in store.audit_events_for(db, dev["mac"])],
                                   shared_with(db, dev, now))


def collision_alerts(db, now):
    """One alert per hostname that more than one connected device reports. Two devices with
    one name is a naming problem, or one device impersonating another: either way a person looks."""
    out = []
    groups = store.hostname_groups(db)
    live_keys = {collision_key(h, m) for h, m in groups.items()}
    store.release_resolved_acks(db, "collision:", live_keys)  # a collision that cleared may alert again
    for host, macs in groups.items():
        key = collision_key(host, macs)
        if store.acked_and_unresolved(db, key):  # you already looked at this exact collision
            continue
        devs = [store.get_device(db, m) for m in macs]
        out.append(dict(rule="hostname_collision", severity="medium", entity=",".join(macs),
                        title=f"{len(macs)} devices report the same hostname: "
                              + ", ".join(f"{d.get('vendor') or 'unknown vendor'} {d['mac']}" for d in devs),
                        detail={"hostname": normalize.label("hostname", devs[0]["hostname"]),
                                "devices": [device_record(db, d, now) for d in devs],
                                "default_action": "RESET_TAMPERED_LABEL" if reads_like_text(devs[0]["hostname"])
                                                  else "RENAME_DEVICE"},
                        dedup_key=key))
    return out


def device_alerts(db, mac, is_new, changes, now):
    dev = store.get_device(db, mac)
    rec = device_record(db, dev, now)
    out = []
    if is_new:
        # A randomised address on Wi-Fi with an ordinary hostname is what every phone and guest shows
        # on first join: record it, don't page. An attacker picks their own MAC, so the address bit
        # alone never lowers severity: wired, nameless or oddly-shaped names stay medium.
        rnd = normalize.mac_randomised(mac)
        host = dev.get("hostname")
        phone_like = (rnd and (dev.get("link") or "").startswith("wifi") and bool(host)
                      and normalize.shape(host)["valid_hostname"])
        out.append(dict(rule="new_device", severity="low" if phone_like else "medium", entity=mac,
                        title=f"New device on the network: {dev.get('vendor') or 'unknown vendor'} {mac}"
                              + (" (randomised address)" if rnd else ""),
                        detail={"device": rec, "default_action": "RESET_TAMPERED_LABEL"
                                if reads_like_text(host) or reads_like_text(dev.get("name"), "name")
                                else "IDENTIFY_DEVICE"}, dedup_key=f"new_device:{mac}"))
    for field, old, new in changes:
        what = "console alias" if field == "name" else "hostname"
        # A hostname going back to a value this device already had this week is a flap (visible,
        # not paged). A value it has never had is medium on first sight, so a new name can't hide.
        revert = (field == "hostname"
                  and new in store.recent_label_values(db, mac, field, now - REVERT_WINDOW_S, now) - {old})
        default = ("RESET_TAMPERED_LABEL" if reads_like_text(new, field)
                   else "RENAME_DEVICE" if rec["facts"]["hostname_shared_with"] else "IDENTIFY_DEVICE")
        sev = "low" if revert else "medium"
        out.append(dict(rule="label_changed", severity=sev, entity=mac,
                        title=f"{what.capitalize()} changed on {dev.get('vendor') or mac}"
                              + (" (back to a name it had this week)" if revert else ""),
                        detail={"field": field, "written_by": normalize.SOURCES[field],
                                "old": normalize.label(field, old), "new": normalize.label(field, new),
                                "reverted_to_recent_value": revert, "device": rec, "default_action": default},
                        # severity in the key: an open low "revert" must not swallow a later medium
                        dedup_key=f"label:{mac}:{field}:{sev}:{_h(new)}"))
    return out


MAC_RE = re.compile(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}")
# Look-alikes folded to Latin, applied before and after case-folding (so capitals and small letters
# both land). Greek, Cyrillic, a few Latin variants, and digit swaps.
CONFUSABLE = str.maketrans({
    "Α": "a", "Β": "b", "Ε": "e", "Ζ": "z", "Η": "h", "Ι": "i", "Κ": "k", "Μ": "m", "Ν": "n", "Ο": "o",
    "Ρ": "p", "Τ": "t", "Υ": "y", "Χ": "x", "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o",
    "ρ": "p", "τ": "t", "υ": "u", "χ": "x", "η": "n",
    "А": "a", "В": "b", "Е": "e", "К": "k", "М": "m", "Н": "h", "О": "o", "Р": "p", "С": "c", "Т": "t",
    "У": "y", "Х": "x", "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p",
    "с": "c", "т": "t", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h", "ӏ": "l",
    "ƒ": "f", "ı": "i", "ł": "l", "ø": "o", "ß": "ss",
    "0": "o", "1": "i", "|": "i", "!": "i", "3": "e", "5": "s", "7": "t",
    "2": "z", "4": "a", "6": "b", "8": "b", "9": "g", "@": "a", "$": "s"})
# Look-alikes made of plain keyboard characters ("Horne" for Home, "AB VVifi"), folded after the rest;
# l is folded to i last because a small L and a capital I look the same in phone system fonts.
ASCII_PAIRS = (("rn", "m"), ("nn", "m"), ("vv", "w"), ("cl", "d"))
BLANK = {"\u3164", "\u115f", "\u1160", "\u2800", "\uffa0"}  # render as nothing, categorised as letters/symbols
DROP_CATEGORIES = {"Mn", "Me", "Cf", "Cc", "Zs", "Zl", "Zp", "Pd", "Pc", "Po", "Ps", "Pe", "Pi", "Pf", "Sm", "Sk"}


@functools.lru_cache(maxsize=8192)
def _ssid_key(name, pairs=True):
    """Comparison key for look-alike network names: accents, invisible and blank characters,
    spacing and punctuation removed; Greek/Cyrillic/Latin look-alikes and digit swaps folded."""
    import unicodedata
    n = unicodedata.normalize("NFKD", name).translate(CONFUSABLE).casefold().translate(CONFUSABLE)
    n = "".join(ch for ch in n if ch not in BLANK and unicodedata.category(ch) not in DROP_CATEGORIES)
    if pairs:  # a second key without them, because 'nn'->'m' shortens 'Anna' below the length checks
        for pair, one in ASCII_PAIRS:
            n = n.replace(pair, one)
    return n.replace("l", "i")


def _edit_distance(a, b, cap=3):
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:  # two letters swapped: one edit
                v = min(v, prev2[j - 2] + 1)
            cur.append(v)
        # past the cap in this row and the one before (a swap looks back two rows): can't come back
        if min(cur) > cap and (prev2 is None or min(prev) > cap):
            return cap + 1
        prev2, prev = prev, cur
    return prev[-1]


BIDI = set("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def plain_name(ssid):
    """True only for an ordinary name: printable ASCII once accents are removed, and no direction
    controls. An allowlist, because a blocklist of look-alikes can never be finished: Unicode has
    hundreds, and a right-to-left override makes 'emoH' display as 'Home'."""
    import unicodedata
    if not ssid or any(ch in BIDI for ch in ssid):
        return False
    n = "".join(ch for ch in unicodedata.normalize("NFKD", ssid) if unicodedata.category(ch) != "Mn")
    return all(" " <= ch <= "~" for ch in n)


def _contains_near(k, ok, cap):
    """True when some stretch of k is within `cap` edits of ok: a typo of your name with something
    added around it ('A8 Wifi Guest' next to 'AB Wifi'), which a whole-name comparison misses."""
    for size in range(max(1, len(ok) - cap), len(ok) + cap + 1):
        for i in range(0, max(0, len(k) - size) + 1):
            if _edit_distance(k[i:i + size], ok, cap) <= cap:
                return True
    return False


# Words that say nothing about whose network it is. Compared after folding, in both key forms.
_GENERIC_RAW = ("guest", "wifi", "wi-fi", "wireless", "wlan", "home", "net", "network", "iot", "5g", "2g", "24g",
                "5ghz", "2.4ghz", "ext", "extender", "free", "internet", "office", "mesh", "iphone", "android",
                "hotspot", "router", "my", "the")
GENERIC_WORDS = frozenset({_ssid_key(w, p) for w in _GENERIC_RAW for p in (True, False)})
_GENERIC_LONGEST_FIRST = sorted(GENERIC_WORDS, key=lambda g: (-len(g), g))  # stable across processes
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _words(name, pairs=True):
    """(length as written, comparison key) per word. Words split on any symbol and on camelCase,
    so 'Guest(AB)Net' and 'TheABNet' both yield 'AB'."""
    raw = [w for piece in re.split(r"[^\w@$!|]+|_+", name) for w in _CAMEL.split(piece) if _ssid_key(w, pairs)]
    out, i = [], 0
    while i < len(raw):  # re-join a generic word the split broke up ('Wi'+'Fi', 'Io'+'T', '5'+'GHz')
        for size in (4, 3, 2, 1):
            joined = "".join(raw[i:i + size])
            if size == 1 or (i + size <= len(raw) and _ssid_key(joined, pairs) in GENERIC_WORDS):
                out.append((len(joined), _ssid_key(joined, pairs)))
                i += size
                break
    return out


def _strip_generic(k):
    """Every form of the key with generic words peeled off either end ('myabnet' -> 'abnet', 'myab',
    'ab', ...). All of them, because a single greedy peel can eat the name itself ('EXTIOTEXT')."""
    seen, todo = {k}, [k]
    while todo and len(seen) < 64:
        x = todo.pop()
        for g in _GENERIC_LONGEST_FIRST:
            for y in ((x[len(g):] if len(x) > len(g) and x.startswith(g) else None),
                      (x[:-len(g)] if len(x) > len(g) and x.endswith(g) else None)):
                if y and y not in seen and len(seen) < 64:
                    seen.add(y)
                    todo.append(y)
    return seen


def _core(own, pairs=True):
    """The part of your network name that says whose it is: the name without its generic words
    ('ab' for 'AB Wifi', 'casarossi' for 'Casa Rossi'), or the whole name if it's all generic."""
    return "".join(w for _, w in _words(own, pairs) if w not in GENERIC_WORDS) or _ssid_key(own, pairs)


def resembles_yours(ssid, ssids):
    """A name that carries yours: contains it, its distinctive part, or a small typo of either, with
    or without extra text around it. Kept separate from 'look-alike' because it's also how
    neighbours sometimes name things: never lowered, never raised."""
    return _resembles(ssid, frozenset(ssids))


@functools.lru_cache(maxsize=4096)
def _resembles(ssid, ssids):
    # Two passes: keys with the letter-pair folds ('rn'->'m') and without, so a fold that shortens a
    # name can't carry it under a length check. The costly approximate checks run on the first only.
    for pairs in (True, False):
        k = _ssid_key(ssid, pairs)[:48]  # SSIDs are 32 bytes; the cap only bounds the work on junk input
        their = {w for _, w in _words(ssid, pairs)}
        peeled = _strip_generic(k)
        for own in ssids:
            ok, core = _ssid_key(own, pairs), _core(own, pairs)
            cap = 2 if len(ok) >= 8 else 1
            if len(ok) >= 4 and ok in k:
                return True
            if len(k) >= 6 and k in ok:  # a shortened form of yours ('Andersson' for 'Andersson Guest')
                return True
            if ok and _edit_distance(k, ok) <= cap:
                return True
            # the distinctive part with the generic words swapped ('AB Guest', 'AB-WLAN', 'Bill 5G').
            # Short ones must stand as a word or at an end, or 'IOT' would catch 'Patriots'.
            if 2 <= len(core) <= 3 and (core in their or any(x.startswith(core) or x.endswith(core) for x in peeled)):
                return True
            if len(core) >= 4 and core in k:
                return True
            own_keys = sorted(w for _, w in _words(own, pairs))
            if len(own_keys) >= 2 and own_keys == sorted(their):  # 'Wifi AB' for 'AB Wifi'
                return True
            if not pairs:
                continue
            if len(ok) >= 4 and _contains_near(k, ok, cap):
                return True
            if len(core) >= 4 and _contains_near(k, core, 2 if len(core) >= 8 else 1):
                return True
            for n, w in _words(own):  # one distinctive word of yours with the rest changed ('Andersson Free')
                if n >= 5 and len(w) >= 4 and w not in GENERIC_WORDS and _contains_near(k, w, 1):
                    return True
    return False


ENVIRONMENTS = {
    "dense": "a shared building (apartments, multi-tenant offices): neighbours' networks are normal here",
    "isolated": "a location where nothing else should be broadcasting nearby",
    "unknown": "surroundings not set (config.json site_environment)",
}


def wifi_facts(db, ssid, bssid):
    """What the controller knows about a network another radio broadcast. Equality checks only.
    A BSSID is a claim (any radio can use any), so a match is a pattern to name, never reassurance."""
    radios = store.get_meta(db, "own_radios") or {}
    vaps = radios.get("vaps") or []
    ssids, event_devices = store.own_wifi(db)
    ssids |= {v["essid"] for v in vaps if v.get("essid")}
    ssids |= set(store.get_meta(db, "known_ssids") or [])  # names your radios broadcast at any point, even if off now
    devices = {d["mac"]: d for d in event_devices}
    devices.update({d["mac"]: d for d in radios.get("devices") or [] if d.get("mac")})
    ssid = ssid if isinstance(ssid, str) else None
    bssid = bssid.lower() if isinstance(bssid, str) and MAC_RE.fullmatch(bssid.lower()) else None
    exact = [v for v in vaps if bssid and v["bssid"] == bssid]
    near = []
    if bssid:  # one entry per device, whether its base MAC or one of its radio addresses matched
        mid = bssid.split(":")[1:5]
        hit = {d["mac"] for d in devices.values() if d["mac"].split(":")[1:5] == mid}
        hit |= {v["device_mac"] for v in vaps if v.get("device_mac") and v["bssid"].split(":")[1:5] == mid}
        near = [devices.get(m, {"mac": m, "name": None, "model": None}) for m in sorted(hit)]
    # unknown beats "not yours": with no list of your networks there's nothing to compare against
    yours = (ssid in ssids) if (ssid and ssids) else None
    own_keys = {_ssid_key(x) for x in ssids}
    lookalike = bool(ssid and ssids and not yours and (_ssid_key(ssid) in own_keys or
                     _ssid_key(ssid.replace("@", "").replace("$", "")) in own_keys))
    resembles = bool(ssid and ssids and not yours and not lookalike and resembles_yours(ssid, ssids))
    env = store.get_meta(db, "site_environment")
    env = env if isinstance(env, str) and env in ENVIRONMENTS else "unknown"
    if exact:
        pid = "clone"
        pattern = ("clone: another radio is using the exact address of one of your access points' networks "
                   "(see bssid_is_one_of_your_radios). UniFi doesn't report its own radios as third-party, "
                   "so treat this as hostile")
    elif yours and near:
        pid = "evil_twin"
        pattern = ("evil-twin pattern: your network name on an address shaped like your access point's. "
                   "Treat as hostile until you've confirmed it in UniFi")
    elif yours:
        pid = "impersonation"
        pattern = "impersonation: your network name from hardware that isn't one of your access points"
    elif lookalike:
        pid = "lookalike"
        pattern = ("look-alike: a network name that differs from yours only in case, spacing or look-alike "
                   "characters. Treat as impersonation")
    elif resembles:
        pid = "resembles"
        pattern = ("resembles yours: a network name that contains yours or is a typo away from it, the usual "
                   "shape of a phishing lure (or a neighbour's naming). Check it")
    elif yours is None:
        pid = "unknown"
        pattern = ("unknown: the list of your network names isn't available, so this can't be compared. "
                   "Check it in UniFi")
    elif near:
        pid = "confirm"
        pattern = ("not a network your access points are configured to broadcast, on an address shaped like "
                   "your hardware's. It could be that device's own mesh or setup radio, or a forgery: confirm in UniFi")
    else:
        pid = "foreign"
        pattern = {"dense": "foreign: not your network name or your hardware. In a shared building that's most "
                            "likely a neighbour's network",
                   "isolated": "foreign: not your network name or your hardware, where nothing else should be "
                               "broadcasting. Treat as suspicious and find it",
                   "unknown": "foreign: not your network name or your hardware (a neighbour, or someone nearby)"}[env]
    wrap = lambda d: {"mac": d["mac"], "name": normalize.label("device_name", d.get("name")), "model": d.get("model")}
    return {"ssid_is_one_of_yours": yours, "your_ssids": [normalize.label("ssid", x) for x in sorted(ssids)],
            "bssid_is_one_of_your_radios": [{"bssid": v["bssid"], "essid": normalize.label("ssid", v.get("essid")),
                                             "device": normalize.label("device_name", v.get("device_name"))} for v in exact],
            "bssid_shares_bytes_with": [wrap(d) for d in near],
            "pattern": pattern, "pattern_id": pid, "plain_name": plain_name(ssid), "site_environment": env,
            "site_environment_means": ENVIRONMENTS[env], "radio_inventory_known": bool(vaps)}


TARGETS_YOU = {"clone", "evil_twin", "impersonation", "lookalike"}


def rogue_severity(facts, unifi_sev):
    """Surroundings (set by you, in config.json) only ever change the reading of a FOREIGN network.
    Anything aimed at your own network is high wherever you are: a crowded building is where an
    evil twin is easiest to run."""
    pid, env = facts.get("pattern_id"), facts.get("site_environment")
    if pid in TARGETS_YOU:
        return "high"
    if pid in ("foreign", "resembles") and env == "isolated":
        return "high"
    # Lowering is allowlisted: only a plainly ordinary, unrelated name in a dense site, and never
    # below UniFi's own high/critical. Anything unusual keeps UniFi's severity (and notifies).
    # It also needs your radio inventory: without it, an idle network of yours missing from recent
    # events would read as foreign, and a copy of it could be lowered.
    if (pid == "foreign" and env == "dense" and facts.get("plain_name") and facts.get("radio_inventory_known")
            and unifi_sev in ("low", "medium")):
        return "low"
    return unifi_sev


def event_alerts(db, e):
    ev = normalize.typed_event(e)
    key, cat = e.get("key") or "", e.get("category")
    if cat == "AUDIT":
        ip = normalize.canonical_ip(ev.get("source_ip"))
        known = bool(ip) and store.baseline_has(db, "admin_ip", ip)
        loc = geo.lookup(ip) if ip else None
        # the title gets the database's place names only; the network owner's name stays wrapped in the detail
        where = f" ({loc['summary'][:48]})" if loc and loc.get("summary") and loc["summary"] != "location unknown" else ""
        if key == "ADMIN_ACCESS":
            # a known address lowers severity but never hides the login
            return [dict(rule="admin_login" if known else "admin_login_new_ip",
                         severity="low" if known else "medium", entity=ip or "unknown",
                         title=(f"Admin login from a known address: {ip}{where}" if known
                                else f"Admin login from an address not seen before: {ip or 'unknown address'}{where}"),
                         detail={"event": ev, "location": loc, "default_action": "CONFIRM_ADMIN_CHANGE"},
                         dedup_key=f"event:{e['id']}" if (known or not ip) else f"admin_ip:{ip}")]
        # every configuration change gets looked at, whoever made it and from wherever
        obj = ev.get("object") or {}
        return [dict(rule="config_change", severity="medium", entity=obj.get("id") or "site",
                     title=f"{ev.get('title') or 'Config change'}: {obj.get('kind') or key} by {ev.get('actor')} "
                           f"from {ip or 'unknown address'}{' (known address)' if known else where}",
                     detail={"event": ev, "location": loc, "default_action": "CONFIRM_ADMIN_CHANGE"},
                     dedup_key=f"event:{e['id']}")]
    sev = "high" if (e.get("severity") or "").upper() in ("HIGH", "CRITICAL") else "medium"
    if SECURITY_KEY_MARKERS & set(key.split("_")):  # whole tokens, so IPS doesn't match IPSEC
        if ev.get("essid") or ev.get("bssid"):
            ev["facts"] = wifi_facts(db, (ev.get("essid") or {}).get("text"), (ev.get("bssid") or {}).get("value"))
            if "ROGUE" in key.split("_"):  # the surroundings only speak to rogue access points
                sev = rogue_severity(ev["facts"], sev)
        return [dict(rule="unifi_security_event", severity=sev, entity=(ev.get("device") or {}).get("mac") or "site",
                     title=f"UniFi security event: {ev.get('title') or key}",
                     detail={"event": ev, "default_action": "INVESTIGATE_ROGUE_AP" if "ROGUE" in key.split("_")
                             else "INVESTIGATE_SECURITY_EVENT"},
                     dedup_key=f"event:{e['id']}")]
    if cat not in OPERATIONAL:
        return [dict(rule="unfamiliar_event", severity="medium", entity="site",
                     title=f"Unfamiliar event category {cat}: {ev.get('title') or key}",
                     detail={"event": ev}, dedup_key=f"event:{e['id']}")]
    return []
