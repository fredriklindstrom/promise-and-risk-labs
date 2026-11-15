"""Experiment B: scoring mode, everything pinned. Expect bit-identical scores."""
import argparse, json, sys
import torch
from common import load_prompts, load_images, input_sha, log_run, sha256_text, load_policies, score_hash, verdict
from models import load, gpu_mem_mb, policy_ids_for
import display

ap = argparse.ArgumentParser()
ap.add_argument("--models", default="shieldstral-3b")
ap.add_argument("--runs", type=int, default=20)
ap.add_argument("--inputs", default="")
ap.add_argument("--threshold", type=float, default=0.5)
ap.add_argument("--seed", type=int, default=1234)
ap.add_argument("--dtype", default=None)
ap.add_argument("--device", default="mps")
ap.add_argument("--delay", type=float, default=0.0)
a = ap.parse_args()

torch.manual_seed(a.seed)
torch.use_deterministic_algorithms(True, warn_only=True)
pol = load_policies()
items = load_prompts() + load_images()
if a.inputs:
    items = [i for i in items if i["id"] in a.inputs.split(",")]
results, drift = {}, []

for key in a.models.split(","):
    m = load(key, device=a.device, dtype=a.dtype)
    pid = policy_ids_for(key)[0]  # likeness / injection
    for it in items:
        if not m.supports(it, pid) or (it.get("promptguard_only") and not key.startswith("promptguard")):
            continue
        display.banner(f"B / pinned / {key} / {it['id']}  (threshold {a.threshold})")
        scores = []
        for n in range(1, a.runs + 1):
            torch.manual_seed(a.seed)
            r = m.score(it, pid)
            s = r["score"]; scores.append(s)
            log_run({"experiment": "B", **m.meta, "run": n, "mode": "scoring", "temperature": 0.0, "top_p": None,
                     "seed": a.seed, "template_sha256": r.get("template_sha256"), "policy_id": pid,
                     "policy_sha256": sha256_text(json.dumps(pol.get(pid), sort_keys=True)),
                     "input_id": it["id"], "input_sha256": input_sha(it), "placeholder_input": bool(it.get("placeholder")),
                     "score": s, "score_repr": repr(s), "score_hash": score_hash(s), "threshold": a.threshold,
                     "verdict": verdict(s, a.threshold), "raw_output": r["raw_output"],
                     "elapsed_ms": r["elapsed_ms"], "gpu_mem_mb": gpu_mem_mb()})
            display.line(n, key, verdict(s, a.threshold), s, score_hash(s), delay=a.delay)
            if s != scores[0]:
                drift.append((key, it["id"], n, repr(scores[0]), repr(s)))
        u = len(set(scores))
        results[f"{key}|{it['id']}"] = {"runs": len(scores), "unique": u, "min": min(scores), "max": max(scores)}
        display.footer(f"{len(scores)}/{len(scores)} identical" if u == 1 else f"NOT IDENTICAL: {u} distinct scores")
        if u != 1:
            print("DRIFT FOUND, stopping this model:", drift[-1]); break
    del m; torch.mps.empty_cache()

print(json.dumps(results, indent=1))
if drift:
    print("FINDING: score drift in pinned mode:", drift); sys.exit(2)
