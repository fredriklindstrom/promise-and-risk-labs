"""100-item slice of ArabicMMLU (IFM's audited set) on this Mac, scored with IFM's own task code.

What is IFM's and what is mine:
- Items: IFM/ArabicMMLU_verified (the audited set IFM reports on), All/test.csv, pinned revision.
  100 items drawn with random.Random(17), stratified by nothing (a plain random sample).
- Prompt + scoring: IFM's harness fork (MBZUAI-IFM/jais3-lm-evaluation-harness @ 8c2ad07),
  lm_eval/tasks/arabicmmlu_gen/utils.py: doc_to_text (English instruction, Arabic question,
  answer as [[X]]), extract_bracketed_label (last [[...]] wins), exact_match_normalized_label.
  The task yaml's 0-shot generate_until setup is kept.
- Generation (mine): mlx-lm BatchGenerator on Apple silicon instead of lm-eval's client against a
  vLLM/SGLang server. Settings from IFM's model card "Best practices": reasoning_effort=high,
  thinking-mode Arabic sampling temperature 0.7, top_p 0.95, top_k 20. The thinking block is removed
  before scoring, as vLLM's k2_horizon reasoning parser does (answer = text after the last
  </ifm|think>). An answer cut off at max_tokens scores [invalid], as it would in the harness.

    cd ~/p-and-r/nov17-bench && .venv/bin/python -I ~/p-and-r/jais3-bench/eval/run_eval.py --model jais3-32b
Resumable: items already in the output file are skipped.
"""
import argparse, csv, glob, importlib.util, json, math, os, random, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
J3 = HERE.parent
HUB = Path.home() / ".cache/huggingface/hub"
UTILS = J3 / "ext/jais3-lm-evaluation-harness/lm_eval/tasks/arabicmmlu_gen/utils.py"
DATA = HERE / "data/All/test.csv"

MODELS = {
    "jais3-32b": {"repo": "IFM/Jais-3-32B-MLX-4bit", "rev": "98ca8c570eebfa8d863439cb921279d7a10364f9",
                  "k2": True, "tk": {"reasoning_effort": "high"}, "think_end": "</ifm|think>", "ifm_reported": 88.6},
    "jais3-36b-a4b": {"repo": "IFM/Jais-3-MoVA-36B-A4B-MLX-4bit", "rev": "03a84009d9e04f33f84d64b22194eae9d900fcbe",
                      "k2": True, "tk": {"reasoning_effort": "high"}, "think_end": "</ifm|think>", "ifm_reported": 87.7},
    # Control: a model IFM also reports (85.8 on the same benchmark), run the same way, thinking on.
    "qwen38-27b": {"repo": "lmstudio-community/Qwen3.8-27B-MLX-4bit", "rev": None,
                   "k2": False, "tk": {"enable_thinking": True}, "think_end": "</think>", "ifm_reported": 85.8},
}


