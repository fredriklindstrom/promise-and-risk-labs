"""Compare each run's decisions against its BF16 reference and build runs.csv.

usage: python compare.py   (reads results/metrics_*.json and decisions_*.jsonl)
"""
import csv
import json

from common import RESULTS, SWEEP_TARGETS

# run name -> (model, precision, runtime, reference run)
RUNS = {
    # Clef 27B: run 1 (PyTorch BF16) did not load, so run 6 (MLX BF16, same weights) is the reference.
    "r1_clef_bf16_torch": ("Clef 27B", "BF16", "PyTorch + Transformers", None),
    "r6_clef_bf16_mlx": ("Clef 27B", "BF16", "MLX (clef_mlx.py, own conversion)", None),
    "r2_clef_8bit_mlx": ("Clef 27B", "8-bit", "MLX (clef_mlx.py)", "r6_clef_bf16_mlx"),
    "r3_clef_4bit_mlx": ("Clef 27B", "4-bit", "MLX (clef_mlx.py)", "r6_clef_bf16_mlx"),
    "r4_flash_bf16_torch": ("Clef-flash 9B", "BF16", "PyTorch + Transformers", None),
    "r7_flash_bf16_mlx": ("Clef-flash 9B", "BF16", "MLX (clef_mlx.py, own conversion)", "r4_flash_bf16_torch"),
    "r5_flash_4bit_mlx": ("Clef-flash 9B", "4-bit", "MLX (TrevorJS clef_mlx.py)", "r4_flash_bf16_torch"),
    "r5b_flash_4bit_mlxc": ("Clef-flash 9B", "4-bit", "MLX (mlx-community clef_mlx.py)", "r4_flash_bf16_torch"),
}


def load_decisions(run):
    p = RESULTS / f"decisions_{run}.jsonl"
    if not p.exists():
        return None
    return {r["id"]: r for r in map(json.loads, p.read_text().splitlines())}


def compare(run, ref):
    a, b = load_decisions(run), load_decisions(ref)
    if not a or not b:
        return None
    n = agree = flips5 = flips8 = 0
    diffs, disagreements = [], []
    for rid, ra in a.items():
        rb = b.get(rid)
        if not rb:
            continue
        for q, pb in rb["probs"].items():
            pa = ra["probs"][q]
            tb, ta = max(pb, key=pb.get), max(pa, key=pa.get)
            n += 1
            agree += ta == tb
            diffs.append(abs(pa[tb] - pb[tb]))
            if "true" in pb:  # yes/no question
                flips5 += (pa["true"] >= 0.5) != (pb["true"] >= 0.5)
                flips8 += (pa["true"] >= 0.8) != (pb["true"] >= 0.8)
            if ta != tb:
                disagreements.append({"id": rid, "question": q, "ref_answer": tb, "ref_p": round(pb[tb], 4),
                                      "run_answer": ta, "run_p": round(pa[ta], 4)})
    return {"n": n, "agreement_pct": round(100 * agree / n, 1), "mean_abs_diff": round(sum(diffs) / n, 4),
            "max_abs_diff": round(max(diffs), 4), "flips_0_5": flips5, "flips_0_8": flips8,
            "disagreements": disagreements}


def expected_agreement(run):
    d = load_decisions(run)
    if not d:
        return None
    n = ok = 0
    for r in d.values():
        for q, exp in r["expected"].items():
            if exp is None:
                continue
            n += 1
            ok += r["top"][q] == exp
    return round(100 * ok / n, 1) if n else None


def main():
    rows, comparisons = [], {}
    for run, (model, prec, runtime, ref) in RUNS.items():
        mp = RESULTS / f"metrics_{run}.json"
        if not mp.exists():
            continue
        m = json.loads(mp.read_text())
        sweep = {s["target"]: s for s in m.get("sweep", [])}
        peaks = [s.get("peak_gb") for s in sweep.values() if s.get("peak_gb")]
        acc_peak = (m.get("accuracy_pass") or {}).get("peak_gb")
        row = {"model": model, "precision": prec, "runtime": runtime, "device": "mps (PyTorch)" if "torch" in run else "Metal (MLX)",
               "loaded": "yes" if m.get("loaded") else "no", "load_time_s": m.get("load_s_first"),
               "load_time_second_s": m.get("load_s_second"),
               "peak_memory_gib": max(peaks + ([acc_peak] if acc_peak else []), default=None)}
        for t in SWEEP_TARGETS:
            s = sweep.get(t, {})
            row[f"median_s_{t}"] = s.get("median_s", s.get("skipped") and "skipped" or s.get("error") and "error")
            row[f"tok_per_s_{t}"] = s.get("tok_per_s")
        c = compare(run, ref) if ref else None
        comparisons[run] = c
        row.update({"agreement_vs_bf16_pct": c and c["agreement_pct"], "mean_prob_diff": c and c["mean_abs_diff"],
                    "max_prob_diff": c and c["max_abs_diff"], "flips_0_5": c and c["flips_0_5"],
                    "flips_0_8": c and c["flips_0_8"], "agreement_vs_expected_pct": expected_agreement(run),
                    "notes": "; ".join([m.get("notes", "")] + [e["error"][:220] for e in m.get("errors", [])]).strip("; ")})
        rows.append(row)
    if rows:
        with open(RESULTS / "runs.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    (RESULTS / "comparisons.json").write_text(json.dumps(comparisons, indent=1))
    for r in rows:
        print(r)
    for run, c in comparisons.items():
        if c:
            print(run, {k: v for k, v in c.items() if k != "disagreements"}, "disagreements:", len(c["disagreements"]))


if __name__ == "__main__":
    main()
