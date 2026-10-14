"""System readers (no root): memory pressure, swap, memory use, GPU utilisation, running apps.
Plus the OOM watchdog and run-window summaries over samples.jsonl / power.jsonl."""
import datetime, json, re, subprocess, threading, time
from pathlib import Path

PRESSURE = {1: "normal", 2: "warn", 4: "critical"}
# macOS thermal state (sampler, every second) and powermetrics thermal pressure (power sampler): worst wins.
THERMAL_RANK = {"nominal": 0, "fair": 1, "moderate": 1, "serious": 2, "heavy": 2, "critical": 3, "trapping": 3, "sleeping": 3}
RANK = {"normal": 0, "warn": 1, "critical": 2}


def sysctl(name):
    return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True).stdout.strip()


def pressure():
    try:
        return PRESSURE.get(int(sysctl("kern.memorystatus_vm_pressure_level")), "unknown")
    except ValueError:
        return "unknown"


def swap_used_gb():
    m = re.search(r"used = ([\d.]+)M", sysctl("vm.swapusage"))
    return round(float(m.group(1)) / 1024, 3) if m else None


def gpu_budget_gb():
    """Metal working-set ceiling under the current iogpu.wired_limit_mb (0 = macOS default)."""
    lim = int(sysctl("iogpu.wired_limit_mb") or 0)
    if lim:
        return round(lim / 1024 * 1.073741824, 2)
    # The default ceiling differs per Mac (83.49 GB on the 96 GB Studio); ask Metal through MLX.
    try:
        import mlx.core as mx
        info = mx.device_info() if hasattr(mx, "device_info") else mx.metal.device_info()
        return round(info["max_recommended_working_set_size"] / 1e9, 2)
    except Exception:
        return None


def mem_used_gb():
    """Activity Monitor style: (active + wired + compressed) pages; uses vm_stat (no root)."""
    out = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
    page = int(re.search(r"page size of (\d+)", out).group(1))
    g = lambda k: int(re.search(rf"{k}:\s+(\d+)", out).group(1))
    return round((g("Pages active") + g("Pages wired down") + g("Pages occupied by compressor")) * page / 1e9, 2)


def gpu_util_pct():
    out = subprocess.run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"], capture_output=True, text=True).stdout
    m = re.search(r'"Device Utilization %"=(\d+)', out)
    return int(m.group(1)) if m else None


def gpu_mem_in_use_gb():
    """IOAccelerator 'In use system memory': everything the GPU has allocated, system-wide (no root)."""
    out = subprocess.run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"], capture_output=True, text=True).stdout
    m = re.search(r'"In use system memory"=(\d+)', out)
    return round(int(m.group(1)) / 1e9, 3) if m else None


def foreground_apps():
    out = subprocess.run(["osascript", "-e", 'tell application "System Events" to get name of (processes where background only is false)'],
                         capture_output=True, text=True).stdout
    return [a.strip() for a in out.split(",") if a.strip()]


def _iso(t):
    return datetime.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()


def read_jsonl(path, t0, t1):
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    for line in p.read_text().splitlines():
        try:
            r = json.loads(line)
            ts = _iso(r["t"])
        except Exception:
            continue
        if t0 <= ts <= t1:
            rows.append((ts, r))
    return rows


def integrate_wh(rows, key):
    """Trapezoid integral of a watts series over time -> Wh. None if fewer than 2 points."""
    pts = [(t, r[key]) for t, r in rows if r.get(key) is not None]
    if len(pts) < 2:
        return None
    return round(sum((t2 - t1) * (w1 + w2) / 2 for (t1, w1), (t2, w2) in zip(pts, pts[1:])) / 3600, 4)


