"""Write models.lock.json: repo, pinned revision, sha256 of each weight file."""
import json, sys
from pathlib import Path
from huggingface_hub import snapshot_download
from common import LOCK, sha256_file

MODELS = {
    "shieldstral-3b": ("mistralai/Shieldstral-1.0-3B", "003ec7e2b0bab5f0e6307edbaf186fa5822b76f5", ["consolidated.safetensors"]),
    "promptguard2-86m": ("meta-llama/Llama-Prompt-Guard-2-86M", "a8ded8e697ce7c355e395a0df51f94adb4a2fd27", []),
    "promptguard2-22m": ("meta-llama/Llama-Prompt-Guard-2-22M", "11614a155199674a0a95e6602d6ab0417b790ed0", []),
    "shieldgemma-2b": ("google/shieldgemma-2b", "d1dffc9c8c9237a90aab09c61383791e718ef9e8", []),
    "shieldgemma-9b": ("google/shieldgemma-9b", "b8b636016df4540721a098c7aab91c97ec6ee508", []),
    "shieldgemma2-4b": ("google/shieldgemma-2-4b-it", "eaf60452b5fc41a911338a022e628b0c15283897", []),
}

lock = json.loads(LOCK.read_text()) if LOCK.exists() else {}
for key in (sys.argv[1:] or MODELS):
    repo, rev, skip = MODELS[key]
    try:
        p = Path(snapshot_download(repo, revision=rev, ignore_patterns=skip, local_files_only="--offline" in sys.argv))
    except Exception as e:
        lock[key] = {"repo": repo, "revision": rev, "status": f"not downloaded: {type(e).__name__}"}
        print(key, "FAILED", type(e).__name__, str(e)[:200]); continue
    w = {f.name: sha256_file(f) for f in sorted(p.iterdir()) if f.suffix == ".safetensors"}
    lock[key] = {"repo": repo, "revision": rev, "status": "ok", "weights_sha256": w,
                 "excluded": skip, "default_dtype": "bf16", "default_device": "mps"}
    print(key, "ok", {k: v[:12] for k, v in w.items()})
LOCK.write_text(json.dumps(lock, indent=2) + "\n")
