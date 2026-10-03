"""Re-run inv-10 and inv-18 with the published invoice numbers (KS-) and compare to the measured run."""
import json, sys
sys.path.insert(0, "scripts")
from bench import MLXCommunity, MLXTrevor, Torch
run, backend, path = sys.argv[1], sys.argv[2], sys.argv[3]
new = {r["id"]: r for r in map(json.loads, open("data/testset.jsonl"))}
old = {r["id"]: r for r in map(json.loads, open(f"runs/decisions_{run}.jsonl"))}
b = Torch("mps") if backend == "torch" else (MLXCommunity() if backend == "mlxc" else MLXTrevor())
b.load(path)
out = []
for rid in ["inv-10", "inv-18"]:
    p, _ = b.predict(new[rid]["record"])
    po, pn = old[rid]["probs"]["risk"], p["risk"]
    out.append({"run": run, "id": rid, "top_measured": max(po, key=po.get), "top_published": max(pn, key=pn.get),
                "max_abs_diff": round(max(abs(po[k] - pn[k]) for k in po), 4)})
print(json.dumps(out))
