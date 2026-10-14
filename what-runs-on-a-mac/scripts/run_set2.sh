#!/bin/bash
# Test set 2 (production stack), clean load. Resumable.
# Usage: scripts/run_set2.sh h3|voice|whisper|ltx [extra args]
set -u
cd "$(dirname "$0")/.."
JOB="${1:?h3|voice|whisper|ltx}"; shift
exec caffeinate -dimsu .venv/bin/python -m bench.media.run_media --jobs "$JOB" --load-condition clean "$@"
