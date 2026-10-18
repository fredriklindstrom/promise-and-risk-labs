"""Jais 3 speed + memory runs through the nov17-bench harness, so the numbers are measured exactly like
the Nov 17 rows (same workload, prompt builder, greedy, 512 output tokens, warm-up + 3 reps, watchdog,
sampler, power window, Wh) and Bench Monitor shows them live.

Kept out of the Nov 17 record: history -> runs/history.jais3.jsonl, rows -> results/summary.jais3.csv.
The one difference from a harness MLX run: the worker is jais_worker.py (reviewed, pinned k2_horizon
model code; see that file), because mlx-lm has no k2_horizon model.

    cd ~/p-and-r/nov17-bench && .venv/bin/python ~/p-and-r/jais3-bench/run_speed.py --verify
    cd ~/p-and-r/nov17-bench && .venv/bin/python ~/p-and-r/jais3-bench/run_speed.py [--variants id,id] [--contexts 8192]
"""
import argparse, json, os, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = Path.home() / "p-and-r/nov17-bench"
sys.path.insert(0, str(BENCH))

from bench import ROOT, refuse_root, exit_on_signals          # noqa: E402
from bench import harness as H                                 # noqa: E402
from bench import events as E                                   # noqa: E402
from bench.runners import adapters as A                         # noqa: E402

HISTORY = E.RUNS / "history.jais3.jsonl"
H.SUMMARY = ROOT / "results/summary.jais3.csv"

MODELS = {
    "jais3-32b": {"name": "Jais 3 32B", "arch": "dense", "params": "32B", "active_params": None,
                  "template_kwargs": {"reasoning_effort": "low"}},
    "jais3-36b-a4b": {"name": "Jais 3 MoVA 36B-A4B", "arch": "moe", "params": "36B", "active_params": 4,
                      "template_kwargs": {"reasoning_effort": "low"}},
}
CONTEXTS = [1024, 8192, 32768, 131072]   # 1K added 2026-10-17 for parity with IFM's short-prompt figure and the Nov 17 1K rows
VARIANTS = [
    {"id": "jais3-36b-a4b-mlx4", "model": "jais3-36b-a4b", "quant": "MLX 4-bit (IFM mixed, 5.6 bpw)",
     "file_format": "mlx-safetensors", "runner": "mlx-jais", "repo": "IFM/Jais-3-MoVA-36B-A4B-MLX-4bit",
     "revision": "03a84009d9e04f33f84d64b22194eae9d900fcbe", "contexts": CONTEXTS},
    {"id": "jais3-32b-mlx4", "model": "jais3-32b", "quant": "MLX 4-bit (IFM mixed)",
     "file_format": "mlx-safetensors", "runner": "mlx-jais", "repo": "IFM/Jais-3-32B-MLX-4bit",
     "revision": "98ca8c570eebfa8d863439cb921279d7a10364f9", "contexts": CONTEXTS},
]


# llama.cpp check (--gguf, Fredrik 2026-10-17): does Homebrew's llama.cpp run K2 Horizon? IFM's own GGUF, one 8K rep.
GGUF = [{"id": "jais3-36b-a4b-q4km", "model": "jais3-36b-a4b", "quant": "GGUF Q4_K_M", "file_format": "gguf",
         "runner": "llamacpp", "repo": "IFM/Jais-3-MoVA-36B-A4B-GGUF", "revision": "8b2c5ab2feffeeca416c99ec972e543db6a97dbe",
         "file": "Jais-3-MoVA-36B-A4B-Q4_K_M.gguf", "contexts": [8192]}]


class JaisEventWriter(E.EventWriter):
    def emit(self, type_, **fields):
        rec = {**fields, "t": E.now(), "type": type_}
        line = json.dumps(rec) + "\n"
        with open(self.path, "a") as f:
            f.write(line)
        if type_ in ("run_end", "run_fail"):
            with open(HISTORY, "a") as f:
                f.write(line)
        return rec


