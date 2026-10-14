"""Model Cache Manager capture: list-only snapshots (never --delete)."""
import datetime, json, os, subprocess, sys
from pathlib import Path
from . import ROOT
from .diskguard import status

APP = Path.home() / "Applications/Model Cache Manager.app/Contents/MacOS/ModelCacheManager"
HUB = Path(os.environ.get("HF_HUB_CACHE") or Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub")
OLLAMA = Path(os.environ.get("OLLAMA_MODELS", Path.home() / ".ollama/models"))


def du(path):
    if not Path(path).exists():
        return None
    out = subprocess.run(["du", "-sk", str(path)], capture_output=True, text=True).stdout.split()
    return round(int(out[0]) * 1024 / 1e9, 2) if out else None


def snapshot(label):
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = ROOT / "results/mcm"
    outdir.mkdir(parents=True, exist_ok=True)
    text = subprocess.run([str(APP), "--list"], capture_output=True, text=True, timeout=600).stdout
    (outdir / f"{label}_{ts}.txt").write_text(text)
    js = subprocess.run([str(APP), "--list", "--json"], capture_output=True, text=True, timeout=600)
    has_json = js.returncode == 0 and js.stdout.lstrip().startswith(("[", "{"))
    if has_json:
        (outdir / f"{label}_{ts}.json").write_text(js.stdout)
    meta = {
        "label": label, "t": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "mcm_total_line": text.strip().splitlines()[-1] if text.strip() else None,
        "du_hf_hub_gb": du(HUB), "du_ollama_gb": du(OLLAMA), "disk": status(), "json": has_json,
    }
    with open(outdir / "snapshots.jsonl", "a") as f:
        f.write(json.dumps(meta) + "\n")
    return meta


if __name__ == "__main__":
    print(json.dumps(snapshot(sys.argv[1] if len(sys.argv) > 1 else "adhoc"), indent=2))
