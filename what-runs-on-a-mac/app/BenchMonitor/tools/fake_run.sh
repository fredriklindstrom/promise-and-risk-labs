#!/bin/bash
# fake_run.sh — simulates the benchmark harness so Bench Monitor can be tested without models.
#
# Creates runs/fake-<n>/ folders, repoints runs/current at each in turn and writes realistic
# events.jsonl sequences over ~2.5 minutes:
#   1. LLM run (dense, MLX)        loading → prefill → decode (progress every 0.5 s) → run_end
#   2. LLM run at 128K (MoE)       loading → prefill → run_fail "OOM at 128K"
#   3. Media run (video)           media_progress per step, 2 clips → run_end
#   4. LLM run (MoE, fast)         → run_end
#   then queue_end.
# Results go to runs/history.fake.jsonl (never the real history.jsonl). Launch the app with
#   BENCH_HISTORY=history.fake.jsonl
# so fake rows never pollute the real table.
#
# On exit (normal, Ctrl-C or error) it deletes the runs/fake-* folders IT created and restores
# runs/current to whatever it pointed at before (or removes it if there was none).
#
# Options:
#   --project DIR        project root (default $BENCH_PROJECT, else the project this script is in)
#   --stall N            go silent for N seconds in the middle of run 1's decode (tests "No data";
#                        the app's default threshold is 60 s, override with BENCH_STALE_S=10)
#   --synthetic-samples  also write fake samples.jsonl lines (memory ramps, pressure, GPU power) —
#                        do NOT run bench-sampler at the same time. Without it, run bench-sampler.
#   --clean-history      delete runs/history.fake.jsonl at the end
#   --no-seed            don't seed history.fake.jsonl with earlier fake results
set -euo pipefail

PROJECT="${BENCH_PROJECT:-$(cd -P -- "$(dirname -- "$0")/../../.." && pwd -P)}"   # tools/ → app/BenchMonitor → app → project
STALL=0
SYNTH=0
CLEAN_HISTORY=0
SEED=1
while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --stall) STALL="$2"; shift 2 ;;
    --synthetic-samples) SYNTH=1; shift ;;
    --clean-history) CLEAN_HISTORY=1; shift ;;
    --no-seed) SEED=0; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

RUNS="$PROJECT/runs"
HIST="$RUNS/history.fake.jsonl"
mkdir -p "$RUNS"

# --- record runs/current so we can put it back -------------------------------------------------
PREV=""
if [ -L "$RUNS/current" ]; then
  PREV="$(readlink "$RUNS/current")"
elif [ -e "$RUNS/current" ]; then
  echo "fake_run: $RUNS/current exists and is not a symlink — refusing to touch it" >&2
  exit 1
fi
echo "fake_run: runs/current was: ${PREV:-<none>}"

CREATED=()
cleanup() {
  local d
  for d in "${CREATED[@]:-}"; do
    case "$d" in
      "$RUNS"/fake-*) rm -rf -- "$d" ;;
    esac
  done
  if [ -n "$PREV" ]; then ln -sfn "$PREV" "$RUNS/current"; else rm -f "$RUNS/current"; fi
  if [ "$CLEAN_HISTORY" = 1 ]; then rm -f "$HIST"; fi
  echo "fake_run: cleaned up; runs/current restored to: ${PREV:-<none>}"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# --- helpers -----------------------------------------------------------------------------------
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
ts_ago() { date -u -v-"$1"S +%Y-%m-%dT%H:%M:%SZ; }
jit() { awk -v b="$1" -v s="$2" -v r="$RANDOM" 'BEGIN { printf "%.2f", b + s * ((r / 32767) * 2 - 1) }'; }
calc() { awk "BEGIN { printf \"%.2f\", $1 }"; }

DIR=""
N=0
new_run_dir() {
  N=$((N + 1))
  local i=1
  while [ -e "$RUNS/fake-$i" ]; do i=$((i + 1)); done
  DIR="$RUNS/fake-$i"
  mkdir -p "$DIR"
  CREATED+=("$DIR")
  ln -sfn "fake-$i" "$RUNS/current"
  echo $$ > "$DIR/runner.pid"
}
emit() { printf '%s\n' "$1" >> "$DIR/events.jsonl"; }
hist() { printf '%s\n' "$1" >> "$HIST"; }

