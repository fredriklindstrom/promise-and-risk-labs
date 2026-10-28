"""What is likely running on each device, and how exposed it is to a web-server flaw such as PHP2Shell.

An assessment, not an inventory of installed software: the Mini SOC is read-only and passive, so it never
scans a device. It works from three things it already has:
  type      UniFi's fingerprint and the MAC vendor: what kind of system the device is, and what that kind
            of system usually runs
  served    the traffic the gateway saw others start towards the device (from the internet, or from your
            other networks), and the controller's port forwards: what the device answers on
  name      the device's own name, which can only raise a device to "check", never lower it

"Unlikely" is given only to kinds of device named in CONSUMER below; anything not recognised is "unknown",
never "unlikely", because a wrong "unlikely" makes someone skip the one device that mattered. The gateway
can't see traffic within one network, so a web server only its own network uses doesn't show up; on a flat
network "serves nothing" proves little. Fixed text only: nothing a device chose is copied in.
"""
import re

from . import normalize

ADVISORIES = {
    "php2shell": {
        "title": "PHP2Shell (CVE-2026-109618, CVE-2026-109619)",
        "published": "2026-10-21",
        "summary": ("Two unauthenticated remote code execution flaws in PHP itself. Every PHP install that serves web "
                    "requests is in scope (Apache mod_php, PHP-FPM, php-cgi, LiteSpeed, FrankenPHP); the PHP project "
                    "rates mod_php the highest risk, per Rapid7. Exploitation has been reported since 21 October "
                    "2026 (watchTowr's honeypots); CISA hadn't listed it as exploited by 22 October."),
        "fixed": "8.2.35, 8.3.36, 8.4.27, 8.5.12",
        "older": ("PHP 7.4 to 8.1 have no official release: use a distribution or vendor package. "
                  "PHP 7.3 and older have no official fix: upgrade, or ask an LTS vendor."),
    },
}
CURRENT = "php2shell"
VISIBILITY = ("Read-only and passive: the Mini SOC never scans a device, so this is an assessment from the device's "
              "type, its MAC vendor, the controller's port forwards and the traffic the gateway saw, not a list of "
              "installed software. Traffic inside one network never reaches the gateway, so a web server that only "
              "its own network uses won't show here. The port forward list doesn't include UPnP or NAT-PMP mappings "
              "that devices open themselves, or IPv6 firewall rules, and traffic from the internet to a high port "
              "(32768 and up) is treated as a reply, so a UPnP mapping on such a port won't show either.")

LEVELS = ("likely", "possible", "unknown", "unlikely")  # how likely the device runs PHP that serves web pages
WEB_PORTS = {80, 81, 443, 2082, 2083, 5000, 5001, 7080, 8000, 8008, 8080, 8081, 8090, 8443, 8888, 9000}
WEB_SERVICES = {"HTTP", "HTTPS", "HTTP_PROXY", "HTTP_CONNECT", "QUIC"}
# what devices answer just by being on a network; any other service others reach on a device raises it
DISCOVERY = {"DNS", "MDNS", "SSDP", "NTP", "DHCP", "DHCPV6", "NETBIOS", "NETBIOS_NAMESERVER", "LLMNR", "UPNP",
             "WS-DISCOVERY", "WSD", "ICMP", "ICMPV6", "IGMP"}
EPHEMERAL = 32768  # ports at or above this are replies and client ports (Linux from 32768, Windows/macOS from 49152)
# a device's own name can only raise it; substrings, plus short words that must stand alone
SERVER_SUBSTR = re.compile(r"server|raspberry|pihole|nextcloud|wordpress|docker|homelab|proxmox|unraid|truenas|"
                           r"ubuntu|debian|mynas|synology|diskstation|qnap")
SERVER_WORD = re.compile(r"(?<![a-z])(nas|srv|pi|wp|web|www)(?![a-z])")

NAS_VENDORS = ("synology", "qnap", "asustor", "terramaster", "western digital", "buffalo", "drobo")
SBC_VENDORS = ("raspberry",)
# kinds of device whose software can't be changed and whose built-in pages are rarely PHP; nothing else is "unlikely"
CONSUMER = ("smartphone", "handheld", "tablet", "wearable", "watch", "smart tv", "smarttv", "set-top", "game console",
            "lighting", "light bulb", "smart plug", "mattress", "air conditioner", "speaker", "thermostat",
            "intelligent home appliances", "e-reader", "voice assistant")
LIKELY = ("nas", "network storage", "storage", "web app", "firewall", "ad blocker")  # ship PHP more often than not
SERVER_CLASS = ("server", "workstation", "virtual machine", "single board", "router", "network video recorder", "nvr",
                "home automation", "media server", "thin client", "hypervisor")
SERVER_OS = ("linux", "raspbian", "freebsd", "ubuntu", "debian", "centos", "red hat", "fedora", "unraid", "truenas")


