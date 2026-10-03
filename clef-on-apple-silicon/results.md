# Clef on Apple silicon: full results

Run 2 October 2026, 17:50–19:35 EDT. Mac Studio M2 Max (12-core CPU, 38-core GPU), 96 GB, macOS 27.0.1. Full versions and repo SHAs in [environment.md](environment.md); every number in [runs.csv](runs.csv); raw probabilities in `runs/decisions_*.jsonl`.

Memory below is in decimal GB (10⁹ bytes) to match the episode script. `runs.csv` is in GiB (1024³), which reads about 7% lower.

## 1. Does Clef run on a 96 GB M2 Max?

Yes, at every precision tested. Nothing needed sudo or a raised GPU memory limit.

| Model | Precision | Runtime | Memory, 500-token ticket | Memory, 32K tokens |
|---|---|---|---|---|
| Clef 27B | BF16 | MLX | 55.3 GB | 61.2 GB |
| Clef 27B | 8-bit | MLX | 30.3 GB | 36.0 GB |
| Clef 27B | 4-bit | MLX | 17.0 GB | 22.5 GB |
| Clef-flash 9B | BF16 | MLX | 19.4 GB | 23.5 GB |
| Clef-flash 9B | BF16 | PyTorch (MPS) | 20.4 GB | failed (see 5) |
| Clef-flash 9B | 4-bit | MLX (both conversions) | 6.2–6.9 GB | 10.0–10.7 GB |

