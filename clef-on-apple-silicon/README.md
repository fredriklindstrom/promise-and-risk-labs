# Clef on Apple silicon

Cloudflare released two open-weight decision models on 1 October 2026: Clef (27B) and Clef-flash (9B). They return a probability for every allowed answer instead of writing text. This lab asks two questions: does Clef run on a Mac Studio, and what does shrinking it to 8-bit or 4-bit change?

Measured for [Promise & Risk of AI](https://promiseandrisk.ai/labs/clef-on-apple-silicon/). Everything needed to check the numbers is in this folder: the scripts, the 60-record synthetic test set, the raw probabilities from every run, the logs (including the failures), and the results.

## Result

Mac Studio, Apple M2 Max (38-core GPU), 96 GB. Seconds to decide one 500-token support ticket, median of five runs (spread under 1%):

| Model | Precision | Runtime | Seconds | Memory |
|---|---|---|---:|---:|
| Clef-flash 9B | BF16 | MLX | 0.80 | 19.4 GB |
| Clef-flash 9B | 4-bit | MLX | 1.03 | 6.2 GB |
| Clef-flash 9B | BF16 | PyTorch (MPS) | 1.48 | 20.4 GB |
| Clef 27B | BF16 | MLX | **2.77** | 55.3 GB |
| Clef 27B | 8-bit | MLX | 3.55 | 30.3 GB |
| Clef 27B | 4-bit | MLX | 3.59 | 17.0 GB |

Clef 27B runs at full precision on a 96 GB Mac. At 32K tokens it peaked at 61.2 GB and took 188 seconds.

**Quantising saved memory and cost speed.** On the M2 Max, 4-bit was about 30% slower than BF16, because the GPU has to unpack the weights during a prefill that is already compute-bound.

**Quantising Clef 27B changed no decisions.** 8-bit and 4-bit agreed with BF16 on all 60 test questions.

**Quantising Clef-flash flipped one agent-safety call.** At BF16, Clef-flash rated `terraform apply -auto-approve` (adding one tag to staging resources) safe to run without a human, at 0.78. Both independent 4-bit conversions rated it unsafe, at 0.45. BF16 on MLX and BF16 on PyTorch gave the same answer (60 of 60 decisions, max probability difference 0.016), so the flip comes from the quantisation, not the runtime. Clef 27B rates the same command unsafe at every precision.

Full detail, the predictions checked against the measurements, and every failure: [results.md](results.md).

## What failed

- **Clef 27B BF16 on PyTorch did not load.** A memory watchdog stopped it at 33% of weight loading, after swap grew 4.9 GB in about 40 seconds. The full-precision 27B reference here is an MLX conversion of Cloudflare's own weights instead.
- **Clef-flash BF16 on PyTorch at 32K tokens aborted.** Metal refused a single 65 GB allocation: the full 32K × 32K attention matrix. Every length up to 8K worked.

## Setup

| | |
|---|---|
| Machine | Mac Studio (2023, Mac14,13) |
| Chip | Apple M2 Max, 38-core GPU |
| Memory | 96 GB unified, macOS default GPU limit (not raised) |
| OS | macOS 27.0.1 (26A434) |
| MLX path | mlx 0.32.3, mlx-vlm 0.7.4, the `clef_mlx.py` loader shipped with the MLX conversions |
| PyTorch path | torch 2.11.0, transformers 5.10.2, Cloudflare's `joint_schema_model.py`, device `mps` |
| Models | `Cloudflare/clef`, `Cloudflare/clef-flash`, `mlx-community/clef-8bit`, `mlx-community/clef-4bit`, `TrevorJS/clef-flash-mlx-4bit`, `mlx-community/clef-flash-4bit` (commit SHAs in [environment.md](environment.md)) |
| Date | 2 October 2026 |

Every run went through Clef's decision head (`joint_head.safetensors`). The GGUF builds for llama.cpp, LM Studio and Ollama were left out: `bartowski/Cloudflare_clef-GGUF` contains no head file, so those tools can only generate text with the backbone, and that text is meaningless.

## Reproduce it

You need an Apple silicon Mac. Clef 27B at 4-bit needs about 22 GB at 32K tokens; BF16 needs about 61 GB. The full download set is about 125 GB.

```bash
python3 -m venv .venv-mlx && .venv-mlx/bin/pip install -r requirements-mlx.txt
python3 -m venv .venv-torch && .venv-torch/bin/pip install -r requirements-torch.txt   # runs 1 and 4 only
HF=.venv-mlx/bin/hf ./scripts/download.sh

P=$(.venv-mlx/bin/hf download mlx-community/clef-4bit --quiet)
.venv-mlx/bin/python scripts/bench.py --run r3_clef_4bit_mlx --backend mlxc --path "$P"
python3 scripts/compare.py      # agreement and flips vs BF16, writes runs/runs.csv
```

`bench.py` loads one configuration, runs three warm-up decisions, the 60-record accuracy pass, then the length sweep (250, 500, 2,000, 8,000 and 32,000 tokens). Backends: `mlxc` (mlx-community loader), `trevor` (TrevorJS loader), `torch` (Cloudflare loader). Run `scripts/watchdog.sh "bench.py --run <name>"` alongside any large run; it kills the job if swap grows by more than 4 GB.

Read each model repo's Python file before importing it. These loaders are custom code.

## What's in here

| Path | Contents |
|---|---|
| `results.md` | Full write-up: memory, speed, decision changes, predictions, failures, deviations |
| `runs.csv` | One row per configuration (memory in GiB) |
| `environment.md` | Hardware, OS, package versions, Hugging Face commit SHAs |
| `data/testset.jsonl` | 60 synthetic records: 20 ticket routing, 20 command safety, 20 invoice risk. Dangerous commands are described in words, never runnable. Two invoice numbers changed after measuring; re-check in `runs/recheck_invoice_numbers.json` |
| `scripts/` | Test-set builder, benchmark harness, comparison, watchdog, download |
| `runs/decisions_*.jsonl` | Every probability for every record in every run |
| `runs/metrics_*.json` | Load time, memory and sweep timings per run |
| `runs/logs/` | Unedited run logs (progress bars removed), including the failures |
