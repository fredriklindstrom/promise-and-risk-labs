"""Shared helpers: hashing, input/policy loading, run logging."""
import datetime
import hashlib
import json
import os
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUTS = ROOT / "inputs"
RESULTS = ROOT / "results"
RUNS = Path(os.environ.get("LAB_RUNS_FILE", str(RESULTS / "runs.jsonl")))
LOCK = ROOT / "models.lock.json"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return sha256_bytes(s.encode("utf-8"))


def sha256_file(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_prompts():
    out = []
    with open(INPUTS / "prompts.jsonl") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_images():
    """Images Fredrik drops in inputs/images/. Never committed."""
    d = INPUTS / "images"
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    return [
        {"id": "img_" + p.stem, "image_path": str(p), "kind": "image"}
        for p in sorted(d.iterdir())
        if p.suffix.lower() in exts
    ] if d.exists() else []


def load_policies():
    with open(INPUTS / "policies.json") as f:
        return json.load(f)


def input_sha(item) -> str:
    if item.get("image_path"):
        return sha256_file(item["image_path"])
    return sha256_text(item["text"])


def lock_entry(model_key):
    if not LOCK.exists():
        return {}
    return json.loads(LOCK.read_text()).get(model_key, {})


def log_run(rec: dict):
    RESULTS.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"), **rec}
    rec.setdefault("host", platform.node().split(".")[0])  # short name only, no network domain
    with open(RUNS, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def score_hash(score: float) -> str:
    """Short hash of the exact float, so identical repeats are visible on screen."""
    return sha256_text(repr(float(score)))[:12]


def verdict(score, threshold):
    return "unsafe" if score > threshold else "safe"


def peak_rss_mb():
    import resource
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(r / (1024 * 1024), 1) if platform.system() == "Darwin" else round(r / 1024, 1)


os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
