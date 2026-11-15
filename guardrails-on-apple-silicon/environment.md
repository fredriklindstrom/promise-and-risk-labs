# Environment

| | |
|---|---|
| Machine | Mac Studio (2023, Mac14,13) |
| Chip | Apple M2 Max, 38-core GPU |
| Memory | 96 GB unified; macOS default GPU limit (`iogpu.wired_limit_mb` not raised) |
| OS | macOS 27.2.1 (26B131) |
| Python | 3.12.14; every package pinned in `requirements.txt` |
| Main runner | `transformers` 5.23.0 on `torch` 2.15.0, MPS (CPU for experiment D) |
| Defaults checked in | `transformers` 5.23.0, `mlx-lm` 0.32.2, `mlx-vlm` 0.7.12 (source), llama.cpp `common/common.h` at 8ca5a80, Ollama `api/types.go` at 675150d |
| llama.cpp runs | Homebrew `llama-server` 0.6.0, build 11429 (commit d81235049), Metal |
| Dates | 13–14 November 2026 |

## Models (pinned commits; every weight file's sha256 matches the hash the Hub publishes)

| Repository | Commit | Used for |
|---|---|---|
| `mistralai/Shieldstral-1.0-3B` | `003ec7e2b0bab5f0e6307edbaf186fa5822b76f5` | text and image, free-text policies |
| `google/shieldgemma-2b` | `d1dffc9c8c9237a90aab09c61383791e718ef9e8` | text |
| `google/shieldgemma-9b` | `b8b636016df4540721a098c7aab91c97ec6ee508` | text |
| `google/shieldgemma-2-4b-it` | `eaf60452b5fc41a911338a022e628b0c15283897` | images |
| `meta-llama/Llama-Prompt-Guard-2-86M` | `a8ded8e697ce7c355e395a0df51f94adb4a2fd27` | injection classifier |
| `meta-llama/Llama-Prompt-Guard-2-22M` | `11614a155199674a0a95e6602d6ab0417b790ed0` | injection classifier |

The file hashes are in `models.lock.json`. `src/lock_models.py` downloads each model at its pinned commit and rewrites the lock file from the files on disk, so `git diff models.lock.json` afterwards shows any mismatch. No `trust_remote_code`, no pickle weights, no community conversions.

### How each model is scored

- **Shieldstral:** `Mistral3ForConditionalGeneration` with `MistralCommonBackend`, following the card's helper. Score = softmax over the Yes/No logits.
- **ShieldGemma 2B and 9B:** the tokenizer's own chat template with `guideline=`, then softmax over the Yes/No logits.
- **ShieldGemma 2:** `ShieldGemma2ForImageClassification` with the processor's `custom_policies`. The score is `probabilities[0, 0]`, which is the "Yes" (violates) index. The `transformers` docstring says index 1, and following it inverts every score.
- **Prompt Guard 2:** sequence classifier. Score = softmax index 1, "malicious". `config.json` has no label names, so `transformers` prints `LABEL_1`.

### llama.cpp GGUF

The GGUF was built with llama.cpp's own converter at commit d81235049, following Mistral's card (`--mistral-format`, bf16), from `consolidated.safetensors` at the pinned commit (its sha256 matches the Hub).

| File | sha256 |
|---|---|
| `Shieldstral-1.0-3B-BF16.gguf` | `4f2837f66c0bedcd656b28398bfe37e626be48e33f312e96adb2de7a6335b62f` |
| `mmproj-Shieldstral-Mistral-Format-BF16.gguf` | `05b822a239ad782d7f5c6f674f8d119506f8aca6fa5f6825a21f5f6d9a4ca82d` |

`llama-server` was started as the card shows (`-m`, `--mmproj`, `-c 32768`, `--host 127.0.0.1`), with no sampling flags. The defaults it reported at `/props` are in `results/llamacpp_server_defaults.json`.

## Rerun

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
# accept the ShieldGemma and Prompt Guard licences on Hugging Face, then log in with your own token
cd src
ALL=shieldstral-3b,shieldgemma-2b,shieldgemma-9b,shieldgemma2-4b,promptguard2-86m,promptguard2-22m
../.venv/bin/python lock_models.py && git diff --exit-code ../models.lock.json   # pinned download + hash check
../.venv/bin/python run_pinned.py --models $ALL                                    # B
../.venv/bin/python run_defaults.py --models shieldstral-3b,shieldgemma-2b,shieldgemma-9b   # A (scripts default to Shieldstral only)
../.venv/bin/python run_scope.py --models $ALL                                     # C
../.venv/bin/python run_hw.py --model shieldstral-3b                               # D, one model per call
../.venv/bin/python run_endorsement.py                                             # E
../.venv/bin/python run_llamacpp.py               # A on llama-server; needs the GGUF pair in ../models/
../.venv/bin/python make_summary.py
```

Image inputs go in `inputs/images/` (gitignored). The original cards are not published, so bring your own and your scores will differ. Each script appends to `results/runs.jsonl`; set `LAB_RUNS_FILE` to write somewhere else. `show.py` replays logged runs without loading a model.

On the author's Mac every batch ran through a local GPU job queue, one job at a time with nothing else on the GPU.
