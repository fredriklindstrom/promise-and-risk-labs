"""Prompt Guard 2 (86M / 22M), through the standard transformers text-classification
interface. The repositories are gated: accept the Llama 4 Community License on Hugging Face
first. config.json has no label names, so index 1 is read as "malicious" (checked against the card)."""
import time
import torch
import transformers
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from common import lock_entry

VARIANTS = {
    "promptguard2-86m": ("meta-llama/Llama-Prompt-Guard-2-86M", "a8ded8e697ce7c355e395a0df51f94adb4a2fd27"),
    "promptguard2-22m": ("meta-llama/Llama-Prompt-Guard-2-22M", "11614a155199674a0a95e6602d6ab0417b790ed0"),
}


class PromptGuard:
    policies = ("injection",)  # fixed: benign vs malicious

    def __init__(self, key="promptguard2-86m", device="mps", dtype="fp32"):
        repo, rev = VARIANTS[key]
        self.key, self.device = key, device
        dt = {"fp32": torch.float32, "fp16": torch.float16, "bf16": torch.bfloat16}[dtype]
        self.tok = AutoTokenizer.from_pretrained(repo, revision=rev)
        self.model = AutoModelForSequenceClassification.from_pretrained(repo, revision=rev, dtype=dt, use_safetensors=True).to(device).eval()
        self.labels = self.model.config.id2label
        self.meta = {"model": repo, "revision": rev, "dtype": dtype, "device": device,
                     "weights_sha256": lock_entry(key).get("weights_sha256", {}).get("model.safetensors"),
                     "runner": "transformers", "runner_version": transformers.__version__}

    def supports(self, item, policy_id):
        return policy_id == "injection" and not item.get("image_path")

    @torch.no_grad()
    def score(self, item, policy_id="injection"):
        t0 = time.perf_counter()
        enc = self.tok(item["text"], return_tensors="pt", truncation=True, max_length=512).to(self.device)
        probs = torch.softmax(self.model(**enc).logits[0].float(), dim=-1)
        # Label 1 is the malicious class on Prompt Guard 2; confirm with id2label.
        return {"score": probs[1].item(), "raw_output": self.labels.get(int(probs.argmax()), str(int(probs.argmax()))),
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1), "template_sha256": None}
