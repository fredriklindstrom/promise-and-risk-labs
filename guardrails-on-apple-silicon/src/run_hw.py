"""Experiment D: same model, same input, different device/precision."""
import argparse, json, time
import torch
from common import load_prompts, log_run, input_sha, sha256_text, load_policies, score_hash
from models import load, policy_ids_for
ap = argparse.ArgumentParser(); ap.add_argument("--model", default="shieldstral-3b"); a = ap.parse_args()
pol = load_policies()
items = [p for p in load_prompts() if a.model.startswith("promptguard") or not p.get("promptguard_only")]
PID = policy_ids_for(a.model)[0]
SETUPS = [("cpu", "fp32"), ("mps", "fp32"), ("mps", "bf16"), ("mps", "fp16"), ("cpu", "bf16")]
table = {}
for dev, dt in SETUPS:
    t = time.perf_counter(); m = load(a.model, device=dev, dtype=dt); load_s = time.perf_counter() - t
    for it in items:
        torch.manual_seed(1234)
        r = m.score(it, PID)
        table.setdefault(it["id"], {})[f"{dev}/{dt}"] = r["score"]
        log_run({"experiment": "D", **m.meta, "mode": "scoring", "temperature": 0.0, "top_p": None, "seed": 1234,
                 "template_sha256": r["template_sha256"], "policy_id": PID,
                 "policy_sha256": sha256_text(json.dumps(pol.get(PID), sort_keys=True)),
                 "input_id": it["id"], "input_sha256": input_sha(it), "placeholder_input": bool(it.get("placeholder")),
                 "score": r["score"], "score_repr": repr(r["score"]), "score_hash": score_hash(r["score"]),
                 "threshold": 0.5, "verdict": "unsafe" if r["score"] > 0.5 else "safe",
                 "raw_output": r["raw_output"], "elapsed_ms": r["elapsed_ms"], "load_s": round(load_s, 1)})
        print(f"{dev}/{dt:5s} {it['id']:22s} {r['score']:.8f}  {r['elapsed_ms']:8.1f} ms")
    del m
    if dev == "mps": torch.mps.empty_cache()
json.dump(table, open(f"../results/charts/D_hardware_{a.model}.json", "w"), indent=1)