One caveat: **Clef 27B BF16 on PyTorch did not load** (run 1, see section 5). The full-precision 27B result comes from my own MLX conversion of Cloudflare's weights, made the same way as mlx-community's (`mlx_vlm.convert`, no quantisation, Cloudflare's decision head copied unchanged). On Clef-flash, MLX and PyTorch at full precision gave the same answer on all 60 decisions (max probability difference 0.016), so changing the runtime did not change the decisions.

## 2. Seconds per decision, 500-token ticket

Median of 5 runs; the spread was under 1% everywhere.

| Configuration | Seconds | Input tokens/s |
|---|---|---|
| Clef-flash BF16, MLX | **0.80** | 626 |
| Clef-flash 4-bit, MLX | 1.03 | 487 |
| Clef-flash BF16, PyTorch | 1.48 | 340 |
| Clef 27B BF16, MLX | **2.77** | 181 |
| Clef 27B 8-bit, MLX | 3.55 | 141 |
| Clef 27B 4-bit, MLX | 3.59 | 140 |

Speed was nearly flat with length. Clef 27B BF16 took 10.5 s at 2K tokens, 41.9 s at 8K and 188 s at 32K. Clef-flash BF16 on MLX took 55.8 s at 32K.

## 3. Did 4-bit or 8-bit change any decisions?

60 questions (20 ticket routing, 20 command safety, 20 invoice risk), compared with full precision on the same runtime family.

| Configuration | Same top answer | Mean / max probability shift | Yes/no flips at 0.5 | Flips at 0.8 |
|---|---|---|---|---|
| Clef 27B 8-bit | 60/60 | 0.002 / 0.017 | 0 | 0 |
| Clef 27B 4-bit | 60/60 | 0.012 / 0.141 | 0 | 1 |
| Clef-flash 4-bit (TrevorJS) | 58/60 | 0.030 / 0.327 | 1 | 0 |
| Clef-flash 4-bit (mlx-community) | 59/60 | 0.030 / 0.329 | 1 | 0 |

- **Clef 27B never changed an answer.** At 4-bit, one yes/no fell below 0.8, the typical auto-approve line: `pip install --upgrade requests` inside a project virtual environment went from 0.845 to 0.791 "safe". At a 0.8 threshold it would stop auto-approving.
- **Clef-flash at 4-bit flipped one agent-safety call.** The case was `terraform apply -auto-approve` adding one tag to staging resources. Full precision said safe (0.78 on MLX, 0.78 on PyTorch). Both independent 4-bit conversions said unsafe (0.45). Because both conversions agree and both runtimes agree at full precision, the cause is the 4-bit quantisation, not the runtime or the conversion. Clef 27B rates the same command unsafe at every precision (0.15–0.20).
- TrevorJS's conversion also flipped one routing ticket (route-18), where full precision was a near coin-flip at 0.52.
- Agreement with my own expected answers was 90.7% for every configuration (92.6% for the TrevorJS flash build), counting only the unambiguous cases. That shows the decision head is working; it is a sanity check, not an accuracy score.

## 4. Predictions made before measuring

These came from back-of-envelope arithmetic in the episode script, written before any run. Measurements were not adjusted toward them.

| Prediction | Measured | Verdict |
|---|---|---|
| Clef BF16 ~58.5 GB for one long request (54 GB + ~4.45 GB at 64K) | Weights 55.0 GB. 61.2 GB peak at **32K** (64K not run) | **Fits, but more than predicted.** The memory added at 32K was 6.1 GB against about 2.1 GB of predicted KV cache. Prefill activations, not the KV cache, account for most of it. Expect roughly 64–67 GB at 64K. That still fits under the ~78 GB default GPU limit. |
| KV cache 64 KB per token | Can't be isolated from activations. Total growth was about 190 KB per token during prefill | Not testable as stated. Use the measured total instead. |
| Clef 4-bit 16.3 GB on disk | 16.3 GB | **Holds** |
| Clef BF16 ~125 tok/s, ~4 s per 500-token ticket | 181 tok/s, 2.77 s (MLX). PyTorch BF16 not measured | **Contradicted: faster than predicted on MLX** |
| Clef-flash BF16 ~380 tok/s, ~1.3 s | 340 tok/s, 1.48 s (PyTorch). 626 tok/s, 0.80 s (MLX) | **Close on PyTorch; MLX is about 1.6 times faster** |
| 4-bit vs BF16 speed roughly similar | 4-bit is **about 30% slower** than BF16 on MLX (3.59 vs 2.77 s for 27B; 1.03 vs 0.80 s for flash). 8-bit is the same as 4-bit | **Partly holds:** quantising doesn't speed it up, and on an M2 Max it slows it down, because weights must be unpacked during a compute-bound prefill. 4-bit saves memory only. |

## 5. Failures and what I tried

1. **Clef 27B BF16 on PyTorch (run 1): did not load.** My memory watchdog stopped it at 33% of weight loading. Swap had grown 4.9 GB in about 40 seconds. PyTorch/Transformers appears to stage weights through CPU memory before copying them to the GPU, so the load briefly needs about twice the model size. I set `PYTORCH_MPS_HIGH_WATERMARK_RATIO=1.0`, capping GPU allocations at 77.8 GB; that cap was not what stopped it. I did not retry, because a PyTorch job with the same memory pattern crashed this machine (kernel panic) on 17 September 2026. I replaced it with run 6 (MLX BF16).
2. **Clef-flash BF16 on PyTorch at 32K: aborted.** The error was `MPSGraph: Failed to allocate MTLBuffer of 65347719424 B for oversized fused alloc`. PyTorch's attention on Apple GPUs builds the full 32K × 32K attention matrix (65 GB) in one go. It failed instantly, with no swap. Every length up to 8K worked.
3. **Clef 27B BF16 on MLX at 32K, first attempt:** the watchdog stopped it when swap grew 6.4 GB while 29% of memory was still free. That was macOS paging out idle apps, not a runaway job. I re-ran just that step with looser limits (kill at +12 GB swap or below 12% free). It completed and the watchdog did not trigger.
4. **Harness and setup issues, all fixed:** the download script's exclude flag was wrong (fixed and re-downloaded); the TrevorJS tokenizer wrapper wasn't callable (fixed and re-ran run 5); `mlx_vlm.convert` failed on a read-only cached `tokenizer.json` (fixed with `chmod u+w` on that one cache file).
5. **PyTorch did not need the CUDA kernels.** Transformers fell back to its pure-PyTorch linear-attention code. `PYTORCH_ENABLE_MPS_FALLBACK` was never needed and nothing ran on the CPU.

## Deviations from the brief

- **The 32K step used 3 repetitions, not 5,** to keep the session under 3 hours (it finished in about 1 h 45 min). The spread across the 3 repetitions was under 0.5%.
- **The full-precision reference for Clef 27B is MLX, not PyTorch** (see failure 1). I also added run 7 (Clef-flash BF16 on MLX) and run 5b (mlx-community's Clef-flash 4-bit) to separate the effect of the runtime from the effect of the precision.
- **The 32K inputs exceed the 16K length the decision head was trained at.** Timing and memory at 32K are valid; decisions at that length were not checked.
- **Two invoice numbers in the published test set differ from the measured run.** Records inv-10 and inv-18 used a different invoice-number prefix when measured; it was changed to `KS-` for publication. Both records were re-run on every configuration with the published numbers: same top answer in all 14 cases, maximum probability difference 0.016 ([runs/recheck_invoice_numbers.json](runs/recheck_invoice_numbers.json)).
- **The test tickets are 46–85 words, not 80–300.** The length sweep covers the longer inputs.
- **Load times are not true cold starts,** because the files were already in the macOS file cache from the download. A second load took 1–4 s.
- **Skipped:** the 64K request (beyond the head's training length, and it would have added about 7 minutes) and the GPU power reading (needs sudo, and I didn't ask).
- **GGUF repos:** `bartowski/Cloudflare_clef-GGUF` has 74 files and none of them is a decision-head file. That confirms they are text-generation only. They were not benchmarked.
- **Other apps running during the tests:** Chrome, Outlook, Mail, Claude, the ChatGPT/Codex app and a small virtual machine. None was heavy. About 42 GB of old swap from earlier in the week was already in use, and every swap figure above is growth from that baseline.
