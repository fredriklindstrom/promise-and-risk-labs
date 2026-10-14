#!/bin/bash
# Preflight: inventory only. Changes nothing, installs nothing, needs no sudo.
# Usage: scripts/preflight.sh   (writes results/preflight_<timestamp>.txt and prints it)
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/results"
OUT="$ROOT/results/preflight_$(date +%Y%m%d-%H%M%S).txt"
{
echo "# preflight  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo; echo "## Machine"
sw_vers | sed 's/^/  /'
if [ -x "$ROOT/sampler/.build/release/bench-sampler" ]; then
  echo "  $("$ROOT/sampler/.build/release/bench-sampler" --machine)"
else
  echo "  chip: $(sysctl -n machdep.cpu.brand_string)  memory: $(( $(sysctl -n hw.memsize) / 1073741824 )) GB  (build the sampler for the full reading: scripts/setup.sh)"
fi
echo "  iogpu.wired_limit_mb: $(sysctl -n iogpu.wired_limit_mb)   (0 = macOS default; the harness never changes it)"
echo "  memory pressure level: $(sysctl -n kern.memorystatus_vm_pressure_level)  (1 normal, 2 warn, 4 critical)"
echo "  swap: $(sysctl -n vm.swapusage)"
echo; echo "## Disk"
df -h /System/Volumes/Data | sed 's/^/  /'
echo; echo "## Runners"
for b in llama-server uv swift; do printf "  %-13s %s\n" "$b" "$(command -v $b || echo 'not installed')"; done
printf "  %-13s %s\n" ollama "$( [ -x /Applications/Ollama.app/Contents/Resources/ollama ] && /Applications/Ollama.app/Contents/Resources/ollama --version 2>&1 | tail -1 || echo 'not installed')"
echo; echo "## Python environments in this folder"
for v in "$ROOT"/.venv "$ROOT"/.venv-*; do
  [ -x "$v/bin/python" ] || continue
  echo "  $(basename "$v"): $("$v/bin/python" -I -c "import sys, importlib.metadata as m
out=[sys.version.split()[0]]
for p in ['mlx','mlx-lm','mlx-audio','torch','huggingface_hub','transformers']:
    try: out.append(p+' '+m.version(p))
    except Exception: pass
print(', '.join(out))")"
done
echo; echo "## Model servers already running (stop them before measuring)"
/usr/bin/pgrep -fl "mlx_lm.server|llama-server|ollama|omlx serve|mtplx serve|mlx-serve" | cut -c1-160 | sed 's/^/  /' || true
echo; echo "## Load"
echo "  $(uptime)"
} | tee "$OUT"
echo; echo "written: $OUT"
