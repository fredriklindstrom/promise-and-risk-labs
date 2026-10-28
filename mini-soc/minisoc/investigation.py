"""The investigation record: four questions for every issue.

  1. What did you see?            filled in from the alert and its evidence
  2. What did you investigate?    the checks the Mini SOC ran, with what each found, plus your own checks
  3. What did you conclude?       your assessment, how confident you are, and why
  4. What would you recommend?    the action, and why

(The four-question structure for documenting an investigation is Daniel Oseghale's.)

Sections 1 and 2 are built only from controller data, the rule, and the Mini SOC's own lookups: no name a
device chose ever appears in them, so they can be shown and exported as plain text. Sections 3 and 4 are
yours. A report can be exported private (addresses and MACs as recorded) or public (scrubbed).
"""
import ipaddress
import re
import time

from . import actions, baseline, fingerprints, store

CONFIDENCE = ("high", "medium", "low")
RECORD_REQUIRED = ("medium", "high")  # closing these needs the full record


def _fmt(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "?"


def _device_line(db, table, mac, public=False):
    d = store.get_device(db, mac)
    if not d:
        return f"{mac}: not in the inventory"
    fp = fingerprints.resolve(table, d) or {}
    # public: the kind of device only; a specific make and model, or the network's name, identifies the household
    # a device can make its fingerprint read as another kind of device: say so wherever the name comes from it
    what = ((fp.get("family") or fp.get("type") or "unidentified device").lower() if public
            else f"{fp.get('model') or fp.get('type')} (UniFi fingerprint, not proof)" if fp.get("model") or fp.get("type")
            else f"{d['vendor']} (from its MAC address, which the device chooses)" if d.get("vendor")
            else "unidentified device")
    first = d.get("controller_first_seen") or d.get("first_seen_here")
    link = d.get("link") or "?"
    where = ("Wi-Fi" if link.startswith("wifi") else link) if public else link
    return f"{mac}: {what}, first seen {_fmt(first)}, {where}" + ("" if d.get("active") else ", offline now")


def seen(db, a):
    """Section 1: what the alert said, from the rule and the controller."""
    issue = a.get("issue_id") or a["id"]
    occ = store.issue_occurrences(db, issue)
    det = a.get("detail") if isinstance(a.get("detail"), dict) else {}
    ev = det.get("event_full") or det.get("event") or {}
    out = [f"{a['severity'].upper()} alert from rule {a['rule']}, raised {_fmt(a['created'])}.",
           f"Issue #{issue}: {store.issue_about(db, issue)}, {len(occ)} occurrence{'s' if len(occ) != 1 else ''}."]
    if ev.get("type"):
        out.append(f"Controller event: {ev.get('type')} ({ev.get('category') or '?'}), severity {ev.get('severity') or '?'}.")
    if ev.get("template"):
        out.append(f"Event template: {ev['template']}")
    return out


def checks(db, a, public=False, scr=None):
    """Section 2: the checks the Mini SOC ran on its own, each with what it found."""
    det = a.get("detail") if isinstance(a.get("detail"), dict) else {}
    table = fingerprints.load()
    out = []
    macs = store.entity_macs(a.get("entity"))
    for f in det.get("flows") or []:
        for end in (f.get("source") or {}, f.get("destination") or {}):
            m = end.get("mac") if isinstance(end.get("mac"), str) else None
            if m and m not in macs and store.get_device(db, m):
                macs.append(m)
    for m in macs[:6]:
        out.append({"check": "Inventory and fingerprint", "found": _device_line(db, table, m, public)})
        p = store.baseline_get(db, m)
        st = baseline.status(db, m, int(time.time()), baseline.mode(db, int(time.time())))
        out.append({"check": "Baseline", "found": f"{m}: {st.get('state')}" + (
            f", {p['days_seen']} days of traffic learned" if p else "")})
    if "flows" in det:
        fl = det.get("flows") or []
        if not fl:
            out.append({"check": "Gateway flow log", "found": "no matching blocked flow in the window"})
        for f in fl[:3]:
            s, d = f.get("source") or {}, f.get("destination") or {}
            # public: the rule's category only; a firewall policy's name is written by the console admin
            pick = (lambda r: r.get("category") or r.get("type")) if public else (lambda r: r.get("name") or r.get("category"))
            rule = ", ".join(pick(r) for r in f.get("rules") or [] if pick(r)) or "?"
            ip = (lambda v: (scr.ip(v) or "?") if scr else (f"[{v}]" if v and ":" in str(v) else v))
            out.append({"check": "Gateway flow log",
                        "found": f"{f.get('action')} {f.get('protocol') or ''} {ip(s.get('ip'))} port {s.get('port')} → "
                                 f"{ip(d.get('ip'))} port {d.get('port')}, rule {rule}, risk {f.get('risk')}"})
            for fact in f.get("facts") or []:
                out.append({"check": "Flow log, what doesn't add up", "found": fact})
    if det.get("flows_error"):
        out.append({"check": "Gateway flow log", "found": f"lookup failed ({det['flows_error']})"})
    loc = det.get("location")
    if isinstance(loc, dict) and loc.get("summary"):
        # public: the country only; the city of an admin login is where the owner is
        found = (loc.get("country") or "outside your network") if public else loc["summary"]
        out.append({"check": "IP location (local database)", "found": found})
    facts = (det.get("event") or {}).get("facts") if isinstance(det.get("event"), dict) else None
    if isinstance(facts, dict) and facts.get("pattern"):
        out.append({"check": "Wi-Fi evidence", "found": str(facts["pattern"])[:200]})
    issue = a.get("issue_id") or a["id"]
    occ = store.issue_occurrences(db, issue)
    if len(occ) > 1:
        out.append({"check": "Earlier occurrences", "found": f"{len(occ)} in issue #{issue}, first {_fmt(occ[0]['created'])}"})
    seen_found, unique = set(), []  # two identical blocked packets read as one line
    for c in out:
        if (c["check"], c["found"]) not in seen_found:
            seen_found.add((c["check"], c["found"]))
            unique.append(c)
    out = unique
    t = a.get("triage") or {}
    if t.get("assessment"):
        out.append({"check": "Qwen's assessment", "found": f"{t['assessment']} ({t.get('confidence') or '?'} confidence)"
                    + (", escalated to a person" if t.get("escalated") else "")})
    return out


def record(db, a, public=False, scr=None):
    """The whole record for the alert page: sections 1 and 2 filled in, 3 and 4 from your latest label."""
    lab = store.latest_label(db, a["id"]) or {}
    return {"seen": seen(db, a), "checks": checks(db, a, public, scr),
            "yours": {k: lab.get(k) for k in ("checks", "should_fire", "assessment", "confidence", "note",
                                               "action", "action_reason", "created")} if lab else None,
            "required_to_close": a["severity"] in RECORD_REQUIRED, "confidence_options": list(CONFIDENCE)}


# ---- the report --------------------------------------------------------------------------------------
def _known_names(db):
    """Names that identify this network or its devices, from the Mini SOC's own data: Wi-Fi names, network
    names, UniFi device names, and every hostname and alias it has seen. Longest first."""
    names = set(store.get_meta(db, "known_ssids") or [])
    names |= {r[0] for r in db.execute("SELECT DISTINCT network FROM devices WHERE network IS NOT NULL")}
    names |= {r[0] for r in db.execute("SELECT DISTINCT hostname FROM devices WHERE hostname IS NOT NULL")}
    names |= {r[0] for r in db.execute("SELECT DISTINCT name FROM devices WHERE name IS NOT NULL")}
    radios = store.get_meta(db, "own_radios") or {}
    names |= {d.get("name") for d in radios.get("devices", []) if isinstance(d, dict)}
    names |= {v.get("essid") for v in radios.get("vaps", []) if isinstance(v, dict)}
    # a device-chosen name that is one of the report's own words would only garble it
    return sorted((n for n in names if isinstance(n, str) and len(n) >= 4 and n.lower() not in REPORT_WORDS),
                  key=len, reverse=True)


REPORT_WORDS = {"outside", "private", "address", "device", "network", "exploits", "blocked", "allowed", "issue",
                "rule", "risk", "high", "medium", "low", "shadow", "learning", "your", "wi-fi", "wired"}
LOOSE_V4 = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
LOOSE_MAC = re.compile(r"[0-9A-Fa-f]{2}(?:[ :.-][0-9A-Fa-f]{2}){5}|[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}")
# a private subnet written partly ("192.168.50.x", "10.0.12.*", "172.16.4"): it still names the network
LOOSE_PRIV = re.compile(r"(?<![\d.])(?:192\.168|172\.(?:1[6-9]|2\d|3[01])|10\.\d{1,3})(?:\.(?:\d{1,3}|[xX*]))+")
LOOSE_V6 = re.compile(r"[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?:%\w+)?")


TOKEN_RE = re.compile(r"[0-9A-Za-z:.%\[\]_-]+")


class Scrubber:
    """Public reports: every MAC becomes "device A/B/...", every address "a private address" or "an outside
    address", and this network's names "your network" / "a device". Values are parsed, not pattern-matched,
    so compressed IPv6, zone ids, ::ffff: mappings, address:port and any MAC spelling are all caught. Names
    are replaced after addresses, so a device-chosen hostname can't break address detection."""

    def __init__(self, names=()):
        self.aliases, self.names = {}, names

    def mac(self, v):
        h = re.sub(r"[^0-9a-fA-F]", "", str(v or "")).lower()
        if len(h) != 12:
            return None
        return self.aliases.setdefault(h, f"device {chr(65 + len(self.aliases) % 26)}")

    def ip(self, v):
        t = str(v or "").strip("[]")
        t = t.split("%", 1)[0]
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", t):  # leading zeros (192.168.001.070) still name an address
            t = ".".join(str(int(x)) for x in t.split("."))
        try:
            a = ipaddress.ip_address(t)
        except ValueError:
            return None
        if getattr(a, "ipv4_mapped", None):
            a = a.ipv4_mapped
        return "a private address" if (a.is_private or a.is_loopback or a.is_link_local) else "an outside address"

    MAC_FORMS = re.compile(r"[0-9A-Fa-f]{2}([:.-]?[0-9A-Fa-f]{2}){5}|[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}")

    def _one(self, core):
        out = self.ip(core) or (self.mac(core) if self.MAC_FORMS.fullmatch(core) else None)
        if out is None and ":" in core:  # address:port or [v6]:port
            ip = self.ip(core.rsplit(":", 1)[0])
            out = ip and f"{ip}:{core.rsplit(':', 1)[1]}"
        return out

    def _token(self, tok):
        core = tok.strip("[].,;()_-")
        if not core:
            return tok
        # as written, then without a trailing or leading colon ("<mac>: Apple ...")
        for c in dict.fromkeys((core, core.rstrip(":"), core.strip(":"))):
            out = c and self._one(c)
            if out:
                return tok.replace(c, out)
        # a label glued on ("IP:10.0.0.9", "src:2001:db8::1", "mac-aa-bb-cc-dd-ee-ff"): scrub what follows it
        lab = re.match(r"([A-Za-z][A-Za-z0-9]*)([:=-])(.+)", core)
        if lab and re.search(r"[g-zG-Z]", lab.group(1)):  # a real word, not the first hex pair of a MAC
            return tok.replace(lab.group(3), self._token(lab.group(3)))
        if "-" in core and all(self._one(p) for p in core.split("-") if p):  # a range
            return tok.replace(core, "-".join(self._one(p) for p in core.split("-") if p))
        return tok

    def text(self, t):
        t = TOKEN_RE.sub(lambda m: self._token(m.group(0)), str(t))
        # fail-safe, before names (a device-chosen name could otherwise eat part of an address): anything still
        # shaped like an address or a MAC goes, even at the cost of over-scrubbing
        t = LOOSE_MAC.sub("[MAC removed]", t)
        t = LOOSE_V4.sub("[address removed]", t)
        t = LOOSE_PRIV.sub("[address removed]", t)
        t = LOOSE_V6.sub(lambda m: "[address removed]" if (m.group(0).count(":") >= 2 and
                         (re.search(r"[0-9A-Fa-f]{3,}", m.group(0)) or "::" in m.group(0))) else m.group(0), t)
        for n in self.names:
            t = re.sub(re.escape(n), "a device", t, flags=re.IGNORECASE)
        return t.replace("device device ", "device ")


def report(db, a, public=False):
    """The investigation as Markdown. Public: scrubbed of MACs and addresses (including in your own notes),
    no device names; still read it before publishing, since your notes are free text."""
    scr = Scrubber(_known_names(db)) if public else None
    rec = record(db, a, public, scr)
    y = rec["yours"] or {}
    s = scr.text if public else (lambda t: str(t))
    title = a["title"] if not public else f"{a['rule']} ({a['severity']})"
    lines = [f"# Investigation: {s(title)}", "",
             f"*Mini SOC, issue #{a.get('issue_id') or a['id']}, {_fmt(a['created'])}."
             + (" Public version: device and address details are replaced." if public else "") + "*", "",
             "## 1. What did I see?", ""] + [f"- {s(x)}" for x in rec["seen"]] + [
             "", "## 2. What did I investigate, and why?", "",
             "Checks the Mini SOC ran:", ""] + [f"- **{c['check']}:** {s(c['found'])}" for c in rec["checks"]]
    if y.get("checks"):
        lines += ["", "My own checks:", "", s(y["checks"])]
    act = actions.ACTIONS.get(y.get("action") or "", {}).get("title") if y.get("action") else None
    lines += ["", "## 3. What did I conclude?", "",
              f"**{(y.get('assessment') or 'not concluded yet').replace('_', ' ')}**"
              + (f", {y['confidence']} confidence" if y.get("confidence") else "")
              + (f". Should the alert have fired: {y['should_fire']}." if y.get("should_fire") else ""), ""]
    if y.get("note"):
        lines += [s(y["note"]), ""]
    lines += ["## 4. What would I recommend next?", "", f"**{act or 'no action chosen yet'}**", ""]
    if y.get("action_reason"):
        lines += [s(y["action_reason"]), ""]
    lines += ["---", "*Four-question format after Daniel Oseghale.*"]
    return "\n".join(lines) + "\n"
