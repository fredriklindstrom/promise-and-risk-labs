"""Deterministic detection. These fire without the model and the model can never close them.

None of these rules reads what a label *says*. They fire on change (a new device, a changed
label, a configuration change, a login from an unknown address) and on UniFi's own security
classifications. An alias that carries a prompt injection is caught by `label_changed` because
an alias changed, not because of its words.
"""
import hashlib

from . import actions, normalize, store

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


def event_alerts(db, e):
    ev = normalize.typed_event(e)
    key, cat = e.get("key") or "", e.get("category")
    if cat == "AUDIT":
        ip = normalize.canonical_ip(ev.get("source_ip"))
        known = bool(ip) and store.baseline_has(db, "admin_ip", ip)
        if key == "ADMIN_ACCESS":
            # a known address lowers severity but never hides the login
            return [dict(rule="admin_login" if known else "admin_login_new_ip",
                         severity="low" if known else "medium", entity=ip or "unknown",
                         title=(f"Admin login from a known address: {ip}" if known
                                else f"Admin login from an address not seen before: {ip or 'unknown address'}"),
                         detail={"event": ev, "default_action": "CONFIRM_ADMIN_CHANGE"},
                         dedup_key=f"event:{e['id']}" if (known or not ip) else f"admin_ip:{ip}")]
        # every configuration change gets looked at, whoever made it and from wherever
        obj = ev.get("object") or {}
        return [dict(rule="config_change", severity="medium", entity=obj.get("id") or "site",
                     title=f"{ev.get('title') or 'Config change'}: {obj.get('kind') or key} by {ev.get('actor')} "
                           f"from {ip or 'unknown address'}{' (known address)' if known else ''}",
                     detail={"event": ev, "default_action": "CONFIRM_ADMIN_CHANGE"}, dedup_key=f"event:{e['id']}")]
    sev = "high" if (e.get("severity") or "").upper() in ("HIGH", "CRITICAL") else "medium"
    if SECURITY_KEY_MARKERS & set(key.split("_")):  # whole tokens, so IPS doesn't match IPSEC
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
