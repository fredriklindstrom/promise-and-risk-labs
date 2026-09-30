"""Where an IP address is, from local copies of the DB-IP Lite databases. No lookup leaves the Mac.

  state/geo/dbip-city-lite-YYYY-MM.mmdb   city, region, country
  state/geo/dbip-asn-lite-YYYY-MM.mmdb    the network the address belongs to (its owner's registered name)

IP geolocation by DB-IP (https://db-ip.com), CC BY 4.0. An estimate: the country is usually right, the
city often isn't, and a VPN or iCloud Private Relay shows the relay's location, not the person's. The
network name is registered by the network's owner. Refreshed monthly (refresh()); the newest file wins.
"""
import gzip
import io
import ipaddress
import os
import re
import tempfile
import time

from . import config, normalize

GEO_DIR = config.STATE_DIR / "geo"
SOURCE = "IP geolocation by DB-IP (db-ip.com), CC BY 4.0"
DERIVED_BY = ("looked up in a local IP location database: an estimate (country usually right, city often "
              "not; a VPN or relay shows its own location); the network name is registered by its owner")
URL = "https://download.db-ip.com/free/dbip-{kind}-lite-{month}.mmdb.gz"
KINDS = ("city", "asn")
REFRESH_EVERY_S = 86400
DOWNLOAD_DEADLINE_S = 120          # the whole download, not per read: it runs inside a poll
MAX_DB_BYTES = 512 * 1024 * 1024   # decompressed; the real city file is ~130 MB
_readers = {}


class _Deadline(io.RawIOBase):
    """Checks the download deadline as the bytes arrive, so a server that trickles can't stall a poll."""
    def __init__(self, raw, until):
        self.raw, self.until = raw, until

    def readable(self):
        return True

    def readinto(self, b):
        if time.monotonic() > self.until:
            raise TimeoutError("location database download too slow")
        data = self.raw.read(len(b))
        if len(data) > len(b):
            raise ValueError("download stream returned more than was asked for")
        b[:len(data)] = data
        return len(data)


def _newest(kind):
    files = sorted(GEO_DIR.glob(f"dbip-{kind}-lite-????-??.mmdb")) if GEO_DIR.exists() else []
    return files[-1] if files else None


def _reader(kind):
    path = _newest(kind)
    if not path:
        return None, None
    cached = _readers.get(kind)
    if not cached or cached[0] != path:
        import maxminddb
        _readers[kind] = (path, maxminddb.open_database(str(path)))
    return _readers[kind][1], path.stem[-7:]  # the YYYY-MM the file was published for


def _name(d, key="names"):
    names = (d or {}).get(key) if isinstance(d, dict) else None
    v = names.get("en") if isinstance(names, dict) else None
    return v[:60] if isinstance(v, str) and v else None


def lookup(ip):
    """{scope, ...}: special addresses are described without a lookup; public ones get city, region,
    country and network from the local databases. Never raises; missing databases say so."""
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return {"scope": "unknown", "summary": "not an IP address"}
    if addr.is_loopback:
        return {"scope": "loopback", "summary": "this machine"}
    if addr.version == 4 and addr in ipaddress.ip_network("100.64.0.0/10"):
        return {"scope": "shared", "summary": "carrier-grade NAT or a VPN overlay (not locatable)"}
    if addr.is_private or addr.is_link_local:
        return {"scope": "private", "summary": "your local network or a VPN (private address)"}
    if not addr.is_global:
        return {"scope": "reserved", "summary": "reserved address (not locatable)"}
    out = {"scope": "public", "derived_by": DERIVED_BY, "source": SOURCE}
    network = None
    try:
        city, month = _reader("city")
        if city:
            r = city.get(str(addr)) or {}
            out.update({k: v for k, v in {
                "city": _name(r.get("city")),
                "region": _name((r.get("subdivisions") or [{}])[0]),
                "country": _name(r.get("country")),
                "country_code": str((r.get("country") or {}).get("iso_code") or "")[:3]}.items() if v})
            out["database_month"] = month
        asn, _ = _reader("asn")
        if asn:
            r = asn.get(str(addr)) or {}
            if isinstance(r.get("autonomous_system_number"), int):
                out["network_asn"] = r["autonomous_system_number"]
            if r.get("autonomous_system_organization"):
                network = str(r["autonomous_system_organization"])[:120]
        if not _newest("city") and not _newest("asn"):
            out["error"] = "no location database installed"
        # summary: the database's own place names only. The network name is chosen by whoever registers the
        # network, so it never goes into a title or the summary, only here, wrapped with who wrote it.
        out["summary"] = ", ".join(x for x in (out.get("city"), out.get("country_code") or out.get("country")) if x) \
            or "location unknown"
        if network:
            out["network"] = normalize.label("network_name", network)
    except Exception as ex:  # enrichment: an alert never fails because the location couldn't be read
        out["error"] = type(ex).__name__
        out.setdefault("summary", "location unknown")
    return out


def refresh(now=None, log=print):
    """Fetch this month's databases once they're published (at most one attempt a day). A download is
    written to a temp file, checked by opening it, then moved into place; older months are removed."""
    import requests
    now = now or time.time()
    month = time.strftime("%Y-%m", time.localtime(now))
    GEO_DIR.mkdir(parents=True, exist_ok=True)
    fetched = []
    for kind in KINDS:
        target = GEO_DIR / f"dbip-{kind}-lite-{month}.mmdb"
        if target.exists():
            continue
        fd, tmp = tempfile.mkstemp(dir=GEO_DIR, prefix=".dl-")
        os.close(fd)
        try:
            with requests.get(URL.format(kind=kind, month=month), stream=True, timeout=60, allow_redirects=False) as r:
                if r.status_code != 200:
                    log(f"geo: {kind} database for {month} not available yet ({r.status_code})")
                    continue
                deadline, written = time.monotonic() + DOWNLOAD_DEADLINE_S, 0
                with gzip.GzipFile(fileobj=_Deadline(r.raw, deadline)) as gz, open(tmp, "wb") as f:
                    while chunk := gz.read(1 << 20):
                        written += len(chunk)
                        if written > MAX_DB_BYTES or time.monotonic() > deadline:
                            raise ValueError("download too large or too slow")
                        f.write(chunk)
            import maxminddb
            maxminddb.open_database(tmp).close()  # a damaged download never replaces a working file
            os.replace(tmp, target)
            fetched.append(kind)
            for old in GEO_DIR.glob(f"dbip-{kind}-lite-????-??.mmdb"):
                if old != target and re.fullmatch(rf"dbip-{kind}-lite-\d{{4}}-\d{{2}}\.mmdb", old.name):
                    old.unlink()
        except Exception as ex:
            log(f"geo: {kind} refresh failed: {type(ex).__name__}")
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    _readers.clear()
    return fetched