class JaisMLXRunner(A.MLXRunner):
    name = "MLX"
    def start(self, ctx):
        self.path = A.snapshot(self.v["repo"], self.v.get("revision"))
        self.tok = A.HFTok(self.path)
        self.cmd = [str(ROOT / ".venv/bin/python"), str(HERE / "jais_worker.py"), "--model", str(self.path)]
        self.log_path = self.run_dir_fn() / "runner.log"
        self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(self.log_path, "ab"),
                                     cwd=ROOT, text=True, bufsize=1, start_new_session=True,
                                     env={**os.environ, "HF_HUB_OFFLINE": "1"})
        msg = self._read()
        return {"load_s": msg["load_s"], "cmd": " ".join(self.cmd), "pid": self.proc.pid,
                "load_note": "worker jais_worker.py: reviewed pinned k2_horizon.py, model_file=None"}


class JaisHarness(H.Harness):
    def __init__(self, args):
        super().__init__(args)
        self.m["models"].update(MODELS)
        self.m["variants"] = VARIANTS
        if args.gguf:
            self.m["variants"] = GGUF
            self.m["settings"]["timed_reps"] = 1
        if args.reps:
            # Fredrik 2026-10-17: the 32B at 128K takes ~55 min per run, so warm-up + 1 timed rep there.
            self.m["settings"]["timed_reps"] = args.reps

    def write_fail_row(self, rid, cell, mcfg, version, reason, fit="no", started=None, extra=None):
        # Same as Harness.write_fail_row, history line to the Jais file.
        orig = E.RUNS / "history.jsonl"
        before = orig.stat().st_size if orig.exists() else 0
        super().write_fail_row(rid, cell, mcfg, version, reason, fit, started, extra)
        if orig.exists() and orig.stat().st_size > before:      # move the line it just appended
            with open(orig, "rb+") as f:
                f.seek(before); line = f.read(); f.seek(before); f.truncate()
            with open(HISTORY, "ab") as f:
                f.write(line)


def verify():
    """bench.download's plan + hash check for the two Jais repos (records go to results/checksums.jsonl).
    Its unsafe-file scan is skipped on purpose: the snapshot holds k2_horizon.py, which is never imported
    (model_file=None); the reviewed copy in vendor/ is checked here against the snapshot's."""
    from bench import download as D
    import hashlib
    bad = 0
    for v in VARIANTS:
        job = {"id": v["id"], "repo": v["repo"], "revision": v["revision"], "file": None, "allow": None}
        sha, files, skipped = D.plan(job)
        local = D.snapshot_path(v["repo"], sha)
        if local is None:
            print(f"{v['id']}: not on disk"); bad += 1; continue
        repo_py = hashlib.sha256((local / "k2_horizon.py").read_bytes()).hexdigest()
        ours = hashlib.sha256((HERE / "vendor/k2_horizon.py").read_bytes()).hexdigest()
        n = D.verify_files(job, sha, files, local)
        print(f"{v['id']}: {len(files)} files, {n} mismatches; skipped {skipped}; repo k2_horizon.py "
              f"{'==' if repo_py == ours else '!='} reviewed copy")
        bad += n + (repo_py != ours)
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="j3")
    ap.add_argument("--load-condition", default="clean", choices=["clean", "everyday", "concurrent"])
    ap.add_argument("--variants")
    ap.add_argument("--contexts")
    ap.add_argument("--no-needle", action="store_true", default=True)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--gguf", action="store_true", help="llama.cpp check: IFM's 36B-A4B GGUF Q4_K_M at 8K, one rep")
    ap.add_argument("--reps", type=int, help="timed reps per cell (default: the manifest's 3)")
    a = ap.parse_args()
    refuse_root()
    if a.verify:
        sys.exit(1 if verify() else 0)
    exit_on_signals()
    ps = subprocess.run(["/usr/bin/pgrep", "-fl", "mlx_lm.server|llama-server|ollama runner"],
                        capture_output=True, text=True).stdout.strip()
    if ps:
        sys.exit(f"Another model server is running; stop it first:\n{ps}")
    A.RUNNERS["mlx-jais"] = JaisMLXRunner
    H.RUNNERS["mlx-jais"] = JaisMLXRunner
    H.EventWriter = JaisEventWriter
    E.acquire_bench_lock("jais3 speed runs")
    JaisHarness(a).run()


if __name__ == "__main__":
    main()
