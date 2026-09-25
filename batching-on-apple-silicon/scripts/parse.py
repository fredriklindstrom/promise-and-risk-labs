#!/usr/bin/env python3
"""Parse runs/run{1,2,3}.txt -> per-batch medians and run-to-run spread."""
import re, statistics, sys, pathlib
D = pathlib.Path(__file__).resolve().parent.parent / "runs"
P, G = 128, 128
rows = {}  # batch -> list of (s_tg, s, peak) per run
for r in (1, 2, 3):
    txt = (D / f"run{r}.txt").read_text()
    for b, tps, peak, tt in re.findall(
        r"batch_size=(\d+)\.\s*\nTrial 1:\s+prompt_tps=[\d.]+, generation_tps=([\d.]+), peak_memory=([\d.]+), total_time=([\d.]+)", txt):
        b = int(b)
        rows.setdefault(b, []).append((float(tps), b * (P + G) / float(tt), float(peak)))
print(f"{'N':>3} {'S_TG med':>9} {'per-req':>8} {'S med':>8} {'peakGB':>7}  S_TG runs (spread)")
for b in sorted(rows):
    v = rows[b]
    stg = [x[0] for x in v]; s = [x[1] for x in v]; pk = max(x[2] for x in v)
    m = statistics.median(stg)
    spread = (max(stg) - min(stg)) / m * 100
    sspread = (max(s) - min(s)) / statistics.median(s) * 100
    flag = "  <-- >10%" if max(spread, sspread) > 10 else ""
    print(f"{b:>3} {m:>9.2f} {m/b:>8.2f} {statistics.median(s):>8.2f} {pk:>7.2f}  {', '.join(f'{x:.2f}' for x in stg)} ({spread:.1f}%, S {sspread:.1f}%){flag}")
    if len(v) != 3: print(f"    WARNING: {len(v)} runs parsed for batch {b}")
