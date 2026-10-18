# Jais 3 on Apple silicon: results

Same text as the [lab page](https://promiseandrisk.ai/labs/jais-3-on-apple-silicon/).

59.6 minutes. That is how long Jais 3 32B read a 128,000-token prompt on a Mac Studio before it wrote its first word. The mixture-of-experts sibling did the same in 18.7.

IFM, the Institute of Foundation Models at MBZUAI in Abu Dhabi, released Jais 3 on 15 October 2026: six open models from 0.9B to 375B parameters, Apache 2.0, built for Arabic. This lab runs two of them, the dense Jais 3 32B and the Jais 3 MoVA 36B-A4B mixture of experts (36 billion parameters, about four billion active per token), and asks three questions: do they fit, what does running them cost, and does IFM's accuracy claim hold?

## Memory, speed, wait and power

IFM's own 4-bit MLX builds (`IFM/Jais-3-32B-MLX-4bit`, `IFM/Jais-3-MoVA-36B-A4B-MLX-4bit`), run through mlx-lm 0.31.3 on mlx 0.32.2. Each cell is one discarded warm-up and three timed runs, median and range; greedy decoding, 512 tokens out. Context N means prompt plus 512 output tokens fit in N.

| Model | Context | Writing, tok/s | Reading, tok/s | First word after | Peak memory | Package power avg / peak | Energy per run |
|---|---|---:|---:|---:|---:|---:|---:|
| Jais 3 32B | 1K | 13.6 (13.6–13.6) | 104 | 4.9 s | 26.1 GB | 38 / 58 W | 0.42 Wh |
| Jais 3 32B | 8K | 11.6 (11.6–11.6) | 101 | 76 s | 28.3 GB | 50 / 61 W | 1.64 Wh |
| Jais 3 32B | 32K | 8.7 (8.7–8.7) | 75 | 7.2 min | 34.7 GB | 54 / 62 W | 7.24 Wh |
| Jais 3 32B | 128K | 4.1 (one run) | 36 | **59.6 min** | 60.9 GB | 51 / 89 W | 52.9 Wh |
| Jais 3 36B-A4B | 1K | 40.5 (40.5–40.6) | 456 | 1.1 s | 27.1 GB | 27 / 44 W | 0.08 Wh |
| Jais 3 36B-A4B | 8K | 32.9 (32.8–33.2) | 498 | 15.4 s | 28.8 GB | 45 / 61 W | 0.36 Wh |
| Jais 3 36B-A4B | 32K | 21.0 (20.8–21.0) | 304 | 106 s | 33.5 GB | 52 / 93 W | 1.85 Wh |
| Jais 3 36B-A4B | 128K | 8.2 (8.0–8.7) | 116 | **18.7 min** | 53.3 GB | 50 / 63 W | 16.5 Wh |

The 32B at 128K is one timed run after the warm-up, because each run takes about an hour. Peak memory is MLX's own peak counter for the request: weights, key-value cache and activations. The GPU limit on this Mac is 83.5 GB at the macOS default, which was not raised.

**Files on disk:** 25.5 GB for the 32B and 26.4 GB for the 36B-A4B, against 16 and 18 GB from parameters times four bits. IFM keeps attention, embeddings and the first dense layers at 8 bits and quantises only the feed-forward or expert weights to four (5.6 bits per weight on average for the 36B-A4B).

**Two comparisons, same harness and machine:** Qwen 3.8 27B (MLX 4-bit) writes 21.3 tokens a second at 1K and waits 21.6 minutes at 128K. Llama 3.3 70B (MLX 4-bit) fits at 32K; at 128K it read for 63 minutes and then failed with Metal `Insufficient Memory`.

## Does IFM's Arabic score hold?

IFM's headline figure for the 32B is an Arabic average of 83.1. It is a composite of about 28 benchmarks, several scored by an LLM judge (AraGen 3C3H, FLORES with a judge) and some not yet released, and the weighting is not published. Averaging IFM's own published component scores gives 83.7 (all 28 model-card benchmarks), 83.8 (the four domain averages) or 82.0 (the ten subdomain averages). None gives 83.1.

So this lab checks one component that is in the composite, has released data and has IFM's own task code: ArabicMMLU, on IFM's audited release (`IFM/ArabicMMLU_verified`, 13,690 test items). 100 items drawn at random (seed 17); prompt, answer extraction and scoring from IFM's harness fork (`MBZUAI-IFM/jais3-lm-evaluation-harness`, `arabicmmlu_gen`). Settings from IFM's model card: reasoning effort high, temperature 0.7, top-p 0.95, top-k 20. The thinking block is removed before scoring.

| Model | Correct of 100 | 95% interval (Wilson) | IFM reports | Inside? | No answer (hit 16,384-token cap) | Median output tokens |
|---|---:|---:|---:|---|---:|---:|
| Jais 3 32B | **86** | 77.9–91.5 | 88.6 | yes | 3 | 479 |
| Jais 3 36B-A4B | **85** | 76.7–90.7 | 87.7 | yes | 1 | 380 |
| Qwen 3.8 27B | **80** | 71.1–86.7 | 85.8 | yes | 2 | 267 |

IFM's number sits inside the interval for all three models, and the order matches theirs. All three came in a few points below IFM's figures, which 100 questions can't separate from noise. Nor can they separate Jais 3 from Qwen 3.8: the intervals overlap.

Generation ran on MLX in batches rather than through the harness's server client, which expects vLLM or SGLang. Batches of eight ran out of GPU memory on long reasoning chains (three of the 32B's answers ran to the 16,384-token cap), so the slices were finished in fresh processes of 12 items at batch four. Every scored item was kept; nothing was rerun.

