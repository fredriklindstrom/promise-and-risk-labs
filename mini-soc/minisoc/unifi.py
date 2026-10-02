"""Read-only UniFi Network client.

Every request goes through _request(), which refuses anything not on the allowlist:
GETs to a handful of read endpoints, and POSTs only to the system-log *query* endpoint (UniFi
uses POST for log searches). Nothing here can change the controller, whatever rights
the API key has.
"""
import pathlib
import re
import time

import requests
import urllib3
from requests.adapters import HTTPAdapter

urllib3.disable_warnings()  # self-signed certificate: trust comes from the pinned fingerprint below

ALLOWED = [
    ("GET", re.compile(r"api/s/[\w-]+/stat/sta", re.ASCII)),
    ("GET", re.compile(r"api/s/[\w-]+/rest/user", re.ASCII)),
    ("GET", re.compile(r"api/s/[\w-]+/stat/device-basic", re.ASCII)),
    ("GET", re.compile(r"api/s/[\w-]+/stat/device", re.ASCII)),  # read for radio BSSIDs; only those are kept
    ("GET", re.compile(r"api/self", re.ASCII)),
    ("GET", re.compile(r"v2/api/fingerprint_devices/0", re.ASCII)),  # UniFi's fingerprint name table
    ("POST", re.compile(r"v2/api/site/[\w-]+/system-log/all", re.ASCII)),
    ("POST", re.compile(r"v2/api/site/[\w-]+/traffic-flows", re.ASCII)),  # a query: flows the gateway routed
]
MAX_LOG_PAGES = 52
MAX_FLOW_PAGES = 20


def _slim_flow(x):
    """Only what the traffic summary uses; the rest of a flow record is dropped here."""
    s, d = x.get("source") or {}, x.get("destination") or {}
    s, d = (s if isinstance(s, dict) else {}), (d if isinstance(d, dict) else {})
    td = x.get("traffic_data") if isinstance(x.get("traffic_data"), dict) else {}
    doms = d.get("domains") if isinstance(d.get("domains"), list) else []
    pols = x.get("policies") if isinstance(x.get("policies"), list) else []
    net = x.get("in") if isinstance(x.get("in"), dict) else {}
    return {"time": x.get("time"), "direction": x.get("direction"), "action": x.get("action"),
            "service": x.get("service"), "protocol": x.get("protocol"), "src_mac": s.get("mac"),
            "src_ip": s.get("ip"), "src_port": s.get("port"), "src_subnet": s.get("subnet"),
            "dst_mac": d.get("mac"), "dst_ip": d.get("ip"), "dst_port": d.get("port"), "region": d.get("region"),
            "domain": next((v for v in doms if isinstance(v, str)), None), "bytes": td.get("bytes_total"),
            "risk": x.get("risk"), "network": net.get("network_name"),
            "policies": [{"type": p.get("type"), "category": p.get("ips_category"), "name": p.get("name")}
                         for p in pols[:3] if isinstance(p, dict)]}


class ReadOnlyViolation(RuntimeError):
    pass


class PinnedAdapter(HTTPAdapter):
    """The console's certificate is self-signed, so CA validation can't help. Pinning its SHA-256
    fingerprint means a machine impersonating the console never receives the API key."""

    def __init__(self, fingerprint, **kw):
        self.fingerprint = fingerprint
        super().__init__(**kw)

    def init_poolmanager(self, *args, **kw):
        kw["assert_fingerprint"] = self.fingerprint
        return super().init_poolmanager(*args, **kw)


