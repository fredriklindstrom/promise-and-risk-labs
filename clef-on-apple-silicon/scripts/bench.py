"""Run one Clef configuration: load, warm-up, 60-record accuracy pass, length sweep.

usage: python bench.py --run NAME --backend {mlxc,trevor,torch} --path LOCAL_DIR [--device mps]
Writes results/decisions_<run>.jsonl and results/metrics_<run>.json.
"""
import argparse
import gc
import json
import os
import sys
import time
import traceback

from common import (RESULTS, ROUTING_Q, SWEEP_REPS, SWEEP_SKIP_SECONDS, SWEEP_TARGETS, Timer,
                    env_info, load_testset, median, sweep_state, top, write_jsonl)

SWEEP_MAX_LENGTH = 70000  # above the longest sweep input, so nothing is truncated
GB = 1024 ** 3


class MLXBase:
    def __init__(self):
        import mlx.core as mx
        self.mx = mx

    def sync(self):
        self.mx.synchronize()

    def reset_peak(self):
        self.mx.reset_peak_memory()

    def peak_gb(self):
        return self.mx.get_peak_memory() / GB

    def active_gb(self):
        return self.mx.get_active_memory() / GB

    def free(self):
        self.model = None
        gc.collect()
        self.mx.clear_cache()


class MLXCommunity(MLXBase):
    """mlx-community clef_mlx.py (runs 2, 3)."""

    def load(self, path):
        sys.path.insert(0, path)
        import clef_mlx
        self.lib = clef_mlx
        self.model = clef_mlx.load(path)
        self.tokenizer = self.model.tokenizer

    def n_tokens(self, record, max_length):
        return len(self.lib.encode_record(self.tokenizer, record, processor=self.model.processor,
                                          max_length=max_length, truncate=False).input_ids)

    def predict(self, record, max_length=16384):
        enc, logits = self.model.logits(record, max_length=max_length, truncate=False)
        probs = {q.question_id: dict(zip(q.option_ids, self.mx.softmax(lg.astype(self.mx.float32)).tolist()))
                 for q, lg in zip(enc.questions, logits)}
        return probs, len(enc.input_ids)


class MLXTrevor(MLXBase):
    """TrevorJS clef_mlx.py (run 5)."""

    def load(self, path):
        sys.path.insert(0, path)
        import clef_mlx
        self.lib = clef_mlx
        self.model = clef_mlx.load(path)
        self.tokenizer = self.model[1]

    def n_tokens(self, record, max_length):
        return len(self.lib.encode_record(self.tokenizer, record, max_length)[0])

    def predict(self, record, max_length=16384):
        n = self.n_tokens(record, max_length)
        if n >= max_length:
            raise ValueError(f"would truncate: {n} >= {max_length}")
        return self.lib.decide(self.model, record, max_length), n


