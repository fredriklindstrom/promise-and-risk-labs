"""Screen-recording display. Replays logged runs from results/runs.jsonl one per line.
  python show.py A --condition llamacpp --input borderline
  python show.py B --input real_driver
  python show.py --live B --input real_driver     # runs it for real instead of replaying
"""
import argparse, json, subprocess, sys
from common import RUNS
import display
ap = argparse.ArgumentParser()
ap.add_argument("experiment", choices=["A", "B"])
ap.add_argument("--input", default="real_driver")
ap.add_argument("--condition", default="defaults")
ap.add_argument("--model", default="Shieldstral-1.0-3B")
ap.add_argument("--delay", type=float, default=0.4)
ap.add_argument("--live", action="store_true")
a = ap.parse_args()
if a.live:
    script = "run_defaults.py" if a.experiment == "A" else "run_pinned.py"
    extra = ["--conditions", a.condition] if a.experiment == "A" else []
    sys.exit(subprocess.call([sys.executable, script, "--inputs", a.input, "--delay", str(a.delay), *extra]))
rs = [json.loads(l) for l in open(RUNS)]
rs = [r for r in rs if r["experiment"] == a.experiment and r["input_id"] == a.input and a.model in r["model"]
      and (a.experiment != "A" or r.get("condition") == a.condition)][-20:]
if not rs:
    sys.exit("no logged runs match")
label = a.model.split("/")[-1].replace("-1.0-3B", "")[:16]
if a.experiment == "A":
    display.banner(f"{a.input} | temp {rs[0]['temperature']} | {'random seed' if rs[0]['seed'] else 'no seed'}")
    yes = 0
    for i, r in enumerate(rs, 1):
        w = "Yes" if r["verdict"] == "unsafe" else "No"; yes += w == "Yes"
        display.line(i, label, w, delay=a.delay)
    display.footer(f"Yes {yes} / No {len(rs) - yes}")
else:
    display.banner(f"{a.input} | pinned | threshold {rs[0]['threshold']}")
    for i, r in enumerate(rs, 1):
        display.line(i, label, r["verdict"], r["score"], r["score_hash"], delay=a.delay)
    u = len({r["score_repr"] for r in rs})
    display.footer(f"{len(rs)}/{len(rs)} identical" if u == 1 else f"{u} distinct scores")
