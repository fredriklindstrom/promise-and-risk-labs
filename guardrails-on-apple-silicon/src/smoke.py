import time, sys
from common import load_prompts, load_policies, peak_rss_mb
from score_shieldstral import Shieldstral
t=time.perf_counter(); m=Shieldstral(); print("load_s", round(time.perf_counter()-t,1))
pol=load_policies()
for it in load_prompts():
    if it.get("promptguard_only"): continue
    for pid in ("likeness","false_claim"):
        r=m.score(it, pol[pid]["shieldstral"])
        print(f'{it["id"]:22s} {pid:12s} score={r["score"]:.6f} top1={r["raw_output"]!r} {r["elapsed_ms"]}ms')
g=m.generate(load_prompts()[0], pol["likeness"]["shieldstral"]); print("generate defaults:", g)
print("peak_rss_mb", peak_rss_mb())
