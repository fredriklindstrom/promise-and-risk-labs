"""Read-only UniFi Network client.

Every request goes through _request(), which refuses anything not on the allowlist:
GETs to four read endpoints, and POSTs only to the system-log *query* endpoint (UniFi
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
    ("GET", re.compile(r"api/self", re.ASCII)),
    ("POST", re.compile(r"v2/api/site/[\w-]+/system-log/all", re.ASCII)),
]
MAX_LOG_PAGES = 52


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


class Fixture:
    """Same interface, served from a saved snapshot folder, for offline testing."""

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
