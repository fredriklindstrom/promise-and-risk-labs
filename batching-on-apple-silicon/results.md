# Batching on Apple silicon: results

Measured 2026-09-24, 07:36 to 07:54, on a quiet machine (load average under 5.5, no swap).
Three full runs; each figure below is the **median of the three**. Raw output: `runs/run1.txt`, `run2.txt`, `run3.txt`.

## Table

| Requests at once | Total generation (tokens/sec, S_TG) | Per request (tokens/sec) | Total throughput incl. prompt (tokens/sec, S) | Peak memory |
|---:|---:|---:|---:|---:|
| 1  | 12.36 | 12.36 | 22.04 | 29.0 GB |
| 2  | 22.69 | 11.34 | 39.11 | 29.4 GB |
| 4  | 32.60 |  8.15 | 53.37 | 30.2 GB |
| 8  | 27.14 |  3.39 | 45.95 | 31.6 GB |
| 16 | 51.48 |  3.22 | 76.59 | 33.0 GB |

Run-to-run spread: at most 0.4% at any step (S_TG runs: 1 = 12.36/12.35/12.36, 2 = 22.64/22.69/22.73, 4 = 32.65/32.60/32.59, 8 = 27.14/27.14/27.16, 16 = 51.46/51.48/51.50).

How the columns are computed:
- **S_TG** is the benchmark's own `generation_tps`. For 2 or more requests it is generated tokens across all requests divided by generation time, the same definition as llama.cpp's S_TG.
- **Per request** is S_TG divided by the number of requests.
- **S** is (requests × 256 tokens) / `total_time` for the timed trial, covering prompt processing plus generation.

## In plain words

- One request: about 12 tokens per second.
- Sixteen at once: about 51 tokens per second in total, a little over four times as much from the same chip.
- The cost: each individual request slows down, from about 12 tokens per second to about 3.
- The gain is not a smooth curve. Two requests nearly double the output. Four give about two and a half times. Eight requests at once were slower than four, in every run. Sixteen gave the best result of all.

## Setup

- Machine: Mac Studio (2023, Mac14,13)
- Chip: Apple M2 Max, 38-core GPU
- Memory: 96 GB unified
- OS: macOS 27.0 (26A428)
- Model: Qwen 3.8-27B (lmstudio-community/Qwen3.8-27B-MLX-8bit)
- Format: MLX safetensors
- Quantisation: 8-bit affine, group size 64
- Architecture: dense (no mixture-of-experts); hybrid attention, 48 linear-attention + 16 full-attention layers
- Engine: mlx_lm 0.31.3 on MLX 0.32.2 (`mlx_lm.benchmark`)
- Command, run once per batch size N in 1, 2, 4, 8, 16:
  `mlx_lm.benchmark --model lmstudio-community/Qwen3.8-27B-MLX-8bit -p 128 -g 128 -b N -n 1`
  (random 128-token prompts, 128 tokens generated per request, end-of-sequence disabled, one warm-up pass before each timed trial)
- Date: 2026-09-24

## Notes

- The original plan used llama.cpp's `llama-batched-bench`. MLX was used instead because the model was already on the machine in MLX format; the columns map to S_TG and S as described above.
- A single request goes through `stream_generate`; 2 or more go through `batch_generate`. That is how the stock tool works.
- The dip at 8 requests is reproducible (it appeared again in the `demo.sh` test run: 27.2).

## Diagnostic: where the dip comes from (2026-09-24, 08:47 to 09:02, single pass, not part of the main table)

Raw output: `runs/diag.txt`. Same command, batch sizes 4 to 12 plus 16. A Time Machine backup was running; the anchor points (4, 8, 16) still matched the main runs within 0.3%.

| Requests | Total tok/s | Per request tok/s | Time per generation step |
|---:|---:|---:|---:|
| 4  | 32.69 | 8.17 | 0.12 s |
| 5  | 33.85 | 6.77 | 0.15 s |
| 6  | 20.40 | 3.40 | 0.29 s |
| 7  | 23.75 | 3.39 | 0.29 s |
| 8  | 27.17 | 3.40 | 0.29 s |
| 9  | 29.88 | 3.32 | 0.30 s |
| 10 | 33.03 | 3.30 | 0.30 s |
| 11 | 36.14 | 3.29 | 0.30 s |
| 12 | 39.62 | 3.30 | 0.30 s |
| 16 | 51.43 | 3.21 | 0.31 s |

(Time per step = requests / total tok/s: how long one round of "every request gets one more token" takes.)

What this shows:
- It is a cliff between 5 and 6 requests, not something special about 8. At 6 the time per step roughly doubles, from 0.15 s to 0.29 s.
- From 6 upward the step time barely moves (0.29 s to 0.31 s), so every extra request adds almost pure throughput: total output rises in a straight line from 20 at six requests to 51 at sixteen.
- So there are two regimes: up to 5 requests, fast individual answers with modest total gain; from 6, slow individual answers but total output that keeps climbing with each request added.
- Why the engine changes behaviour at 6 has not been verified. The likely explanation is that MLX switches to a different computation method at that batch size, but I have not confirmed it in the MLX source, so it is a hypothesis, not a finding.