# synthetic sample state
MEM_BASE=22.0; MEM=22.0; MEM_TARGET=22.0; GPU=2; GPUW=0.8; PRESS=normal; SWAP=0.7; DISK=412.0; LAST_SAMPLE=0
sample() {
  [ "$SYNTH" = 1 ] || return 0
  local now; now=$(date +%s)
  [ "$now" != "$LAST_SAMPLE" ] || return 0
  LAST_SAMPLE=$now
  MEM=$(awk -v m="$MEM" -v t="$MEM_TARGET" 'BEGIN { d = t - m; if (d > 6) d = 6; if (d < -12) d = -12; printf "%.2f", m + d }')
  PRESS=$(awk -v m="$MEM" 'BEGIN { print (m > 90 ? "critical" : (m > 80 ? "warn" : "normal")) }')
  local g w p
  g=$(awk -v b="$GPU" -v r="$RANDOM" 'BEGIN { v = b + (b > 10 ? 4 : 1) * ((r / 32767) * 2 - 1); if (v < 0) v = 0; if (v > 100) v = 100; printf "%.0f", v }')
  w=$(jit "$GPUW" 1.2); p=$(calc "$w + 6.5")
  printf '{"t":"%s","type":"sample","mem_used_gb":%s,"mem_free_gb":%s,"mem_compressed_gb":1.2,"mem_wired_gb":%s,"mem_pressure":"%s","swap_gb":%s,"gpu_util_pct":%s,"gpu_power_w":%s,"pkg_power_w":%s,"runner_rss_gb":0.4,"disk_free_gb":%s,"disk_free_gb_external":4199.6}\n' \
    "$(ts)" "$MEM" "$(calc "96 - $MEM")" "$(calc "$MEM * 0.8")" "$PRESS" "$SWAP" "$g" "$w" "$p" "$DISK" >> "$DIR/samples.jsonl"
}
# sleep in 0.5 s ticks so synthetic samples keep flowing
wait_s() {
  local ticks; ticks=$(awk -v s="$1" 'BEGIN { printf "%d", s * 2 }')
  local i
  for ((i = 0; i < ticks; i++)); do sleep 0.5; sample; done
}

# --- seed earlier results ----------------------------------------------------------------------
if [ "$SEED" = 1 ] && [ ! -s "$HIST" ]; then
  hist "{\"t\":\"$(ts_ago 5400)\",\"type\":\"run_end\",\"run_id\":\"s1-llama33-8b-mlx4-32k-r1\",\"fit\":\"yes\",\"decode_tps\":71.8,\"prefill_tps\":905.2,\"ttft_s\":0.41,\"peak_mem_gb\":7.9,\"energy_wh\":0.6,\"model\":\"Llama 3.3 8B\",\"runner\":\"MLX\",\"quant\":\"MLX 4-bit\",\"context_tokens\":32768,\"kind\":\"llm\"}"
  hist "{\"t\":\"$(ts_ago 5100)\",\"type\":\"run_end\",\"run_id\":\"s1-llama33-8b-mlx4-32k-r2\",\"fit\":\"yes\",\"decode_tps\":72.6,\"prefill_tps\":911.0,\"ttft_s\":0.40,\"peak_mem_gb\":7.9,\"energy_wh\":0.6,\"model\":\"Llama 3.3 8B\",\"runner\":\"MLX\",\"quant\":\"MLX 4-bit\",\"context_tokens\":32768,\"kind\":\"llm\"}"
  hist "{\"t\":\"$(ts_ago 4500)\",\"type\":\"run_end\",\"run_id\":\"s1-llama33-8b-gguf-q4-32k-r1\",\"fit\":\"yes\",\"decode_tps\":64.3,\"prefill_tps\":780.4,\"ttft_s\":0.52,\"peak_mem_gb\":8.6,\"energy_wh\":0.7,\"model\":\"Llama 3.3 8B\",\"runner\":\"llama.cpp\",\"quant\":\"Q4_K_M\",\"context_tokens\":32768,\"kind\":\"llm\"}"
  hist "{\"t\":\"$(ts_ago 3600)\",\"type\":\"run_end\",\"run_id\":\"s1-qwen27b-mlx4-32k-r1\",\"fit\":\"yes\",\"decode_tps\":11.4,\"prefill_tps\":206.1,\"ttft_s\":1.98,\"peak_mem_gb\":21.2,\"energy_wh\":3.2,\"model\":\"Qwen 3.8 27B\",\"runner\":\"MLX\",\"quant\":\"MLX 4-bit\",\"context_tokens\":32768,\"kind\":\"llm\"}"
  hist "{\"t\":\"$(ts_ago 2400)\",\"type\":\"run_end\",\"run_id\":\"s1-gptoss120b-mlx4-32k-r1\",\"fit\":\"yes\",\"decode_tps\":38.2,\"prefill_tps\":410.7,\"ttft_s\":1.10,\"peak_mem_gb\":66.4,\"energy_wh\":2.4,\"model\":\"gpt-oss 120B\",\"runner\":\"MLX\",\"quant\":\"MLX 4-bit\",\"context_tokens\":32768,\"kind\":\"llm\"}"
  hist "{\"t\":\"$(ts_ago 1500)\",\"type\":\"run_fail\",\"run_id\":\"s1-llama33-70b-mlx8-128k-r1\",\"reason\":\"OOM at 128K\",\"model\":\"Llama 3.3 70B\",\"runner\":\"MLX\",\"quant\":\"MLX 8-bit\",\"context_tokens\":131072}"