## What Arabic costs in tokens

The UN Universal Declaration of Human Rights, Arabic and English parallel texts from the UDHR in Unicode project, counted with each model's own `tokenizer.json`, no special tokens.

| Tokenizer | Vocabulary | Arabic, Article 1 | Arabic, full text | English, full text | Arabic tokens vs Jais 3 |
|---|---:|---:|---:|---:|---:|
| Jais 3 (both models) | 250,624 | 31 | **1,611** | 1,867 | 1.00× |
| Qwen 3.8 27B | 248,077 | 42 | 2,192 | 1,942 | **1.36×** |
| gpt-oss-120b | 200,019 | 44 | 2,245 | 1,888 | 1.39× |
| Llama 3.3 70B | 128,256 | 48 | 2,721 | 1,887 | **1.69×** |

Jais 3 encodes the Arabic in fewer tokens than the English (0.86 to one). The full Arabic text is 7,315 characters: 4.5 characters per token on Jais 3, 3.3 on Qwen 3.8, 2.7 on Llama 3.3. One text, one register (formal Modern Standard Arabic); dialects and mixed Arabic-English text were not tested.

## What would not run

- **llama.cpp.** IFM's own `Jais-3-MoVA-36B-A4B-Q4_K_M.gguf` (22.4 GB, hash-verified) failed in Homebrew llama.cpp 0.6.0 (build 11429) with `unknown model architecture: 'k2-horizon'`. IFM's GGUF card says support is merged upstream; it is not in that release.
- **mlx-lm without IFM's code.** mlx-lm 0.31 has no `k2_horizon` architecture. IFM's MLX repositories ship `k2_horizon.py` and ask for `trust_remote_code`. I read the file (628 lines of MLX model code, imports only `mlx` and `mlx_lm`, no file or network access), pinned its SHA-256 (`80b501f2…f5483bc`, identical in both repositories) and loaded the reviewed copy with the repository's own file disabled. The file is referenced in the repo by hash, not copied.

## Method notes

- Both repositories were verified file by file against the Hugging Face hashes before loading; every snapshot is pinned to a commit (in [environment.md](environment.md)).
- The speed runs use the benchmark harness from the Nov 17 episode; that harness is published with that episode. The scripts specific to this lab, the worker that loads the reviewed model code, the ArabicMMLU runner and the tokenizer count, are in the repo now, with every raw row.
- Not tested: dialects, agentic and tool-use work, native Arabic writing quality, hosted models, and the other four Jais 3 sizes.
