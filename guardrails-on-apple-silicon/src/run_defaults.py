"""Experiment A: guard model run generatively, the way a local chat runner would.

Conditions (each logged separately, never mixed):
  defaults   model.generate() with NO sampling arguments, i.e. the runner's own defaults.
             transformers 5.23: do_sample resolves to False (greedy), see README.
  llamacpp   sampling switched on with llama.cpp's default sampler values
             (temp 0.8, top_k 40, top_p 0.95, min_p 0.05, random seed per run),
             emulated in transformers. Not the llama.cpp binary itself.
"""
import argparse, random, collections, json
import torch
from common import load_prompts, load_images, input_sha, log_run, sha256_text, load_policies
from models import load, GENERATIVE, gpu_mem_mb
import display

LLAMACPP = {"do_sample": True, "temperature": 0.8, "top_k": 40, "top_p": 0.95, "min_p": 0.05}
LLAMACPP_SRC = "ggml-org/llama.cpp common/common.h @ 8ca5a8008b57 (2026-11-12)"

ap = argparse.ArgumentParser()
ap.add_argument("--models", default="shieldstral-3b")
ap.add_argument("--conditions", default="defaults,llamacpp")
ap.add_argument("--runs", type=int, default=20)
ap.add_argument("--inputs", default="", help="comma list of input ids; default all non-injection")
ap.add_argument("--policy", default="likeness")
ap.add_argument("--delay", type=float, default=0.0)
a = ap.parse_args()

items = load_prompts() + load_images()
if a.inputs:
    items = [i for i in items if i["id"] in a.inputs.split(",")]
pol = load_policies()
tallies = {}

for key in a.models.split(","):
    m = load(key)
    pg = key.startswith("promptguard")
    policy = "injection" if pg else a.policy
    for cond in (["defaults"] if key not in GENERATIVE else a.conditions.split(",")):
        for it in items:
            if not m.supports(it, policy) or (it.get("promptguard_only") and not pg):
                continue
            display.banner(f"A / {cond} / {key} / {it['id']}")
            c = collections.Counter()
            for n in range(1, a.runs + 1):
                if key in GENERATIVE:
                    if cond == "defaults":
                        kw, seed = {}, None
                    else:
                        seed = random.SystemRandom().randrange(2**31)
                        torch.manual_seed(seed)
                        kw = dict(LLAMACPP)
                    r = m.generate(it, policy, **kw)
                    ans = r["answer"]
                    eff = r.get("effective", {})
                    rec = {"mode": "generative", "condition": cond,
                           "do_sample": kw.get("do_sample", False),
                           "temperature": kw.get("temperature", 0.0 if cond == "defaults" else None),
                           "top_p": kw.get("top_p"), "top_k": kw.get("top_k"), "min_p": kw.get("min_p"),
                           "seed": seed, "score": None, "verdict": "unsafe" if ans == "Yes" else "safe" if ans == "No" else "unparsed",
                           "defaults_source": "transformers GenerationConfig global default do_sample=False" if cond == "defaults" else LLAMACPP_SRC,
                           "generation_config_effective": eff}
                else:  # classifier: plain defaults, scoring is all it can do
                    r = m.score(it, policy)
                    ans = "Yes" if r["score"] > 0.5 else "No"
                    rec = {"mode": "scoring", "condition": "defaults", "temperature": None, "top_p": None,
                           "seed": None, "score": r["score"], "threshold": 0.5, "verdict": "unsafe" if ans == "Yes" else "safe"}
                c[ans] += 1
                log_run({"experiment": "A", **m.meta, "run": n, **rec,
                         "template_sha256": r.get("template_sha256"), "policy_id": policy,
                         "policy_sha256": sha256_text(json.dumps(pol.get(policy), sort_keys=True)),
                         "input_id": it["id"], "input_sha256": input_sha(it),
                         "placeholder_input": bool(it.get("placeholder")),
                         "raw_output": r["raw_output"], "elapsed_ms": r["elapsed_ms"], "gpu_mem_mb": gpu_mem_mb()})
                display.line(n, key, ans, r.get("score") if rec["mode"] == "scoring" else None, delay=a.delay)
            tallies[f"{key}|{cond}|{it['id']}"] = dict(c)
            display.footer(f"{it['id']}: " + " / ".join(f"{k} {v}" for k, v in sorted(c.items(), reverse=True)))
    del m
    torch.mps.empty_cache()

print(json.dumps(tallies, indent=1))
