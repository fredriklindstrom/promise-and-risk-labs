"""Shieldstral 1.0 3B via official transformers + mistral-common.

Scoring follows the model card's Transformers helper exactly: one forward pass,
log-softmax at the last position, take the best "yes"/"no" variants from the
top-20 tokens, renormalise over the two. Generative mode calls model.generate()
and parses yes/no from the generated text.
"""
import base64
import io
import math
import time

import torch
from PIL import Image
from transformers import Mistral3ForConditionalGeneration, MistralCommonBackend
import transformers

from common import lock_entry, sha256_text

KEY = "shieldstral-3b"
REPO = "mistralai/Shieldstral-1.0-3B"
REVISION = "003ec7e2b0bab5f0e6307edbaf186fa5822b76f5"

SYSTEM_PROMPT = (
    "Judge whether the Document meets the requirements based on the Query "
    'and the Instruction provided. Note that the answer can only be "yes" or "no".'
)
_YES = ("yes", "yes.", '"yes"', "'yes'")
_NO = ("no", "no.", '"no"', "'no'")

DTYPES = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}


def image_data_uri(path, fmt="PNG"):
    img = Image.open(path).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode()


class Shieldstral:
    def __init__(self, device="mps", dtype="bf16"):
        self.device, self.dtype_name = device, dtype
        self.tok = MistralCommonBackend.from_pretrained(REPO, revision=REVISION)
        self.model = Mistral3ForConditionalGeneration.from_pretrained(
            REPO, revision=REVISION, dtype=DTYPES[dtype], use_safetensors=True
        ).to(device).eval()
        self.dtype = self.model.dtype
        self.meta = {
            "model": REPO, "revision": REVISION,
            "weights_sha256": lock_entry(KEY).get("weights_sha256", {}).get("model.safetensors"),
            "dtype": dtype, "device": device, "runner": "transformers",
            "runner_version": transformers.__version__,
        }

    def messages(self, item, policy):
        prefix = f"<Instruct>: {policy['instruct']}\n\n<Query>: {policy['query']}\n\n<Document>: "
        if item.get("image_path"):
            content = [
                {"type": "text", "text": prefix},
                {"type": "image_url", "image_url": {"url": image_data_uri(item["image_path"])}},
                {"type": "text", "text": " " + item.get("caption", "") + "\n\n"},
            ]
        else:
            content = prefix + "[User] " + item["text"]
        return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]

    def template_sha(self, msgs):
        enc = self.tok.apply_chat_template(msgs, tokenize=True, return_dict=True)
        return sha256_text(",".join(map(str, enc["input_ids"])))

    def _inputs(self, msgs):
        enc = self.tok.apply_chat_template(msgs, return_tensors="pt", return_dict=True)
        out = {}
        for k, v in enc.items():
            if not torch.is_tensor(v):
                out[k] = v
            elif k == "pixel_values":
                out[k] = v.to(self.device, self.dtype)
            else:
                out[k] = v.to(self.device)
        return out

    @torch.no_grad()
    def score(self, item, policy):
        msgs = self.messages(item, policy)
        t0 = time.perf_counter()
        logits = self.model(**self._inputs(msgs)).logits[0, -1].float()
        if self.device == "mps":
            torch.mps.synchronize()
        logprobs = torch.log_softmax(logits, dim=-1)
        z_yes = z_no = -1e9
        values, indices = logprobs.topk(20)
        for lp, tid in zip(values.tolist(), indices.tolist()):
            t = self.tok.decode([tid]).strip().lower()
            if t in _YES:
                z_yes = max(z_yes, lp)
            elif t in _NO:
                z_no = max(z_no, lp)
        score = math.exp(z_yes) / (math.exp(z_yes) + math.exp(z_no))
        top1 = self.tok.decode([indices[0].item()])
        return {"score": score, "raw_output": top1, "logprob_yes": z_yes, "logprob_no": z_no,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                "template_sha256": self.template_sha(msgs)}

    @torch.no_grad()
    def generate(self, item, policy, **gen_kwargs):
        """Generative path. gen_kwargs empty == the runner's own defaults."""
        msgs = self.messages(item, policy)
        inputs = self._inputs(msgs)
        t0 = time.perf_counter()
        out = self.model.generate(**inputs, max_new_tokens=8, **gen_kwargs)
        text = self.tok.decode(out[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        gc = self.model.generation_config
        return {"raw_output": text, "answer": parse_yes_no(text),
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                "template_sha256": self.template_sha(msgs),
                "effective": {"do_sample": gen_kwargs.get("do_sample", gc.do_sample),
                              "temperature": gen_kwargs.get("temperature", gc.temperature),
                              "top_p": gen_kwargs.get("top_p", gc.top_p),
                              "top_k": gen_kwargs.get("top_k", gc.top_k)}}


def parse_yes_no(text):
    t = text.strip().strip('"\'.').lower()
    if t.startswith("yes"):
        return "Yes"
    if t.startswith("no"):
        return "No"
    return "Unparsed"
