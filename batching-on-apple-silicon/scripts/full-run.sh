#!/bin/bash
# Three full runs of mlx_lm.benchmark at batch 1, 2, 4, 8, 16. Raw output per run in runs/.
cd "$(dirname "$0")/.."
export HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-0}
M=${MODEL:-lmstudio-community/Qwen3.8-27B-MLX-8bit}
mkdir -p runs
( while true; do echo "$(date +%T) load1=$(sysctl -n vm.loadavg | awk '{print $2}') swap=$(sysctl -n vm.swapusage | awk '{print $6}') free=$(memory_pressure | awk '/free percentage/{print $5}')"; sleep 5; done ) > runs/full-mem.txt 2>&1 &
MON=$!
for r in 1 2 3; do
  for b in 1 2 4 8 16; do
    mlx_lm.benchmark --model "$M" -p 128 -g 128 -b $b -n 1 2>&1
  done > runs/run$r.txt
  [ $r -lt 3 ] && sleep 60
done
kill $MON
