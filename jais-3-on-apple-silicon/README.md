# Jais 3 on Apple silicon

IFM (the Institute of Foundation Models at MBZUAI, Abu Dhabi) released Jais 3 on 15 October 2026: six open Arabic-centric models under Apache 2.0. This lab runs two of them on a Mac Studio, the dense **Jais 3 32B** and the **Jais 3 MoVA 36B-A4B** mixture of experts (36 billion parameters, about four billion active per token), and asks three questions: do they fit, what does running them cost, and does IFM's Arabic accuracy claim hold?

Measured for [Promise & Risk of AI](https://promiseandrisk.ai/labs/jais-3-on-apple-silicon/) ([video](https://youtu.be/Nmy_y4TyLco)). In this folder: the lab-specific scripts, every timed run, every ArabicMMLU answer with the model's full output, the token counts, and the llama.cpp failure log.

## Result

Mac Studio, Apple M2 Max (38-core GPU), 96 GB, IFM's 4-bit MLX builds, mlx-lm 0.31.3.

| | Jais 3 32B | Jais 3 36B-A4B |
|---|---:|---:|
| File on disk | 25.5 GB | 26.4 GB |
| Peak memory at 128K tokens | 60.9 GB | 53.3 GB |
| Writing speed at 1K / 128K | 13.6 / 4.1 tok/s | 40.5 / 8.2 tok/s |
| First word after, at 128K | **59.6 min** | **18.7 min** |
| Energy per 128K run | 52.9 Wh | 16.5 Wh |
| ArabicMMLU, 100 random items | **86** (IFM: 88.6) | **85** (IFM: 87.7) |

**Both fit at every length up to 128K** inside the 83.5 GB the GPU gets by default. File size beats the napkin: IFM keeps attention and embeddings at 8 bits, so the files are 9.5 and 8.4 GB larger than parameters × four bits.

**IFM's ArabicMMLU numbers hold within the noise.** Scored with IFM's own task code on its audited release, both Jais 3 models and the Qwen 3.8 27B control (80, IFM 85.8) land with IFM's figure inside the 95% interval, in IFM's order. 100 items cannot separate Jais 3 from Qwen 3.8.

**IFM's 83.1 Arabic average cannot be reproduced.** It averages about 28 benchmarks, some scored by an LLM judge and some unreleased, with unpublished weighting; IFM's own published components average 82.0–83.8 depending on grouping.

**Arabic is cheap in Jais 3's tokenizer.** The Arabic UDHR is 1,611 tokens on Jais 3, 2,192 on Qwen 3.8 (1.36×) and 2,721 on Llama 3.3 (1.69×).

Every table, the method and the caveats: [results.md](results.md).

## What failed

- **llama.cpp:** IFM's GGUF fails in Homebrew llama.cpp 0.6.0 (b11429) with `unknown model architecture: 'k2-horizon'` ([log](logs/llamacpp-gguf-load-failure.log)).
- **mlx-lm without IFM's code:** there is no `k2_horizon` architecture in mlx-lm 0.31. The reviewed, hash-pinned copy of IFM's file is how these runs loaded the model ([environment.md](environment.md)).
- **ArabicMMLU at batch 8:** Metal ran out of memory on long reasoning chains. The slices were finished in fresh processes of 12 items at batch 4; no scored item was rerun.

## What is here

| Path | What |
|---|---|
| `scripts/jais_worker.py` | MLX worker: registers the reviewed `k2_horizon.py` by hash, loads with `model_file=None` |
| `scripts/run_speed.py` | Speed and memory cells (driven through the Nov 17 benchmark harness, which is published with that episode) |
| `scripts/run_eval.py` | ArabicMMLU slice: IFM's prompt and scoring functions, MLX batch generation, resumable |
| `scripts/count_tokens.py` | UDHR token counts per tokenizer |
| `data/speed_runs.csv` | Every timed run: decode, prefill, TTFT, peak memory, power, energy, thermal, versions |
| `data/eval/results_<model>.jsonl` | One line per ArabicMMLU item: id, subject, gold, prediction, finish reason, full model output |
| `data/eval/summary_<model>.json` | Score, Wilson interval, invalid and capped counts |
| `data/tokenizer/token_counts.json` | Token counts and character counts |
| `logs/llamacpp-gguf-load-failure.log` | The llama-server load failure |

ArabicMMLU item IDs and outputs derive from `IFM/ArabicMMLU_verified` (CC BY-NC 4.0); see that dataset for its terms.