fi

# --- runs --------------------------------------------------------------------------------------
llm_run() { # id model arch active quant runner rv ctx load rep of pos total decode prefill model_gb load_s prefill_s decode_s
  local id="$1" model="$2" arch="$3" active="$4" quant="$5" runner="$6" rv="$7" ctx="$8" load="$9"
  local rep="${10}" of="${11}" pos="${12}" total="${13}" dec="${14}" pre="${15}" gb="${16}"
  local load_s="${17}" prefill_s="${18}" decode_s="${19}"
  new_run_dir
  echo "fake_run: [$pos/$total] $model $quant $ctx ($id)"
  emit "{\"t\":\"$(ts)\",\"type\":\"run_start\",\"run_id\":\"$id\",\"set\":\"1\",\"model\":\"$model\",\"arch\":\"$arch\",\"active_params\":$active,\"quant\":\"$quant\",\"runner\":\"$runner\",\"runner_version\":\"$rv\",\"context_tokens\":$ctx,\"load_condition\":\"$load\",\"rep\":$rep,\"of\":$of,\"queue_pos\":$pos,\"queue_total\":$total,\"kind\":\"llm\",\"gpu_budget_gb\":83.49,\"runner_pid\":$$,\"future_field\":{\"ignored\":true}}"
  emit "{\"t\":\"$(ts)\",\"type\":\"phase\",\"phase\":\"loading\"}"
  MEM_TARGET=$(calc "$MEM_BASE + $gb"); GPU=3; GPUW=1.5
  wait_s "$load_s"
  emit "{\"t\":\"$(ts)\",\"type\":\"phase\",\"phase\":\"prefill\"}"
  GPU=88; GPUW=34
  wait_s "$prefill_s"
  local ttft prefill
  ttft=$(jit 1.9 0.1); prefill=$(jit "$pre" 4)
  emit "{\"t\":\"$(ts)\",\"type\":\"progress\",\"tokens\":0,\"prefill_tps\":$prefill,\"ttft_s\":$ttft}"
  emit "{\"t\":\"$(ts)\",\"type\":\"phase\",\"phase\":\"decode\"}"
  GPU=96; GPUW=38
  local ticks i tokens=0 d sum=0
  ticks=$(awk -v s="$decode_s" 'BEGIN { printf "%d", s * 2 }')
  for ((i = 1; i <= ticks; i++)); do
    sleep 0.5; sample
    if [ "$STALL" -gt 0 ] && [ "$N" = 1 ] && [ "$i" = $((ticks / 2)) ]; then
      echo "fake_run: stalling ${STALL}s (no events)"; wait_s "$STALL"
    fi
    d=$(jit "$dec" "$(calc "$dec * 0.04")")
    tokens=$(awk -v t="$tokens" -v d="$d" 'BEGIN { printf "%d", t + d / 2 }')
    emit "{\"t\":\"$(ts)\",\"type\":\"progress\",\"tokens\":$tokens,\"decode_tps\":$d,\"prefill_tps\":$prefill,\"ttft_s\":$ttft}"
  done
  emit "{\"t\":\"$(ts)\",\"type\":\"phase\",\"phase\":\"done\"}"
  local end_line
  end_line="{\"t\":\"$(ts)\",\"type\":\"run_end\",\"run_id\":\"$id\",\"fit\":\"yes\",\"decode_tps\":$(jit "$dec" 0.2),\"prefill_tps\":$prefill,\"ttft_s\":$ttft,\"peak_mem_gb\":$(calc "$gb + 0.3"),\"energy_wh\":$(jit 2.5 0.5),\"model\":\"$model\",\"runner\":\"$runner\",\"quant\":\"$quant\",\"context_tokens\":$ctx,\"kind\":\"llm\",\"wall_s\":null,\"s_per_step\":null}"
  emit "$end_line"; hist "$end_line"
  MEM_TARGET=$MEM_BASE; GPU=2; GPUW=0.8
}

