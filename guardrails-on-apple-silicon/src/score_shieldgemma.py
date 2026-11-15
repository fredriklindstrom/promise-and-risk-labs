"""ShieldGemma (text, 2B/9B) and ShieldGemma 2 (image, 4B), per their model cards.
Text: the tokenizer's own chat template with guideline= (card: "Usage with chat templates"),
score = softmax over the Yes/No logits at the last position.
Image: ShieldGemma2ForImageClassification + processor custom_policies (card + transformers source)."""
import json
import time
import torch
import transformers
from transformers import AutoTokenizer, AutoModelForCausalLM
from common import lock_entry, sha256_text

TEXT = {
    "shieldgemma-2b": ("google/shieldgemma-2b", "d1dffc9c8c9237a90aab09c61383791e718ef9e8"),
    "shieldgemma-9b": ("google/shieldgemma-9b", "b8b636016df4540721a098c7aab91c97ec6ee508"),
}
IMAGE = ("google/shieldgemma-2-4b-it", "eaf60452b5fc41a911338a022e628b0c15283897")

DT = {"fp32": torch.float32, "fp16": torch.float16, "bf16": torch.bfloat16}


class ShieldGemmaText:
    def __init__(self, key="shieldgemma-2b", device="mps", dtype="bf16", policies=None):
        repo, rev = TEXT[key]
        self.device, self.pol = device, policies
        self.tok = AutoTokenizer.from_pretrained(repo, revision=rev)
        self.model = AutoModelForCausalLM.from_pretrained(repo, revision=rev, dtype=DT[dtype], use_safetensors=True).to(device).eval()
        v = self.tok.get_vocab()
        self.ids = [v["Yes"], v["No"]]
        self.meta = {"model": repo, "revision": rev, "dtype": dtype, "device": device,
                     "weights_sha256": json.dumps(lock_entry(key).get("weights_sha256"), sort_keys=True),
                     "runner": "transformers", "runner_version": transformers.__version__}

    def supports(self, item, policy_id):
        return not item.get("image_path") and "shieldgemma" in self.pol.get(policy_id, {})

    def _enc(self, item, policy_id):
        chat = [{"role": "user", "content": item["text"]}]
        enc = self.tok.apply_chat_template(chat, guideline=self.pol[policy_id]["shieldgemma"]["guideline"],
                                           return_tensors="pt", return_dict=True)
        return enc.to(self.device), sha256_text(",".join(map(str, enc["input_ids"][0].tolist())))

    @torch.no_grad()
    def score(self, item, policy_id):
        t0 = time.perf_counter()
        enc, th = self._enc(item, policy_id)
        logits = self.model(**enc).logits[0, -1, self.ids].float()
        s = torch.softmax(logits, dim=0)[0].item()
        return {"score": s, "raw_output": "Yes" if s > 0.5 else "No",
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1), "template_sha256": th}

    @torch.no_grad()
    def generate(self, item, policy_id, **kw):
        from score_shieldstral import parse_yes_no
        enc, th = self._enc(item, policy_id)
        t0 = time.perf_counter()
        out = self.model.generate(**enc, max_new_tokens=8, **kw)
        text = self.tok.decode(out[0, enc["input_ids"].shape[1]:], skip_special_tokens=True)
        gc = self.model.generation_config
        return {"raw_output": text, "answer": parse_yes_no(text), "template_sha256": th,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                "effective": {k: kw.get(k, getattr(gc, k)) for k in ("do_sample", "temperature", "top_p", "top_k")}}


class ShieldGemmaImage:
    """ShieldGemma 2: image + policy. Custom likeness policy is outside its three trained
    categories (sexual, dangerous, violent); say so on air."""

    def __init__(self, device="mps", dtype="bf16", policies=None):
        from transformers import AutoProcessor, ShieldGemma2ForImageClassification
        repo, rev = IMAGE
        self.device, self.pol = device, policies
        self.proc = AutoProcessor.from_pretrained(repo, revision=rev)
        self.model = ShieldGemma2ForImageClassification.from_pretrained(repo, revision=rev, dtype=DT[dtype], use_safetensors=True).to(device).eval()
        self.meta = {"model": repo, "revision": rev, "dtype": dtype, "device": device,
                     "weights_sha256": json.dumps(lock_entry("shieldgemma2-4b").get("weights_sha256"), sort_keys=True),
                     "runner": "transformers", "runner_version": transformers.__version__}

    def supports(self, item, policy_id):
        return bool(item.get("image_path")) and "shieldgemma2" in self.pol.get(policy_id, {})

    @torch.no_grad()
    def score(self, item, policy_id):
        from PIL import Image
        t0 = time.perf_counter()
        img = Image.open(item["image_path"]).convert("RGB")
        g = self.pol[policy_id]["shieldgemma2"]["guideline"]
        inputs = self.proc(images=[img], custom_policies={policy_id: g}, policies=[policy_id],
                           return_tensors="pt").to(self.device)
        probs = self.model(**inputs).probabilities.float()
        s = probs[0, 0].item()  # [Yes, No] per the card
        return {"score": s, "raw_output": "Yes" if s > 0.5 else "No",
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1), "template_sha256": sha256_text(g)}