def load_utils():
    spec = importlib.util.spec_from_file_location("ifm_arabicmmlu_gen_utils", UTILS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sample(n, seed):
    rows = list(csv.DictReader(open(DATA, newline="", encoding="utf-8")))
    rng = random.Random(seed)
    return rng.sample(rows, n), len(rows)


def wilson(k, n, z=1.96):
    if n == 0:
        return (0, 0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


def snapshot(repo, rev):
    base = HUB / ("models--" + repo.replace("/", "--")) / "snapshots"
    hits = sorted(base.glob((rev or "") + "*"), key=lambda d: d.stat().st_mtime)
    if not hits:
        sys.exit(f"{repo} not in the HF cache")
    return hits[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=list(MODELS))
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=16384)
    ap.add_argument("--cache-limit-gb", type=float, default=None,
                    help="cap MLX's freed-buffer cache (added 2026-10-17 after batch-8 Metal OOMs on the Jais models)")
    ap.add_argument("--report", action="store_true", help="score the output file only")
    ap.add_argument("--chunk", type=int, default=None,
                    help="run at most this many of the remaining items, then exit (fresh process per chunk; added after MLX buffer build-up crashes)")
    a = ap.parse_args()
    cfg = MODELS[a.model]
    out = HERE / f"results_{a.model}.jsonl"
    U = load_utils()
    items, total = sample(a.n, a.seed)

    done = {}
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            done[r["id"]] = r
    if a.report:
        report(a, cfg, items, done, total)
        return

    import mlx.core as mx
    from mlx_lm import load
    from mlx_lm.generate import BatchGenerator
    from mlx_lm.sample_utils import make_sampler
    sys.path.insert(0, str(J3))
    path = snapshot(cfg["repo"], cfg["rev"])
    if cfg["k2"]:
        from jais_worker import register_k2_horizon
        register_k2_horizon()
    model, tok = load(str(path), tokenizer_config={"trust_remote_code": False}, model_config={"model_file": None})
    mx.random.seed(a.seed)
    if a.cache_limit_gb:
        mx.set_cache_limit(int(a.cache_limit_gb * 1e9))

    todo = [d for d in items if d["ID"] not in done]
    if a.chunk:
        todo = todo[:a.chunk]
    print(f"{a.model}: {len(items)} items sampled from {total}; {len(done)} done, {len(todo)} to run", flush=True)
    prompts = []
    for d in todo:
        msgs = [{"role": "user", "content": U.doc_to_text(d)}]
        prompts.append(tok.apply_chat_template(msgs, add_generation_prompt=True, **cfg["tk"]))
    gen = BatchGenerator(model, stop_tokens=[[t] for t in tok.eos_token_ids],
                         sampler=make_sampler(temp=0.7, top_p=0.95, top_k=20),
                         completion_batch_size=a.batch, prefill_batch_size=a.batch)
    uids = gen.insert(prompts, [a.max_tokens] * len(prompts))
    by_uid = {u: (d, []) for u, d in zip(uids, todo)}
    t0 = time.time()
    fin = 0
    gen_tokens = 0
    with open(out, "a") as f:
        while responses := gen.next_generated():
            for r in responses:
                d, toks = by_uid[r.uid]
                if r.finish_reason != "stop":
                    toks.append(r.token)
                    gen_tokens += 1
                if r.finish_reason is None:
                    continue
                text = tok.decode(toks)
                answer = text.split(cfg["think_end"])[-1] if cfg["think_end"] in text else ("" if r.finish_reason == "length" else text)
                pred = list(U.extract_bracketed_label([[answer]], [d]))[0]
                score = U.exact_match_normalized_label([pred], [d["Answer Key"]])["exact_match"]
                rec = {"id": d["ID"], "subject": d["Subject"], "group": d["Group"], "level": d["Level"], "gold": d["Answer Key"],
                       "pred": pred, "correct": score, "finish": r.finish_reason, "out_tokens": len(toks),
                       "prompt_tokens": len(prompts[todo.index(d)]), "answer": answer.strip()[-400:], "text": text}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
                fin += 1
                el = time.time() - t0
                print(f"[{fin}/{len(todo)}] id {d['ID']:>6} gold {d['Answer Key']} pred {pred:<9} {'OK ' if score else 'MISS'} "
                      f"{len(toks):>5} tok ({r.finish_reason}) · {gen_tokens / el:.1f} tok/s aggregate · {el / 60:.1f} min", flush=True)
    gen.close()
    done = {json.loads(l)["id"]: json.loads(l) for l in out.read_text().splitlines()}
    report(a, cfg, items, done, total)


def report(a, cfg, items, done, total):
    rows = [done[d["ID"]] for d in items if d["ID"] in done]
    k = sum(r["correct"] for r in rows)
    n = len(rows)
    lo, hi = wilson(k, n)
    inval = sum(1 for r in rows if r["pred"] == "[invalid]")
    cut = sum(1 for r in rows if r["finish"] == "length")
    toks = sorted(r["out_tokens"] for r in rows)
    summ = {"model": a.model, "repo": cfg["repo"], "n": n, "sampled_from": total, "seed": a.seed, "correct": k,
            "accuracy_pct": round(100 * k / n, 1) if n else None, "wilson95_pct": [round(lo, 1), round(hi, 1)],
            "ifm_reported_pct": cfg["ifm_reported"], "invalid": inval, "cut_at_max_tokens": cut,
            "out_tokens_median": toks[len(toks) // 2] if toks else None, "out_tokens_max": toks[-1] if toks else None}
    print(json.dumps(summ, indent=1))
    (HERE / f"summary_{a.model}.json").write_text(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main()
