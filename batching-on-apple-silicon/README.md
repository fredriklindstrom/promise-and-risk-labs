# Batching on Apple silicon

One Mac Studio, one 27-billion-parameter model, and one question: how much more does the same chip produce when it answers several requests at once?

Measured for the Season 4 finale of [Promise & Risk of AI](https://promiseandrisk.ai/labs/batching-on-apple-silicon/). Everything needed to check the numbers is in this folder: the scripts, the raw output of every run, and the results.

## Result

Qwen 3.8-27B, MLX 8-bit, on an Apple M2 Max (38-core GPU, 96 GB). Median of three runs; no step varied by more than 0.4% between runs.

| Requests at once | Total tokens/sec | Per request tokens/sec | vs one request |
|---:|---:|---:|---:|
| 1  | 12.4 | 12.4 | 1.0x |
| 2  | 22.7 | 11.3 | 1.8x |
| 4  | 32.6 | 8.2  | 2.6x |
| 8  | 27.1 | 3.4  | 2.2x |
| 16 | 51.5 | 3.2  | 4.2x |

Sixteen requests at once produce 4.2 times the total output of one. Each individual request slows from about 12 tokens per second to about 3.

The dip at eight is real (it showed up in every run). A sweep from four to twelve places it between five and six requests, where the time for one generation step doubles, from 0.15 s to 0.29 s, and then stays nearly flat all the way to sixteen. Why MLX changes behaviour at six is not established here. Full detail in [results.md](results.md).

## Setup

| | |
|---|---|
| Machine | Mac Studio (2023, Mac14,13) |
| Chip | Apple M2 Max, 38-core GPU |
| Memory | 96 GB unified |
| OS | macOS 27.0 (26A428) |
| Model | [lmstudio-community/Qwen3.8-27B-MLX-8bit](https://huggingface.co/lmstudio-community/Qwen3.8-27B-MLX-8bit), dense, 8-bit affine, group size 64 |
| Engine | mlx-lm 0.31.3 on MLX 0.32.2 |
| Workload | 128-token random prompt, 128 generated tokens per request, end-of-sequence disabled |
| Date | 24 September 2026 |

## Reproduce it

You need an Apple silicon Mac with enough memory for the model (peak use here was 33 GB at 16 requests) and about 28 GB of disk for the download.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./scripts/full-run.sh        # three runs, output to runs/run1.txt .. run3.txt
python3 scripts/parse.py     # medians and run-to-run spread
./scripts/diag.sh            # optional: the sweep around the dip
```

Set `MODEL=` to try a different MLX model. Close everything else first; the numbers in this folder were taken with the machine otherwise idle.

## What's in here

| Path | What it is |
|---|---|
| `results.md` | Full results, method notes and the diagnostic sweep |
| `runs/run1.txt` to `run3.txt` | Unedited `mlx_lm.benchmark` output for the three measured runs |
| `runs/diag.txt` | Unedited output of the batch 4 to 12 sweep |
| `runs/full-mem.txt` | Load average, swap and free memory, sampled every 5 s during the three runs |
| `runs/demo-*.txt` | Logs from single test passes of the on-screen demo with Qwen 3.6-27B and Mistral Small 3.2-24B |
| `scripts/full-run.sh`, `diag.sh` | The measurement scripts |
| `scripts/parse.py` | Turns the raw runs into the results table |
| `scripts/demo.py`, `demo.sh` | The interactive demo recorded for the episode |

## How the numbers are computed

- **Total tokens/sec** is `generation_tps` as reported by `mlx_lm.benchmark`: tokens generated across all requests, divided by generation time. It is the same quantity llama.cpp reports as `S_TG`.
- **Per request** is that figure divided by the number of requests.
- A single request runs through `stream_generate`; two or more run through `batch_generate`. That is how the stock benchmark works.
- Each batch size is a separate invocation with one warm-up pass before the timed pass.

## Limits

One machine, one workload shape, one engine version. Longer prompts, longer outputs, a different quantisation or a different MLX release can all move these numbers. The Qwen 3.6 and Mistral figures in the demo logs are single passes, not three-run medians.
