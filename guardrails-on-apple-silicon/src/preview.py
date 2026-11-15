"""One unlogged scoring pass for a quick look; the logged runs are run_*.py."""
import torch
from common import load_prompts, load_images
from models import load
texts = [p for p in load_prompts() if p["id"] in ("real_driver", "real_driver_variant", "fictional_driver", "benign")]
imgs = load_images()
for key in ["shieldstral-3b", "shieldgemma2-4b", "shieldgemma-2b", "shieldgemma-9b", "promptguard2-86m"]:
    m = load(key)
    pids = ["injection"] if key.startswith("promptguard") else ["likeness", "false_claim"]
    for it in texts + imgs:
        for pid in pids:
            if m.supports(it, pid):
                print(f'{key:16s} {it["id"]:22s} {pid:12s} {m.score(it, pid)["score"]:.4f}', flush=True)
    del m; torch.mps.empty_cache()