def _has(text, words):
    return any(w in text for w in words)


def profile(f, gear=False):
    """(runs, php level, why, check) from lower-cased fingerprint type/family/os and MAC vendor."""
    kind = f"{f['type']} | {f['family']}"
    os_ = f["os"]
    if gear:
        return ("UniFi equipment", "unlikely", "Adopted UniFi network equipment runs UniFi OS or UniFi firmware, "
                "which isn't known to serve its pages with PHP", "Keep its firmware updated, and watch Ubiquiti's PHP2Shell notice.")
    if _has(os_, ("mac os", "macos", "os x")) or (_has(kind, ("computer", "desktop", "laptop")) and "ios" in os_):
        return ("macOS", "possible", "macOS 12 Monterey and later don't include PHP, so it's only there if someone "
                "installed it (Homebrew, MAMP, Docker, a local development site); older macOS shipped it",
                "In Terminal: php -v; brew list --versions php; docker ps. If PHP serves a site, update it.")
    if _has(kind, LIKELY) or _has(f["vendor"], NAS_VENDORS):
        return ("a NAS, firewall or web appliance", "likely",
                "These systems often ship PHP: NAS web-hosting packages (Synology Web Station, QNAP Web Server), "
                "some NAS and firewall admin pages themselves (pfSense, OPNsense), Pi-hole before version 6",
                "Install the vendor's PHP2Shell update as soon as it ships, whatever packages you use, and keep its "
                "web pages off the internet.")
    if _has(kind, SERVER_CLASS) or _has(os_, SERVER_OS) or "server" in os_ or _has(f["vendor"], SBC_VENDORS):
        return ("a server or small Linux computer", "possible",
                "PHP if it runs a web app: Nextcloud, WordPress, a home dashboard, Pi-hole before version 6, an "
                "NVR or home-automation add-on",
                "php -v; on Linux, apt list --installed 2>/dev/null | grep php; docker ps. Update what you find.")
    if _has(kind, ("computer", "desktop", "laptop")) or "windows" in os_:
        return ("a Windows or Linux computer", "possible", "PHP only if someone installed it (XAMPP, WAMP, WSL, "
                "Docker, a web app)", "php -v (or look for XAMPP/WAMP and running containers). If PHP serves a "
                "site, update it.")
    if os_.startswith(("apple ios", "ipados", "watchos", "tvos")) or (
            os_ == "ios" and ("apple" in f["vendor"] or _has(kind, ("handheld", "tablet", "phone")))):
        return ("an Apple mobile system", "unlikely", "Apple's mobile systems (iOS, iPadOS, watchOS, tvOS) "
                "practically never run a web server for other devices", "Keep it updated.")
    if _has(kind, CONSUMER) or os_ in ("android", "xbox", "xmb", "playstation", "tizen", "webos", "roku"):
        return ("a consumer device (phone, TV, console, smart-home or wearable)", "unlikely",
                "These rarely run anything that serves web pages to other devices, and their built-in pages (if "
                "any) are rarely PHP", "Keep its firmware updated.")
    if _has(kind, ("printer", "scanner")):
        return ("a printer or scanner", "unlikely", "Vendor firmware with a small built-in web page for settings; "
                "PHP isn't the usual choice there", "Keep its firmware updated, and watch the vendor's PHP2Shell notice.")
    if "ubiquiti" in f["vendor"]:
        return ("Ubiquiti equipment the network controller doesn't manage", "unknown",
                "Not every Ubiquiti system is UniFi OS (airMAX radios, EdgeRouters, UniFi Drive, Protect devices), "
                "and some have shipped PHP", "Check Ubiquiti's PHP2Shell notice for this model, and keep it updated.")
    if "apple" in f["vendor"] and not (f["type"] or f["family"]):
        return ("an Apple device UniFi hasn't fingerprinted", "unknown", "If it's a Mac, PHP is there only if "
                "someone installed it; if it's an iPhone, iPad or Apple TV, it isn't", "If it's a Mac: php -v in Terminal.")
    if f["type"] or f["family"] or os_:
        return ("a kind of device the assessment doesn't know", "unknown", "UniFi recognised it, but not as a kind "
                "of device whose software is known here", "Find out what it runs, and check it for PHP if it serves "
                "web pages.")
    if f["vendor"]:
        return ("unknown (only the MAC vendor is known)", "unknown", "The MAC vendor says who made the network chip, "
                "not what runs on the device", "Find out what it is; if it's a computer or server, check it for PHP.")
    return None


UNKNOWN = ("unknown", "unknown", "UniFi has no fingerprint and no MAC vendor for it, so there's nothing to judge it by",
           "Find out what it is (where it's connected, when it's online).")
PRIVATE = ("a phone, tablet or computer using a private Wi-Fi address", "unknown",
           "A rotating private address hides the vendor; most such devices are phones and tablets",
           "Nothing to do unless it turns out to be a computer that serves web pages.")


