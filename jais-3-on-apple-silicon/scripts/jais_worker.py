"""Jais 3 MLX worker: the nov17-bench mlx_worker protocol, for the k2_horizon architecture.

mlx-lm has no k2_horizon model yet; IFM's MLX repos ship one as k2_horizon.py and ask for
trust_remote_code. The nov17-bench harness refuses repo code on principle, so this worker does not
load the repo's file. It registers a REVIEWED, PINNED copy (vendor/k2_horizon.py, sha256 below)
as mlx_lm.models.k2_horizon and loads with model_file=None, the same flags the harness uses.

Protocol (unchanged from bench/runners/mlx_worker.py): stdout {"event":"loaded"}, then per
request "first" / "tick" / "done"; stdin {"messages":[...], "max_tokens":N, "template_kwargs":{...}}.
"""
import argparse, hashlib, importlib.util, json, os, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENDOR = HERE / "vendor/k2_horizon.py"
VENDOR_SHA256 = "80b501f2ea7364842bd25659928d3af7692238e7c6c2a50bad2f35f61f5483bc"   # reviewed 2026-10-17


def register_k2_horizon():
    got = hashlib.sha256(VENDOR.read_bytes()).hexdigest()
    if got != VENDOR_SHA256:
        sys.exit(f"vendor/k2_horizon.py sha256 {got} != reviewed {VENDOR_SHA256}; refusing")
    spec = importlib.util.spec_from_file_location("mlx_lm.models.k2_horizon", VENDOR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mlx_lm.models.k2_horizon"] = mod
    spec.loader.exec_module(mod)


def out(**k):
    sys.stdout.write(json.dumps(k) + "\n")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--seed", type=int, default=17)
    a = ap.parse_args()
    import mlx.core as mx
    from mlx_lm import load, stream_generate
    from mlx_lm.sample_utils import make_sampler
    mx.random.seed(a.seed)
    cfg = json.load(open(os.path.join(a.model, "config.json")))
    if cfg.get("model_type") != "k2_horizon" or "auto_map" in cfg:
        sys.exit("this worker only loads k2_horizon checkpoints without auto_map")
    register_k2_horizon()
    t0 = time.perf_counter()
    model, tok = load(a.model, tokenizer_config={"trust_remote_code": False}, model_config={"model_file": None})
    out(event="loaded", load_s=round(time.perf_counter() - t0, 3), mem_after_load_gb=round(mx.get_active_memory() / 1e9, 3))
    sampler = make_sampler(temp=0.0)
    for line in sys.stdin:
        req = json.loads(line)
        ids = tok.apply_chat_template(req["messages"], add_generation_prompt=True, **req.get("template_kwargs", {}))
        mx.reset_peak_memory()
        t_start = time.perf_counter()
        t_first = None
        text, last, n = [], None, 0
        for r in stream_generate(model, tok, ids, max_tokens=req["max_tokens"], sampler=sampler):
            now = time.perf_counter()
            if t_first is None:
                t_first = now
                out(event="first", ttft_s=round(now - t_start, 4), prompt_tokens=r.prompt_tokens, prompt_tps=r.prompt_tps)
            n = r.generation_tokens
            text.append(r.text)
            last = r
            if n % 16 == 0:
                out(event="tick", tokens=n, t=round(now - t_start, 3))
        t_end = time.perf_counter()
        ttft = t_first - t_start
        out(event="done", ttft_s=round(ttft, 4), prompt_tokens=last.prompt_tokens, output_tokens=n,
            prefill_tps=round(last.prompt_tokens / ttft, 2),
            decode_tps=round((n - 1) / (t_end - t_first), 3) if n > 1 else None,
            mlx_prompt_tps=round(last.prompt_tps, 2), mlx_generation_tps=round(last.generation_tps, 3),
            peak_mem_gb=round(mx.get_peak_memory() / 1e9, 3), finish_reason=last.finish_reason,
            wall_s=round(t_end - t_start, 3), text="".join(text),
            chat_template="model's own tokenizer.chat_template via mlx-lm apply_chat_template",
            prompt_ids_len=len(ids))


if __name__ == "__main__":
    main()
