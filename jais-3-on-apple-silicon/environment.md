# Environment

| | |
|---|---|
| Machine | Mac Studio (2023, Mac14,13) |
| Chip | Apple M2 Max, 38-core GPU |
| Memory | 96 GB unified; GPU limit 83.49 GB (macOS default `iogpu.wired_limit_mb`, not raised) |
| OS | macOS 27.0.1 (26A434) |
| MLX | mlx 0.32.2, mlx-lm 0.31.3 (speed runs and ArabicMMLU) |
| llama.cpp | Homebrew 0.6.0, build 11429 (commit d81235049) |
| Tokenizers | `tokenizers` from the same environment, reading each model's `tokenizer.json` |
| Dates | 17–18 October 2026 |
| Load condition | Ordinary desktop apps open (not a dedicated AI node), no other model running |

## Models (pinned commits, every file hash-verified against the Hub before loading)

| Repository | Commit | On disk |
|---|---|---:|
| `IFM/Jais-3-32B-MLX-4bit` | `98ca8c570eebfa8d863439cb921279d7a10364f9` | 25.5 GB |
| `IFM/Jais-3-MoVA-36B-A4B-MLX-4bit` | `03a84009d9e04f33f84d64b22194eae9d900fcbe` | 26.4 GB |
| `IFM/Jais-3-MoVA-36B-A4B-GGUF`, `Jais-3-MoVA-36B-A4B-Q4_K_M.gguf` | `8b2c5ab2feffeeca416c99ec972e543db6a97dbe` | 22.4 GB |
| `lmstudio-community/Qwen3.8-27B-MLX-4bit` (ArabicMMLU control, tokenizer) | `6067b15cf581666a4aecf6af3afaba4bb5efc20c` | |
| `mlx-community/Llama-3.3-70B-Instruct-4bit` (tokenizer) | `de2dfaf56839b7d0e834157d2401dee02726874d` | |
| `openai/gpt-oss-120b` (tokenizer) | as in the local cache | |

## IFM's model code (`k2_horizon.py`)

mlx-lm 0.31 has no `k2_horizon` architecture, and IFM's MLX repositories ship one as `k2_horizon.py`, loaded via `trust_remote_code`. It is not copied here (it is IFM's file). To reproduce:

1. Download `k2_horizon.py` from either MLX repository above, at the pinned commit.
2. Check `sha256` = `80b501f2ea7364842bd25659928d3af7692238e7c6c2a50bad2f35f61f5483bc` (identical in both repositories on 17 October 2026).
3. Read it. It is 628 lines of MLX model code, imports only `math`, `dataclasses`, `typing`, `mlx` and `mlx_lm`, and does no file or network access.
4. Save it as `scripts/vendor/k2_horizon.py`. `jais_worker.py` refuses to run if the hash differs, registers it as `mlx_lm.models.k2_horizon`, and loads the model with `model_file=None`, so the repository's own copy is never imported.

## Evaluation inputs

| | |
|---|---|
| ArabicMMLU items | `IFM/ArabicMMLU_verified` @ `50b3f62a27156085a4d18bb61c0204a9b10c8496`, `All/test.csv` (13,690 items), CC BY-NC 4.0 |
| Task code | `MBZUAI-IFM/jais3-lm-evaluation-harness` @ `8c2ad073cf4aca5bf4f9f5b4728dd9abe4943eaf`, `lm_eval/tasks/arabicmmlu_gen/utils.py` |
| Tokenizer text | UDHR in Unicode project (`eric-muller/udhr`), `udhr_arb.xml` and `udhr_eng.xml` |
