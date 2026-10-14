# What runs on a Mac: local AI, measured

The harness behind the Promise & Risk of AI episode *"Your Mac Can Run More AI Than You Think — How Much Memory Do You Need for Local AI?"*. It measures
local language models, text-to-speech and (optionally) video generation on Apple-silicon Macs, logs every number to
files, and shows the run live in **Bench Monitor**, a small native dashboard.

Every number measured for the episode comes from a log or an API response, never from a screen. You can rerun the same tests on
your own Mac with the same pinned models, prompts and settings, and compare.

## What it measures

- **Language models** across runners on the same model: MLX (`mlx-lm`), llama.cpp, Ollama, and optionally oMLX,
  MTPLX and mlx-serve. Decode and prefill speed, time to first token, load time, peak memory, power and energy, thermal state and
  chip temperature, fit / does-not-fit at 1K–128K context, and a needle-in-a-haystack retrieval check.
- **Agent loop** (`scripts/run_cache_test.sh`): a scripted coding session of up to ten turns against each runner's own
  server, with prompt caching on, measuring what a broken cache costs. Prompts and turn script are in
  `inputs/agent_loop/`.
- **Narration** (text-to-speech) on MLX engines: real-time factor, peak memory, energy. The episode's results also include
  one PyTorch engine (Chatterbox on MPS) for comparison; it isn't in the example manifests, and its package is not
  hash-locked, so add it only if you accept that.
- **Video** (MiniMax H3 through mlx-serve or the PipeNetwork MLX port): seconds per diffusion step, minutes per clip.
  Not enabled in the example manifests — H3's licence requires your own authorisation from MiniMax.

## Requirements