class Torch:
    """Cloudflare joint_schema_model.py (runs 1, 4)."""

    def __init__(self, device):
        import torch
        self.torch = torch
        self.device = torch.device(device)
        self.peak = 0.0

    def load(self, path):
        sys.path.insert(0, path)
        import joint_schema_model as jsm
        self.lib = jsm
        self.model, self.processor = jsm.load_release_model(path, device=str(self.device))
        self.tokenizer = self.processor.tokenizer
        self._track()

    def sync(self):
        if self.device.type == "mps":
            self.torch.mps.synchronize()

    def _track(self):
        if self.device.type == "mps":
            self.peak = max(self.peak, self.torch.mps.driver_allocated_memory() / GB)

    def reset_peak(self):
        self.peak = 0.0
        self._track()

    def peak_gb(self):
        self._track()
        return self.peak

    def active_gb(self):
        return self.torch.mps.current_allocated_memory() / GB if self.device.type == "mps" else 0.0

    def free(self):
        self.model = None
        gc.collect()
        if self.device.type == "mps":
            self.torch.mps.empty_cache()

    def n_tokens(self, record, max_length):
        return len(self.lib.encode_record(self.tokenizer, record, max_length=max_length,
                                          processor=self.processor).input_ids)

    def predict(self, record, max_length=16384):
        enc = self.lib.encode_record(self.tokenizer, record, max_length=max_length, processor=self.processor)
        batch = self.lib.collate_records([enc], self.tokenizer.pad_token_id, self.device)
        with self.torch.inference_mode():
            logits = self.model(batch)[0]
        probs = {q.question_id: dict(zip(q.option_ids, ql.float().softmax(-1).tolist()))
                 for q, ql in zip(enc.questions, logits)}
        self._track()
        return probs, len(enc.input_ids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--backend", required=True, choices=["mlxc", "trevor", "torch"])
    ap.add_argument("--path", required=True)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--targets", default=",".join(map(str, SWEEP_TARGETS)))
    ap.add_argument("--reps", type=int, default=SWEEP_REPS)
    ap.add_argument("--reps-long", type=int, default=3, help="reps for targets >= 32000")
    ap.add_argument("--skip-warm-load", action="store_true")
    ap.add_argument("--skip-accuracy", action="store_true")
    args = ap.parse_args()

    out = RESULTS / f"metrics_{args.run}.json"
    m = {"run": args.run, "backend": args.backend, "path": args.path, "device": args.device,
         "env": env_info(), "env_vars": {k: v for k, v in os.environ.items() if k.startswith(("PYTORCH", "MLX"))},
         "started": time.strftime("%Y-%m-%d %H:%M:%S"), "errors": []}

    def save():
        out.write_text(json.dumps(m, indent=1))

    b = Torch(args.device) if args.backend == "torch" else (MLXCommunity() if args.backend == "mlxc" else MLXTrevor())

    # ---- load (first load in this process; a second load measures the OS-cached case)
    try:
        b.reset_peak()
        with Timer() as t:
            b.load(args.path)
            b.sync()
        m["load_s_first"] = round(t.s, 2)
        m["mem_after_load_gb"] = round(b.active_gb(), 2)
        if not args.skip_warm_load:
            b.free()
            with Timer() as t:
                b.load(args.path)
                b.sync()
            m["load_s_second"] = round(t.s, 2)
        m["loaded"] = True
    except Exception as e:
        m["loaded"] = False
        m["errors"].append({"stage": "load", "error": repr(e), "trace": traceback.format_exc()[-3000:]})
        save()
        print("LOAD FAILED", e)
        return
    print(f"loaded in {m['load_s_first']}s, active {m['mem_after_load_gb']} GB", flush=True)
    save()

    tests = load_testset()

    # ---- warm-up: 3 decisions, discarded
    for r in tests[:3]:
        b.predict(r["record"])
    b.sync()

    # ---- accuracy pass, batch size 1
    if not args.skip_accuracy:
        b.reset_peak()
        rows, lat = [], []
        for r in tests:
            try:
                with Timer(b.sync) as t:
                    probs, n = b.predict(r["record"])
            except Exception as e:
                m["errors"].append({"stage": "accuracy", "id": r["id"], "error": repr(e)})
                continue
            lat.append(t.s)
            rows.append({"id": r["id"], "task": r["task"], "input_tokens": n, "latency_s": round(t.s, 4),
                         "probs": probs, "top": {q: top(p) for q, p in probs.items()},
                         "expected": r["expected"]})
        write_jsonl(RESULTS / f"decisions_{args.run}.jsonl", rows)
        m["accuracy_pass"] = {"n": len(rows), "median_latency_s": round(median(lat), 4) if lat else None,
                              "median_input_tokens": median([x["input_tokens"] for x in rows]) if rows else None,
                              "peak_gb": round(b.peak_gb(), 2)}
        print("accuracy pass:", m["accuracy_pass"], flush=True)
        save()

    # ---- length sweep
    overhead = b.n_tokens({"state": {"ticket": ""}, "questions": ROUTING_Q}, SWEEP_MAX_LENGTH)
    m["sweep"] = []
    stop = None
    for target in [int(x) for x in args.targets.split(",")]:
        if stop:
            m["sweep"].append({"target": target, "skipped": stop})
            continue
        rec = {"state": sweep_state(b.tokenizer, max(10, target - overhead)), "questions": ROUTING_Q}
        n = b.n_tokens(rec, SWEEP_MAX_LENGTH)
        b.reset_peak()
        times, entry = [], {"target": target, "input_tokens": n}
        try:
            for rep in range(args.reps_long if target >= 32000 else args.reps):
                with Timer(b.sync) as t:
                    b.predict(rec, max_length=SWEEP_MAX_LENGTH)
                times.append(t.s)
                print(f"  {target} tok rep {rep + 1}: {t.s:.2f}s", flush=True)
                if t.s > SWEEP_SKIP_SECONDS:
                    stop = f"previous length took {t.s:.0f}s for one run (> {SWEEP_SKIP_SECONDS}s)"
                    break
        except Exception as e:
            entry["error"] = repr(e)
            stop = f"error at {target}: {e!r}"[:300]
        if times:
            med = median(times)
            entry.update({"reps": len(times), "median_s": round(med, 3), "min_s": round(min(times), 3),
                          "max_s": round(max(times), 3), "tok_per_s": round(n / med, 1),
                          "peak_gb": round(b.peak_gb(), 2), "times": [round(x, 3) for x in times]})
        m["sweep"].append(entry)
        print("sweep:", {k: v for k, v in entry.items() if k != "times"}, flush=True)
        save()

    m["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()
    print("DONE", out)


if __name__ == "__main__":
    main()