def _service_port(service):
    m = re.fullmatch(r"(?:TCP|UDP)/(\d{1,5})", str(service or ""), re.IGNORECASE)
    return int(m.group(1)) if m else None


def _is_web(service):
    p = _service_port(service)
    return str(service or "").upper() in WEB_SERVICES or (p is not None and p in WEB_PORTS)


def served_by_device(rows):
    """{mac: {"internet": {service: flows}, "local": {service: flows}}} from traffic rows (mac, kind, service,
    flows), keeping only real services: a port below EPHEMERAL, or a named service."""
    out = {}
    for mac, kind, service, flows in rows:
        p = _service_port(service)
        if p is not None and p >= EPHEMERAL:
            continue
        side = "internet" if kind == "internet_in" else "local"
        s = out.setdefault(mac, {"internet": {}, "local": {}})[side]
        s[service] = s.get(service, 0) + flows
    return out


def forwards_by_ip(forwards):
    """Enabled port forwards grouped by the address they send to (a rule without the field counts as enabled)."""
    out = {}
    for f in forwards or []:
        if isinstance(f, dict) and f.get("enabled", True) is not False and f.get("fwd"):
            out.setdefault(f["fwd"], []).append(f)
    return out


def forward_text(x):
    return (f"port forward: {x.get('proto') or 'tcp_udp'} {x.get('dst_port') or '?'} → "
            f"{x.get('fwd')} port {x.get('fwd_port') or x.get('dst_port') or '?'}")


RECENT_S = 14 * 86400  # an unidentified device seen this recently stays on the list while it's offline


def assess(device, fp, served=None, forwards=None, gear=False, now=None):
    """One device's assessment. device: the stored row; fp: its resolved fingerprint (or None); served: its
    served_by_device entry; forwards: enabled port forwards to an address it holds or held; gear: it's adopted
    UniFi network equipment (the controller's own device list)."""
    f = {k: str((fp or {}).get(k) or "").lower() for k in ("type", "family", "os")}
    f["vendor"] = str((fp or {}).get("vendor") or device.get("vendor") or "").lower()
    # a client with its own fingerprint isn't controller gear, whatever MAC it shows
    prof = profile(f, gear and not any(f[k] for k in ("type", "family", "os"))) or (PRIVATE if normalize.mac_randomised(device["mac"]) else UNKNOWN)
    runs, level, why, check = prof
    reasons = [why]
    served = served or {"internet": {}, "local": {}}
    evidence = []
    for side, label in (("internet", "from the internet"), ("local", "from your other networks")):
        for svc, n in sorted(served[side].items(), key=lambda x: -x[1])[:5]:
            evidence.append(f"reached {label} on {svc} ({n} flow{'s' if n != 1 else ''})")
    evidence += [forward_text(x) for x in (forwards or [])][:5]
    web_internet = any(_is_web(s) for s in served["internet"]) or any(
        _is_web(f"TCP/{p}") for x in (forwards or []) for p in _ports(x.get("fwd_port") or x.get("dst_port")))
    web_any = web_internet or any(_is_web(s) for s in served["local"])
    exposed = bool(served["internet"] or forwards)
    other_service = any(str(s).upper() not in DISCOVERY and not str(s).upper().startswith(("UDP/", "ICMP"))
                        for s in served["local"])
    if (web_any or exposed or other_service) and level in ("unlikely", "unknown"):
        level = "possible"
        reasons.append("it answers requests from outside it, and what serves them is unknown")
    name = " ".join(str(device.get(k) or "").lower() for k in ("hostname", "name"))
    if level in ("unlikely", "unknown") and (SERVER_SUBSTR.search(name) or SERVER_WORD.search(name)):
        level = "possible"
        reasons.append("its own name suggests a server (a device's name can only raise this, never lower it)")
    if exposed:
        priority = "act"
        reasons.append("it's reachable from the internet: check it first")
    elif level in ("likely", "possible") or (level == "unknown" and (device.get("active") or (
            now and (device.get("last_seen") or 0) >= now - RECENT_S))):
        priority = "check"
    else:
        priority = "low"
    return {"runs": runs, "check": check, "php": level, "priority": priority, "reasons": reasons,
            "evidence": evidence, "exposed_to_internet": exposed, "serves_web": web_any, "advisory": CURRENT}


def _ports(v):
    """A port forward's port field: '443', '8080-8090' or '80,443'; at most a few hundred ports."""
    out = []
    for part in str(v or "").split(","):
        m = re.fullmatch(r"\s*(\d{1,5})\s*(?:-\s*(\d{1,5})\s*)?", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2) or m.group(1))
            if 0 < a <= b <= 65535 and b - a < 300:
                out += range(a, b + 1)
    return out


PRIORITY_ORDER = {"act": 0, "check": 1, "low": 2}
