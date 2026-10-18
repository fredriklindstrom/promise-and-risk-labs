"""Token counts for the same text under several tokenizers (local tokenizer.json files, no remote code)."""
import glob, json, os, re, sys, unicodedata
import xml.etree.ElementTree as ET
from tokenizers import Tokenizer

HUB = os.path.expanduser("~/.cache/huggingface/hub")
def tok(repo):
    p = sorted(glob.glob(f"{HUB}/models--{repo.replace('/', '--')}/snapshots/*/tokenizer.json"))
    return p[-1] if p else None

TOKS = {
    "Jais 3 (32B and 36B-A4B)": tok("IFM/Jais-3-MoVA-36B-A4B-MLX-4bit"),
    "Qwen 3.8 27B": tok("lmstudio-community/Qwen3.8-27B-MLX-4bit") or tok("Qwen/Qwen3.8-27B"),
    "Llama 3.3 70B": tok("mlx-community/Llama-3.3-70B-Instruct-4bit"),
    "gpt-oss-120b": tok("openai/gpt-oss-120b"),
}
NS = {"u": "http://efele.net/udhr"}
def paras(path):
    root = ET.parse(path).getroot()
    return [unicodedata.normalize("NFC", "".join(p.itertext()).strip()) for p in root.iter("{http://efele.net/udhr}para")]
def article1(path):
    root = ET.parse(path).getroot()
    a = next(a for a in root.iter("{http://efele.net/udhr}article") if a.get("number") == "1")
    return unicodedata.normalize("NFC", " ".join(p.strip() for p in a.itertext() if p.strip()).split(" ", 2)[-1] if False else
                                 " ".join("".join(p.itertext()).strip() for p in a.iter("{http://efele.net/udhr}para")))
docs = {}
for lang in ("arb", "eng"):
    ps = paras(f"data/udhr_{lang}.xml")
    docs[lang] = {"article 1": article1(f"data/udhr_{lang}.xml"), "full text": "\n".join(ps)}
out = {"tokenizers": {k: v for k, v in TOKS.items()}, "texts": {l: {k: {"chars": len(t), "words": len(t.split())} for k, t in d.items()} for l, d in docs.items()}, "counts": {}}
for name, path in TOKS.items():
    if not path:
        print("missing tokenizer:", name); continue
    t = Tokenizer.from_file(path)
    out["counts"][name] = {f"{l} {k}": len(t.encode(txt, add_special_tokens=False).ids) for l, d in docs.items() for k, txt in d.items()}
    out["counts"][name]["vocab"] = t.get_vocab_size()
print("Article 1 (Arabic):", docs["arb"]["article 1"])
print("Article 1 (English):", docs["eng"]["article 1"])
print(json.dumps(out, indent=1, ensure_ascii=False))
hdr = f"{'tokenizer':28} {'vocab':>8} {'ar art.1':>9} {'ar full':>8} {'en full':>8} {'ar/en':>6} {'ar chars/tok':>12} {'vs Jais':>8}"
print(hdr)
j = out["counts"].get("Jais 3 (32B and 36B-A4B)", {}).get("arb full text")
for name, c in out["counts"].items():
    ar, en = c["arb full text"], c["eng full text"]
    print(f"{name:28} {c['vocab']:>8} {c['arb article 1']:>9} {ar:>8} {en:>8} {ar/en:>6.2f} {out['texts']['arb']['full text']['chars']/ar:>12.2f} {ar/j:>7.2f}x")
json.dump(out, open("results.json", "w"), indent=1, ensure_ascii=False)
