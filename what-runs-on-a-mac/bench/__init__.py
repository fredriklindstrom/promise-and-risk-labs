"""nov17-bench: benchmark harness for the Nov 17 episode."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Which machine profile to run: manifest.yaml (Mac Studio) or e.g. manifest-air16.yaml (BENCH_MANIFEST=...).
MANIFEST = Path(os.environ.get("BENCH_MANIFEST") or ROOT / "manifest.yaml")
if not MANIFEST.is_absolute():
    MANIFEST = ROOT / MANIFEST


def load_manifest():
    import yaml
    return yaml.safe_load(MANIFEST.read_text())


def machine_label():
    """e.g. '<model id> Apple M3 16GB' — written on every row so results from different Macs can be merged."""
    import subprocess
    g = lambda k: subprocess.run(["sysctl", "-n", k], capture_output=True, text=True).stdout.strip()
    try:
        return f"{g('hw.model')} {g('machdep.cpu.brand_string')} {int(g('hw.memsize')) // 1073741824}GB"
    except ValueError:
        return "unknown"


# --- safety helpers (security review 2026-10-13) ---------------------------------------------------------------
import re as _re

SAFE_ID = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
# Manifests are code-adjacent: a variant's env may only set these keys (no DYLD_*, PYTHON*, PATH, …).
ENV_ALLOW = {"OMLX_QWEN4_PLE_MODE", "OLLAMA_FLASH_ATTENTION", "OLLAMA_KV_CACHE_TYPE", "MLX_SERVE_ANE_SPLIT"}
# serve_args come after --host and --model, and runners accept abbreviated flags, so only these exact pairs pass.
ARG_ALLOW = {"--engine": r"ds4", "--memory-guard": r"[a-z]+", "--memory-guard-gb": r"\d{1,3}"}


def validate_manifest(m):
    """Fail early on ids that would become unsafe path parts and on env keys outside the allowlist."""
    ids = [v["id"] for v in m.get("variants", [])] + [e["id"] for e in m.get("media", {}).get("voice", {}).get("engines", [])] \
        + [b["id"] for b in m.get("media", {}).get("h3", {}).get("builds", [])] \
        + [r["id"] for r in m.get("media", {}).get("voice", {}).get("references", [])]
    bad = [i for i in ids if not SAFE_ID.match(str(i))]
    if bad:
        raise SystemExit(f"manifest ids must match {SAFE_ID.pattern}: {bad}")
    for v in m.get("variants", []):
        extra = set((v.get("env") or {})) - ENV_ALLOW
        if extra:
            raise SystemExit(f"variant {v['id']}: env keys not allowed: {sorted(extra)} (allowed: {sorted(ENV_ALLOW)})")
        args = v.get("serve_args") or []
        pairs = list(zip(args[::2], args[1::2])) if isinstance(args, list) and len(args) % 2 == 0 else None
        if pairs is None or any(k not in ARG_ALLOW or not _re.fullmatch(ARG_ALLOW[k], str(a)) for k, a in pairs):
            raise SystemExit(f"variant {v['id']}: serve_args must be flag/value pairs from {sorted(ARG_ALLOW)}")
        if v.get("tok_from") and v.get("tokenizer_repo"):
            raise SystemExit(f"variant {v['id']}: set tok_from or tokenizer_repo, not both")
        data = v.get("data_files") or []
        if not isinstance(data, list) or any("/" in str(n) or not str(n).endswith(".bin") for n in data):
            raise SystemExit(f"variant {v['id']}: data_files must be a list of top-level .bin names")


def refuse_root():
    if os.geteuid() == 0:
        raise SystemExit("Don't run the benchmark as root (only sampler/power_sampler.sh needs sudo).")


def exit_on_signals():
    """SIGTERM/SIGHUP (e.g. the Terminal window closing) become SystemExit, so `finally` blocks stop the servers."""
    import signal
    def _bye(signum, frame):
        raise SystemExit(128 + signum)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _bye)


def run_hook(name):
    """Optional local hooks (gitignored): scripts/<name>.local.sh, e.g. to stop/start other GPU-heavy services."""
    import subprocess
    h = ROOT / "scripts" / f"{name}.local.sh"
    if h.exists():
        subprocess.run(["/bin/bash", str(h)], check=False)
