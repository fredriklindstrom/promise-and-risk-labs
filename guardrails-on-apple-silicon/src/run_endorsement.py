"""Endorsement test: can a custom-policy guard flag a card that carries someone's own CTA/branding
next to a real person's likeness? Shieldstral only (the one model here that takes free-text policies).
Pinned scoring, logged as experiment E. chatgpt_antonelli_3 is the card with Fredrik's footer."""
import json
import torch
from common import load_prompts, load_images, input_sha, log_run, sha256_text, load_policies, verdict
from models import load
torch.manual_seed(1234)
pol = load_policies()
items = load_images() + [p for p in load_prompts() if p["id"] in ("real_driver", "fictional_driver", "benign")]
m = load("shieldstral-3b")
for pid in ("endorsement", "unrelated_cta"):
    for it in items:
        r = m.score(it, pid); s = r["score"]
        log_run({"experiment": "E", **m.meta, "mode": "scoring", "temperature": 0.0, "top_p": None, "seed": 1234,
                 "template_sha256": r["template_sha256"], "policy_id": pid,
                 "policy_sha256": sha256_text(json.dumps(pol[pid], sort_keys=True)),
                 "input_id": it["id"], "input_sha256": input_sha(it), "score": s, "threshold": 0.5,
                 "verdict": verdict(s, 0.5), "verdicts": {t: verdict(s, float(t)) for t in ("0.3", "0.5", "0.7")},
                 "has_fredrik_cta": it["id"] == "img_chatgpt_antonelli_3",
                 "raw_output": r["raw_output"], "elapsed_ms": r["elapsed_ms"]})
        flag = "   <-- card with Fredrik's footer" if it["id"] == "img_chatgpt_antonelli_3" else ""
        print(f"{pid:14s} {it['id']:26s} {s:.4f}  {verdict(s, 0.5)}{flag}", flush=True)
