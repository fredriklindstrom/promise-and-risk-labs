#!/bin/bash
# Agent-loop cache test: a scripted ten-turn coding session against each runner's server, caching ON.
#   scripts/run_cache_test.sh --dry-run                 Qwen on MLX, condition A, 3 turns -> results/cache_test.dry.csv
#   scripts/run_cache_test.sh                           the full plan (resumable: finished run_ids are skipped)
#   scripts/run_cache_test.sh --variants qwen27b-mlx4 --conditions A,B
#   scripts/run_cache_test.sh --ollama-default-ctx      one row in results/cache_test_notes.md
# Pause: touch runs/.cache_pause stops before the next run. Progress: runs/.cache_status.json.
set -euo pipefail
cd "$(dirname "$0")/.."
caffeinate -dimsu .venv/bin/python -m bench.cache_test "$@"
