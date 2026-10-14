#!/bin/bash
# Build results/report.md from the CSVs (add --morning for the overnight summary).
cd "$(dirname "$0")/.." && exec .venv/bin/python -m bench.report "$@"
