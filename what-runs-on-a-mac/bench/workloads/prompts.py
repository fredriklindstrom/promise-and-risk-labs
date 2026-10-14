"""Identical workloads across runners.

Context length N means: the prompt is built so prompt tokens + 512 output tokens fit in N
(prompt target = N - max_output - 64 tokens of template slack), trimmed with the model's own
tokenizer. The runner-reported prompt token count is what goes in the results.
A fresh nonce on the first line of every prompt defeats prefix/prompt caching between reps.
"""
import uuid
from .. import ROOT

CORPUS = ROOT / "inputs/context_corpus.txt"
SPEED_HEAD = ("Read the following excerpt from a novel. Afterwards, write a detailed essay of at least 800 words "
              "on its characters, setting and themes.\n\n<excerpt>\n")
SPEED_TAIL = "\n</excerpt>\n\nWrite the essay now, in full paragraphs."
NEEDLE_HEAD = "Read the following document carefully.\n\n<document>\n"
NEEDLE_TAIL = ("\n</document>\n\nWhat is the archive password mentioned in the document? "
               "Answer with the password only.")
SLACK = 64


def corpus():
    return CORPUS.read_text()


def nonce():
    return f"Session {uuid.uuid4().hex[:12]}."


def _messages(nonce_line, head, body, tail):
    return [{"role": "user", "content": f"{nonce_line}\n\n{head}{body}{tail}"}]


def build(tok, ctx, max_out, kind="speed", depth=None, needle=None, template_kwargs=None):
    """tok: object with encode(str)->list[int], decode(list)->str, prompt_len(messages, template_kwargs)->int.
    Returns (messages, meta)."""
    head, tail = (SPEED_HEAD, SPEED_TAIL) if kind == "speed" else (NEEDLE_HEAD, NEEDLE_TAIL)
    n = nonce()
    target = ctx - max_out - SLACK
    overhead = tok.prompt_len(_messages(n, head, "", tail), template_kwargs)
    if needle:
        overhead += len(tok.encode(" " + needle + " "))
    budget = max(target - overhead, 16)
    ids = tok.encode(corpus()[: budget * 8])      # ~4 chars/token, 2x margin
    body = tok.decode(ids[:budget])
    if needle:
        pos = int(len(body) * depth / 100)
        if 0 < depth < 100:                         # snap to the next sentence end
            j = body.find(". ", pos)
            pos = j + 2 if j != -1 else pos
        body = body[:pos] + (" " if pos else "") + needle + " " + body[pos:]
    msgs = _messages(n, head, body, tail)
    return msgs, {"nonce": n, "prompt_tokens_planned": tok.prompt_len(msgs, template_kwargs), "target": target}


class HFTok:
    """transformers tokenizer from a local snapshot (no remote code)."""

    def __init__(self, path):
        from transformers import AutoTokenizer
        self.t = AutoTokenizer.from_pretrained(str(path), trust_remote_code=False, local_files_only=True)
        self.source = f"HF tokenizer + chat template from {path}"

    def encode(self, s):
        return self.t.encode(s, add_special_tokens=False)

    def decode(self, ids):
        return self.t.decode(ids)

    def prompt_len(self, messages, template_kwargs=None):
        return len(self.t.apply_chat_template(messages, add_generation_prompt=True, tokenize=True, **(template_kwargs or {})))


def score_needle(text, answer):
    """Exact-match: the answer string appears in the final output (after any reasoning block)."""
    final = text.split("</think>")[-1]
    if "<|channel|>final<|message|>" in final:
        final = final.split("<|channel|>final<|message|>")[-1]
    return answer in final