fail_run() { # id model arch active quant runner rv ctx pos total
  local id="$1" model="$2" arch="$3" active="$4" quant="$5" runner="$6" rv="$7" ctx="$8" pos="$9" total="${10}"
  new_run_dir
  echo "fake_run: [$pos/$total] $model $quant $ctx — will fail ($id)"
  emit "{\"t\":\"$(ts)\",\"type\":\"run_start\",\"run_id\":\"$id\",\"set\":\"1\",\"model\":\"$model\",\"arch\":\"$arch\",\"active_params\":$active,\"quant\":\"$quant\",\"runner\":\"$runner\",\"runner_version\":\"$rv\",\"context_tokens\":$ctx,\"load_condition\":\"clean\",\"rep\":1,\"of\":3,\"queue_pos\":$pos,\"queue_total\":$total,\"kind\":\"llm\",\"gpu_budget_gb\":83.49,\"runner_pid\":$$}"
  emit "{\"t\":\"$(ts)\",\"type\":\"phase\",\"phase\":\"loading\"}"
  MEM_TARGET=78; GPU=3; GPUW=1.5
  wait_s 6
  emit "{\"t\":\"$(ts)\",\"type\":\"phase\",\"phase\":\"prefill\"}"
  MEM_TARGET=94; GPU=90; GPUW=36; SWAP=3.4; DISK=138.0
  wait_s 5
  local line="{\"t\":\"$(ts)\",\"type\":\"run_fail\",\"run_id\":\"$id\",\"reason\":\"OOM at 128K\",\"model\":\"$model\",\"runner\":\"$runner\",\"quant\":\"$quant\",\"context_tokens\":$ctx}"
  emit "$line"; hist "$line"
  MEM_TARGET=$MEM_BASE; GPU=2; GPUW=0.8; SWAP=0.9; DISK=72.0
}

media_run() { # id pos total
  local id="$1" pos="$2" total="$3" steps=8 clips=2 sps=2.5
  new_run_dir
  echo "fake_run: [$pos/$total] media run ($id)"
  emit "{\"t\":\"$(ts)\",\"type\":\"run_start\",\"run_id\":\"$id\",\"set\":\"3\",\"model\":\"Wan 2.2 TI2V 5B\",\"arch\":\"dense\",\"active_params\":null,\"quant\":\"bf16\",\"runner\":\"MLX\",\"runner_version\":\"mlx-video 0.4.1\",\"context_tokens\":null,\"load_condition\":\"clean\",\"rep\":1,\"of\":1,\"queue_pos\":$pos,\"queue_total\":$total,\"kind\":\"media\",\"gpu_budget_gb\":83.49,\"width\":1280,\"height\":704,\"seconds\":5,\"steps\":$steps,\"lora\":null,\"clip\":1,\"clips\":$clips,\"runner_pid\":$$}"
  emit "{\"t\":\"$(ts)\",\"type\":\"phase\",\"phase\":\"loading\"}"
  MEM_TARGET=48; GPU=3; GPUW=1.5
  wait_s 5
  emit "{\"t\":\"$(ts)\",\"type\":\"phase\",\"phase\":\"rendering\"}"
  GPU=99; GPUW=42
  local c s eta
  for ((c = 1; c <= clips; c++)); do
    for ((s = 1; s <= steps; s++)); do
      wait_s "$sps"
      eta=$(calc "($steps - $s) * $sps")
      emit "{\"t\":\"$(ts)\",\"type\":\"media_progress\",\"step\":$s,\"steps\":$steps,\"s_per_step\":$(jit $sps 0.15),\"eta_s\":$eta,\"clip\":$c,\"clips\":$clips}"
    done
  done
  local line="{\"t\":\"$(ts)\",\"type\":\"run_end\",\"run_id\":\"$id\",\"fit\":\"yes\",\"decode_tps\":null,\"prefill_tps\":null,\"ttft_s\":null,\"peak_mem_gb\":26.4,\"energy_wh\":1.9,\"model\":\"Wan 2.2 TI2V 5B\",\"runner\":\"MLX\",\"quant\":\"bf16\",\"context_tokens\":null,\"kind\":\"media\",\"wall_s\":$(calc "$steps * $clips * $sps + 5"),\"s_per_step\":$sps}"
  emit "$line"; hist "$line"
  MEM_TARGET=$MEM_BASE; GPU=2; GPUW=0.8
}

R=$RANDOM
llm_run "fake$R-qwen27b-mlx4-32k-r2" "Qwen 3.8 27B" dense null "MLX 4-bit" MLX "mlx-lm 0.31.3" 32768 clean 2 3 14 96 11.8 212.4 21.0 6 3 36
wait_s 4
fail_run "fake$R-qwen35a3b-q4-128k-r1" "Qwen 3.8 35B-A3B" moe 3 "Q4_K_M" "llama.cpp" "llama.cpp b6120" 131072 15 96
wait_s 3
media_run "fake$R-wan22-5b-720p-r1" 16 96
wait_s 3
llm_run "fake$R-qwen30a3b-mlx4-32k-r1" "Qwen 3.8 30B-A3B" moe 3 "MLX 4-bit" MLX "mlx-lm 0.31.3" 32768 everyday 1 3 17 96 54.6 1180.0 17.5 4 2 24
emit "{\"t\":\"$(ts)\",\"type\":\"queue_end\"}"
echo "fake_run: queue_end; holding 8 s"
wait_s 8