def window_summary(run_dir, t0, t1, power_all):
    s = [r for _, r in read_jsonl(Path(run_dir) / "samples.jsonl", t0, t1)]
    p = read_jsonl(Path(run_dir) / "power.jsonl", t0, t1) or read_jsonl(power_all, t0, t1)
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
    press = [r.get("mem_pressure") for r in s if r.get("mem_pressure") in RANK]
    return {
        "mem_pressure_max": max(press, key=RANK.get) if press else None,
        "swap_used_gb_max": max((r["swap_gb"] for r in s if r.get("swap_gb") is not None), default=None),
        "mem_used_gb_max": max((r["mem_used_gb"] for r in s if r.get("mem_used_gb") is not None), default=None),
        "gpu_active_pct_avg": avg([r["gpu_util_pct"] for r in s if r.get("gpu_util_pct") is not None]),
        "gpu_power_w_avg": avg([r["gpu_power_w"] for _, r in p if r.get("gpu_power_w") is not None]),
        "package_power_w_avg": avg([r["pkg_power_w"] for _, r in p if r.get("pkg_power_w") is not None]),
        "gpu_power_w_max": max((r["gpu_power_w"] for _, r in p if r.get("gpu_power_w") is not None), default=None),
        "package_power_w_max": max((r["pkg_power_w"] for _, r in p if r.get("pkg_power_w") is not None), default=None),
        "energy_wh": integrate_wh(p, "pkg_power_w"),
        "gpu_energy_wh": integrate_wh(p, "gpu_power_w"),
        "thermal_state_max": max((r["thermal_state"] for r in s if r.get("thermal_state") in THERMAL_RANK), key=THERMAL_RANK.get, default=None),
        "thermal_pressure_max": max((r["thermal_pressure"] for _, r in p if r.get("thermal_pressure") in THERMAL_RANK), key=THERMAL_RANK.get, default=None),
        "soc_temp_max_c": max((r["soc_temp_max_c"] for r in s if r.get("soc_temp_max_c") is not None), default=None),
        "soc_temp_avg_c": avg([r["soc_temp_avg_c"] for r in s if r.get("soc_temp_avg_c") is not None]),
        "gpu_freq_mhz_avg": avg([r["gpu_freq_mhz"] for _, r in p if r.get("gpu_freq_mhz")]),
        "gpu_freq_mhz_min": min((r["gpu_freq_mhz"] for _, r in p if r.get("gpu_freq_mhz")), default=None),
        "cpu_p_freq_mhz_avg": avg([r["cpu_p_freq_mhz"] for _, r in p if r.get("cpu_p_freq_mhz")]),
        "gpu_mem_in_use_gb_max": max((r["gpu_mem_in_use_gb"] for r in s if r.get("gpu_mem_in_use_gb") is not None), default=None),
        "samples": len(s), "power_samples": len(p),
    }


class Watchdog(threading.Thread):
    """Declares a run unfit when memory pressure is critical AND swap has grown past a limit,
    or swap grows past a hard limit at any pressure. Calls on_trip(reason) once."""

    def __init__(self, on_trip, swap_growth_gb=4.0, hard_swap_growth_gb=8.0, interval=1.0):
        super().__init__(daemon=True)
        self.on_trip, self.limit, self.hard, self.interval = on_trip, swap_growth_gb, hard_swap_growth_gb, interval
        self.base = swap_used_gb() or 0.0
        self.stop_evt = threading.Event()
        self.tripped = None
        self.crit_s = 0

    def run(self):
        while not self.stop_evt.wait(self.interval):
            sw = (swap_used_gb() or 0.0) - self.base
            pr = pressure()
            self.crit_s = self.crit_s + 1 if pr == "critical" else 0
            reason = None
            if pr == "critical" and sw > self.limit:
                reason = f"memory pressure critical with swap +{sw:.1f} GB"
            elif sw > self.hard:
                reason = f"swap grew +{sw:.1f} GB"
            elif self.crit_s >= 30:
                reason = "memory pressure critical for 30 s"
            if reason:
                self.tripped = reason
                self.on_trip(reason)
                return

    def stop(self):
        self.stop_evt.set()


class FallbackSampler(threading.Thread):
    """Used only when the Swift bench-sampler binary isn't built. ~1 Hz, writes samples.jsonl."""

    def __init__(self, path_fn):
        super().__init__(daemon=True)
        self.path_fn, self.stop_evt = path_fn, threading.Event()

    def run(self):
        from .events import now
        import shutil
        while not self.stop_evt.wait(1.0):
            try:
                rec = {"t": now(), "type": "sample", "mem_used_gb": mem_used_gb(), "mem_pressure": pressure(),
                       "swap_gb": swap_used_gb(), "gpu_util_pct": gpu_util_pct(), "gpu_power_w": None, "pkg_power_w": None,
                       "disk_free_gb": round(shutil.disk_usage("/System/Volumes/Data").free / 1e9, 1), "source": "fallback"}
                with open(self.path_fn(), "a") as f:
                    f.write(json.dumps(rec) + "\n")
            except Exception:
                pass

    def stop(self):
        self.stop_evt.set()
