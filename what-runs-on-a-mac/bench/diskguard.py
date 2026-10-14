"""Disk-space guard: monitor the disk so the machine is never lost to
over-subscription. Downloads refuse a repo that would cross the floor; runs pause below it."""
import os, shutil, time, yaml
from . import ROOT, load_manifest

GB = 1e9


def _cfg():
    return load_manifest()["disk_guard"]


def free_gb(path):
    try:
        return shutil.disk_usage(path).free / GB
    except FileNotFoundError:
        return None


def status():
    c = _cfg()
    i, e = free_gb(c["internal_path"]), free_gb(c["external_path"])
    level = "ok"
    if i is not None and i < c["internal_warn_gb"]:
        level = "warn"
    if (i is not None and i < c["internal_floor_gb"]) or (e is not None and e < c["external_floor_gb"]):
        level = "stop"
    return {"internal_free_gb": round(i, 1) if i else i, "external_free_gb": round(e, 1) if e else e, "level": level}


def can_fit(size_gb, path=None):
    """True if writing size_gb to path's volume still leaves the floor."""
    c = _cfg()
    path = path or c["internal_path"]
    floor = c["external_floor_gb"] if str(path).startswith("/Volumes/") else c["internal_floor_gb"]
    f = free_gb(path)
    return f is not None and f - size_gb >= floor, f, floor


def wait_until_ok(log=print, poll_s=None):
    """Block while the disk is below the floor (used between runs)."""
    poll_s = poll_s or _cfg()["check_every_s"]
    warned = False
    while (s := status())["level"] == "stop":
        if not warned:
            log(f"DISK GUARD: paused, {s}")
            warned = True
        time.sleep(poll_s)
    if warned:
        log(f"DISK GUARD: resumed, {status()}")
