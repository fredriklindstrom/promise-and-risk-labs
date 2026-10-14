"""MLX worker process: one model per process (clean peak-memory counter, OOM kills only this process).
Protocol: JSON lines. stdout: {"event":"loaded",...}, then per request token ticks and a "done" record.
stdin: {"messages":[...], "max_tokens":N, "template_kwargs":{...}}"""
import argparse, json, sys, time
import mlx.core as mx
from mlx_lm import load, stream_generate
from mlx_lm.sample_utils import make_sampler


def out(**k):
    sys.stdout.write(json.dumps(k) + "\n")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--seed", type=int, default=17)
    a = ap.parse_args()
    mx.random.seed(a.seed)
    # Security: mlx-lm imports a repo-supplied module if config.json names one ("model_file"); transformers-style
    # "auto_map" does the same for tokenizers. Refuse such repos outright, and disable model_file on top.
    import os
    cfg = json.load(open(os.path.join(a.model, "config.json")))
    nested = [cfg] + [v for v in cfg.values() if isinstance(v, dict)]
    if any(("model_file" in c and c["model_file"]) or "auto_map" in c for c in nested):
        sys.exit("refusing to load: config.json asks to import repository code (model_file/auto_map)")
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