class UniFi:
    def __init__(self, cfg):
        self.base = cfg["host"].rstrip("/") + "/proxy/network"
        self.site = cfg["site"]
        if not str(cfg.get("host", "")).lower().startswith("https://"):  # http would send the key in clear, unpinned
            raise ValueError("config.json host must start with https://")
        fp = str(cfg.get("tls_sha256") or "")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", fp):  # an empty pin would silently disable the check
            raise ValueError("config.json tls_sha256 must be the console certificate's 64-hex SHA-256")
        key = pathlib.Path(cfg["key_file"]).read_text().strip()
        self.s = requests.Session()
        self.s.trust_env = False  # no system/env proxies between us and the console
        self.s.verify = False
        # pinned for EVERY https request this session makes: a host string that requests normalises
        # (IDNA, port spelling) must not fall through to an unpinned default adapter
        self.s.mount("https://", PinnedAdapter(cfg["tls_sha256"]))
        self.s.headers.update({"X-API-KEY": key, "Accept": "application/json"})
        self.truncated = False

    def _request(self, method, path, **kw):
        if not any(m == method and rx.fullmatch(path) for m, rx in ALLOWED):
            raise ReadOnlyViolation(f"{method} {path} is not on the read-only allowlist")
        # no redirects: a 30x would carry the key to a host or path the allowlist never saw
        r = self.s.request(method, f"{self.base}/{path}", timeout=30, allow_redirects=False, **kw)
        if r.is_redirect:
            raise ReadOnlyViolation(f"refusing redirect from {path} to {r.headers.get('location')}")
        r.raise_for_status()
        return r.json()

    def whoami(self):
        d = self._request("GET", "api/self").get("data") or [{}]
        me = d[0]
        return {k: me.get(k) for k in ("name", "is_owner", "is_super", "role", "permissions") if k in me}

    def own_radios(self):
        """Your UniFi devices and the exact BSSID of every network their radios broadcast. Only these
        fields are kept; the rest of the (large) device record is dropped here."""
        out = {"devices": [], "vaps": []}
        for d in self._request("GET", f"api/s/{self.site}/stat/device").get("data", []):
            mac = str(d.get("mac") or "").lower()
            out["devices"].append({"mac": mac, "name": d.get("name"), "model": d.get("model")})
            for v in d.get("vap_table") or []:
                if v.get("bssid"):
                    out["vaps"].append({"essid": v.get("essid"), "bssid": str(v["bssid"]).lower(),
                                        "radio": v.get("radio"), "device_mac": mac, "device_name": d.get("name")})
        return out

    def fingerprint_table(self):
        return self._request("GET", "v2/api/fingerprint_devices/0")

    def clients_active(self):
        return self._request("GET", f"api/s/{self.site}/stat/sta")["data"]

    def clients_known(self):
        return self._request("GET", f"api/s/{self.site}/rest/user")["data"]

    def events_since(self, since_ms):
        """Sets self.truncated when the page cap cut the window short (a gap the caller must report)."""
        now = int(time.time() * 1000)
        rows, page = [], 0
        self.truncated = False
        while True:
            d = self._request("POST", f"v2/api/site/{self.site}/system-log/all", json={
                "timestampFrom": since_ms, "timestampTo": now, "pageSize": 100, "pageNumber": page})
            rows += d.get("data", [])
            total = d.get("total_page_count", 1)
            if page + 1 >= total:
                return rows
            if page + 1 >= MAX_LOG_PAGES:
                self.truncated = True
                return rows
            page += 1


    def traffic_flows(self, since_ms, until_ms, max_pages=MAX_FLOW_PAGES):
        """Flows the gateway routed in the window. Sets self.flows_truncated when the page cap cut it short."""
        rows, page = [], 0
        self.flows_truncated = False
        while True:
            d = self._request("POST", f"v2/api/site/{self.site}/traffic-flows", json={
                "timestampFrom": since_ms, "timestampTo": until_ms, "pageSize": 100, "pageNumber": page})
            rows += [_slim_flow(x) for x in d.get("data") or [] if isinstance(x, dict)]
            if not d.get("has_next"):
                return rows
            if page + 1 >= max_pages:
                self.flows_truncated = True
                return rows
            page += 1


class Fixture:
    """Same interface, served from a saved snapshot folder, for offline tests."""

    def __init__(self, snap_dir):
        import json
        self.d = pathlib.Path(snap_dir)
        self._j = lambda n: json.loads((self.d / f"{n}.json").read_text())
        self.truncated = False

    def whoami(self):
        return {"name": "fixture", "is_owner": False}

    def clients_active(self):
        return self._j("clients_active")

    def clients_known(self):
        return self._j("clients_known")

    def events_since(self, since_ms):
        return [e for e in self._j("log_all") if e["timestamp"] >= since_ms]
