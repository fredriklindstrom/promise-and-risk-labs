#!/bin/bash
# Diagnostic sweep around the dip: batch 4 to 12, plus 16.
cd "$(dirname "$0")/.."
export HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-0}
M=${MODEL:-lmstudio-community/Qwen3.8-27B-MLX-8bit}
for b in 4 5 6 7 8 9 10 11 12 16; do
  mlx_lm.benchmark --model "$M" -p 128 -g 128 -b $b -n 1 2>&1
done > runs/diag.txt
