#!/bin/bash
# Test set 1 (language models), clean load. Resumable: rerun to continue where it stopped.
# Usage: scripts/run_set1.sh [extra harness args, e.g. --variants qwen27b-mlx4 --contexts 1024]
set -u
cd "$(dirname "$0")/.."
exec caffeinate -dimsu .venv/bin/python -m bench.harness --set 1 --load-condition clean "$@"
