"""UniFi's fingerprint name table (v2/api/fingerprint_devices/0), cached locally and refreshed weekly.

Client records carry only numeric ids (dev_id, dev_vendor, dev_family, dev_cat, os_name). This
resolves them to names ("Apple iPhone 16 Plus", "Smartphone", "Handheld"). The names come from
Ubiquiti's table; which id a device gets comes from how it behaves on the network (DHCP options,
mDNS). That's harder to fake than a hostname you type in, but a device can still fake it:
stronger evidence, never proof.
"""
import json
import os
import tempfile
import time

from . import config

CACHE = config.STATE_DIR / "fingerprints.json"
MAX_AGE_S = 7 * 86400
DERIVED_BY = ("UniFi's fingerprint of the device's network behaviour (DHCP/mDNS): harder to fake "
              "than a name, not proof")


def load(client=None, now=None):
    """The cached table, refreshed from the controller when stale and a client is given."""
    now = now or time.time()
    cache = config.STATE_DIR / "fingerprints.json"
    fresh = cache.exists() and now - cache.stat().st_mtime < MAX_AGE_S
    if not fresh and client is not None and hasattr(client, "fingerprint_table"):
        try:
            table = client.fingerprint_table()
            slim = {k: table.get(k, {}) for k in ("dev_ids", "vendor_ids", "family_ids", "dev_type_ids", "os_name_ids")}
            slim["dev_ids"] = {k: {"name": (v.get("name") or "").strip()} for k, v in slim["dev_ids"].items()}
            fd, tmp = tempfile.mkstemp(dir=config.STATE_DIR, prefix=".fp-", suffix=".json")
            with os.fdopen(fd, "w") as f:
                json.dump(slim, f)
            os.replace(tmp, cache)
            return slim
        except Exception:
            pass  # fall back to whatever cache exists; fingerprints are enrichment, not detection
    if cache.exists():
        return json.loads(cache.read_text())
    return {}


def resolve(table, dev):
    """Names for a stored device row, or None when UniFi hasn't fingerprinted it."""
    if not table or not any(dev.get(k) for k in ("dev_id", "dev_vendor", "dev_family", "dev_cat")):
        return None
    g = lambda t, k: (table.get(t) or {}).get(str(dev.get(k))) if dev.get(k) is not None else None
    model = (g("dev_ids", "dev_id") or {}).get("name") or None
    out = {"model": model, "vendor": g("vendor_ids", "dev_vendor"), "family": g("family_ids", "dev_family"),
           "type": g("dev_type_ids", "dev_cat"), "os": g("os_name_ids", "os_name")}
    out = {k: v for k, v in out.items() if v}
    return {**out, "derived_by": DERIVED_BY} if out else None
