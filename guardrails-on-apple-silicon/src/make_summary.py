"""results/runs.jsonl -> results/summary.csv + results/charts/*.json"""
import csv, json, collections
from common import RUNS, RESULTS
runs = [json.loads(l) for l in open(RUNS)]
rows, A, B = [], {}, {}
g = collections.defaultdict(list)
for r in runs:
    g[(r["experiment"], r["model"], r.get("condition") or r.get("device", "") + "/" + r.get("dtype", ""), r["input_id"], r["policy_id"])].append(r)
for (exp, model, cond, inp, pid), rs in sorted(g.items()):
    row = {"experiment": exp, "model": model, "condition": cond, "input_id": inp, "policy_id": pid, "n": len(rs),
           "placeholder_input": rs[0].get("placeholder_input", False)}
    if exp == "A":
        c = collections.Counter("Yes" if x["verdict"] == "unsafe" else "No" if x["verdict"] == "safe" else "Unparsed" for x in rs)
        row.update(yes=c["Yes"], no=c["No"], unparsed=c["Unparsed"], temperature=rs[0]["temperature"], seed="random" if rs[0]["seed"] else "none")
        A.setdefault(model.split("/")[-1], {}).setdefault(rs[0]["condition"], {})[inp] = {"yes": c["Yes"], "no": c["No"], "runs": len(rs),
                                                    "settings": {k: rs[0].get(k) for k in ("do_sample", "temperature", "top_k", "top_p", "min_p")}}
    elif exp in ("B", "D"):
        s = [x["score"] for x in rs]
        row.update(min=min(s), max=max(s), unique=len(set(s)), verdict_0_5=rs[0]["verdict"])
        if exp == "B":
            B.setdefault(model.split("/")[-1], {})[inp] = {"score": rs[0]["score"], "hash": rs[0]["score_hash"], "runs": len(s), "unique": len(set(s)), "verdict": rs[0]["verdict"]}
    elif exp == "C":
        v = rs[0]["verdicts"]
        row.update(score=rs[0]["score"], v_0_3=v["0.3"], v_0_5=v["0.5"], v_0_7=v["0.7"], expected=rs[0]["expected"],
                   matches_expectation=rs[0]["matches_expectation"])
    rows.append(row)
keys = []
for r in rows:
    keys += [k for k in r if k not in keys]
with open(RESULTS / "summary.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)
json.dump(A, open(RESULTS / "charts/A_tallies.json", "w"), indent=1)
json.dump(B, open(RESULTS / "charts/B_pinned.json", "w"), indent=1)
Ctab = [{"model": r["model"].split("/")[-1], "input_id": r["input_id"], "policy_id": r["policy_id"], "score": r["score"],
         **{f"v@{t}": r["verdicts"][t] for t in ("0.3", "0.5", "0.7")}, "expected": r["expected"],
         "matches_expectation": r["matches_expectation"], "placeholder_input": r.get("placeholder_input", False)}
        for r in runs if r["experiment"] == "C"]
json.dump(Ctab, open(RESULTS / "charts/C_scope_table.json", "w"), indent=1)
print("rows", len(rows))
for mdl in sorted({r["model"] for r in runs}):
    mem = max((r.get("gpu_mem_mb") or 0) for r in runs if r["model"] == mdl)
    el = sorted(r["elapsed_ms"] for r in runs if r["model"] == mdl and r["experiment"] in ("B", "C"))
    ge = sorted(r["elapsed_ms"] for r in runs if r["model"] == mdl and r["experiment"] == "A")
    print(f"{mdl} ({next(r["dtype"] for r in runs if r["model"] == mdl and r["experiment"] == "B")}): peak MPS MB {mem} | score median {el[len(el)//2]} ms p95 {el[int(len(el)*.95)]} | generate median {ge[len(ge)//2] if ge else None} ms")