- An Apple-silicon Mac on macOS 14 or later. 16 GB works for the small profile; the large profile was measured on 96 GB.
- Xcode Command Line Tools (`xcode-select --install`) for building the Swift tools.
- [uv](https://docs.astral.sh/uv/) (Python 3.12 environments), llama.cpp (`brew install llama.cpp`), and
  Ollama from [ollama.com](https://ollama.com) if you want the Ollama rows, and mlx-serve for the Flash-Next rows:
  download `mlx-serve-bin-macos-arm64.tar.gz` from [release v26.10.1](https://github.com/ddalcu/mlx-serve/releases/tag/v26.10.1)
  (SHA-256 `e53056e481364ff72188fafea8b3eb0aeb0b5b7cebcd6e6e7d26bf1ce4223873`), then
  `mkdir -p tools/mlx-serve-26.10.1 && tar xzf mlx-serve-bin-macos-arm64.tar.gz --strip-components 1 -C tools/mlx-serve-26.10.1`.
  Install them yourself; this repo ships no binaries.
- Free disk: the large profile downloads about 500 GB of weights; the 16 GB profile about 40 GB.

## Quick start

```bash
scripts/setup.sh                                   # builds the Swift tools from source, creates the Python envs (hash-locked)
cp manifest-16gb.example.yaml manifest.yaml        # or manifest.example.yaml on a 64 GB+ Mac; read it first
.venv/bin/python -m bench.download                 # downloads pinned models and verifies every file's hash
scripts/preflight.sh                               # what this Mac is, what's running, nothing changed
open "app/BenchMonitor/build/Bench Monitor.app"    # optional: the live dashboard
sudo sampler/power_sampler.sh                      # optional, separate Terminal: power + thermal pressure (root)
scripts/run_set1.sh                                # language models; rerun to resume
scripts/report.sh                                  # results/report.md from the CSVs
```

Quit other apps first: anything using the GPU or memory changes the numbers. The harness refuses to start while
another local model server is running.

## How the numbers are defined

- **Context N** = prompt + 512 output tokens fit in N. The prompt is trimmed with the model's own tokenizer and chat
  template; the runner-reported prompt token count goes in the results.
- **Workload**: *War and Peace* (Project Gutenberg #2600, public domain; `inputs/context_corpus.txt`, hash in
  `context_corpus.SOURCE.txt`). Task: write an essay on the excerpt. Greedy decoding, seed 17, 512 output tokens.
  Thinking is off for Qwen models on every runner; gpt-oss runs at reasoning effort low.
- **No prompt caching** (except the agent loop, which tests it): a fresh nonce starts every prompt; server-side caches are off where the runner allows it.
- **Reps**: one discarded warm-up, then the timed reps from the manifest (median and range reported).
- **Timing**: the runner's own timing fields where it has them (llama.cpp, MTPLX, Ollama), client-side timestamps
  otherwise (MLX, oMLX). Each row says which in `timing_source`.
- **Peak memory**: MLX's peak counter; Ollama's `size_vram`; otherwise GPU-allocated memory (IOAccelerator) during the
  run minus the idle baseline. `peak_mem_source` names it on every row.
- **Fit**: `no` when the runner fails to allocate, is killed, or the memory watchdog trips (critical pressure with swap
  growth). On a small Mac "does not fit" is a result, not a bug.
- **Power and energy** need `sudo sampler/power_sampler.sh` (only `/usr/bin/powermetrics` runs as root). Without it,
  everything else still runs and energy is marked missing.
- **Thermal**: macOS's thermal state every second (public API), thermal pressure and GPU/CPU clocks from powermetrics,
  and the hottest SoC die sensor (see *Private API* below).

## Security and privacy

This repo is about running AI locally and safely, so the harness practises it:

- **Weights**: safetensors and GGUF only. Downloads use an allowlist of data-only file types, every repo is pinned to a
  full commit SHA, and every file is verified against the Hub's own hash. The harness refuses to load a model without
  a complete verified record, and refuses any repo whose config asks to import code (`model_file`, `auto_map`).
  `trust_remote_code` is never set. Runners run with `HF_HUB_OFFLINE=1`.
- **Ollama tags are mutable**: each Ollama variant records the digest it was measured with, and the runner refuses a
  different one.
- **Python dependencies** install with `--require-hashes` from `requirements/*.txt`.
- **Network**: every model server the harness starts binds to 127.0.0.1 (a random port, except Ollama's own 11434). If an
  Ollama server is already running, the harness uses it as you configured it. The Swift tools make no network calls.
- **Root**: only `powermetrics` runs as root, from `sampler/power_sampler.sh`, which sets a fixed `PATH` and runs its
  parser as you. Nothing else needs or accepts root.
- **Manifests are code-adjacent**: they choose what is downloaded and which binaries run. Don't run one you haven't read.
- **Your logs identify your machine**: run folders contain your username in paths, your Mac's model, and some runners
  log the hostname. Scrub `runs/` and `results/` before sharing them. Recording the list of open apps is off by default
  (`record_foreground_apps`).
- **Voice cloning**: only use a reference voice you have the right to clone. No reference voices ship with this repo.

**Private API.** Chip temperatures come from Apple's private IOHID sensor interface (read-only, no root; the same one
monitoring apps use). It is looked up at run time, so a macOS that changes it shows "—" instead of crashing; set
`BENCH_NO_PRIVATE_SENSORS=1` to turn it off.

Found a security problem? See [SECURITY.md](SECURITY.md).

## Layout

| Path | What |
|---|---|
| `bench/` | The harness (Python): runners, downloads + verification, workloads, media jobs, report |
| `sampler/` | Swift: 1 Hz sampler (no root), `bench-power` parser, `power_sampler.sh` (the one sudo script) |
| `app/BenchMonitor/` | Swift: the live dashboard (build from source; see its README) |
| `manifest*.example.yaml` | What to test: models, pinned revisions, runners, settings |
| `requirements/` | Hash-locked Python dependencies |
| `scripts/` | setup, preflight, run, report |
| `measured/` | The episode's own results: per-run CSVs for the Mac Studio and the MacBook Air, the agent-loop CSV, the tables, and the Air's throttling log |

## Licence

Code: MIT (see the repository's LICENSE). Results and write-ups: CC BY 4.0. Models keep their own licences; read each
model card before downloading (Llama 3.3 community licence, Qwen and gpt-oss Apache-2.0, Qwen community licence for
Qwen3.8-Flash-Next, MiniMax H3 community licence with authorisation).
