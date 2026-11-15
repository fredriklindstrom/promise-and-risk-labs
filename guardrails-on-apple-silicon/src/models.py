"""One interface over the guard models. load(key) -> object with .meta, .supports,
.score(item, policy_id) and (generative models) .generate(item, policy_id, **kw)."""
import torch
from common import load_policies

AVAILABLE = ["shieldstral-3b", "promptguard2-86m", "promptguard2-22m",
             "shieldgemma-2b", "shieldgemma-9b", "shieldgemma2-4b"]
GENERATIVE = {"shieldstral-3b", "shieldgemma-2b", "shieldgemma-9b"}


class ShieldstralAdapter:
    def __init__(self, device, dtype):
        from score_shieldstral import Shieldstral
        self.m, self.pol = Shieldstral(device, dtype), load_policies()
        self.meta = self.m.meta

    def supports(self, item, policy_id):
        return "shieldstral" in self.pol.get(policy_id, {})

    def score(self, item, policy_id):
        return self.m.score(item, self.pol[policy_id]["shieldstral"])

    def generate(self, item, policy_id, **kw):
        return self.m.generate(item, self.pol[policy_id]["shieldstral"], **kw)


def load(key, device="mps", dtype=None):
    if key == "shieldstral-3b":
        return ShieldstralAdapter(device, dtype or "bf16")
    if key.startswith("promptguard2"):
        from score_promptguard import PromptGuard
        return PromptGuard(key, device, dtype or "fp32")
    if key in ("shieldgemma-2b", "shieldgemma-9b"):
        from score_shieldgemma import ShieldGemmaText
        return ShieldGemmaText(key, device, dtype or "bf16", load_policies())
    if key == "shieldgemma2-4b":
        from score_shieldgemma import ShieldGemmaImage
        return ShieldGemmaImage(device, dtype or "bf16", load_policies())
    raise KeyError(key)


def gpu_mem_mb():
    try:
        return round(torch.mps.driver_allocated_memory() / 2**20, 1)
    except Exception:
        return None


def policy_ids_for(key):
    return ["injection"] if key.startswith("promptguard2") else ["likeness", "false_claim"]
