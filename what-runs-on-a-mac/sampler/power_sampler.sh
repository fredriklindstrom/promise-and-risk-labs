#!/bin/bash -p
# power_sampler.sh — GPU / CPU / package power for the benchmark runs.
#
# Run it yourself in a Terminal window, with sudo (powermetrics needs root), from the project folder:
#     sudo sampler/power_sampler.sh
# Stop with Ctrl-C.
#
# Optional since 2026-10-24: Bench Monitor and `bench-power --ioreport` sample power without root (BenchProbe.
# EnergySampler). Use this script only for powermetrics' extra fields (thermal pressure level, CPU cluster clocks);
# Bench Monitor stops writing while it runs. `bash -p` ignores BASH_ENV, inherited functions and SHELLOPTS.
# The parser and its one-off build come from $PROJECT/sampler and run as you, never as root.
#
# Only `powermetrics` (and `script`, with POWER_PTY=1) runs as root. Its plist stream is piped into
# `bench-power`, which runs as you (via `sudo -u $SUDO_USER`), so power.jsonl / power_all.jsonl are
# owned by you, not root. A one-off `swift build` of bench-power also runs as you.
# Every command is called by absolute path with a fixed PATH, and nothing is passed through a shell
# string, so the caller's environment cannot change what runs as root.
#
# Writes {"t":..,"type":"power","pkg_power_w":..,"cpu_power_w":..,"gpu_power_w":..,"ane_power_w":..}
#   to  runs/current/power.jsonl   (symlink re-resolved for every sample; skipped if absent)
#   and runs/power_all.jsonl       (everything, across runs)
# bench-sampler picks up the newest power line (if < 3 s old) and copies it into samples.jsonl.
#
# Options (environment):
#   BENCH_PROJECT=/path   project root (default: the folder above this script)
#   POWER_INTERVAL_MS=1000
#   POWER_PTY=1           (pass as: sudo POWER_PTY=1 sampler/power_sampler.sh) run powermetrics under a pseudo-terminal,
#                         in case its output turns out to be block-buffered when piped (symptom: power.jsonl
#                         updates in bursts every ~10-30 s instead of every second). Off by default.
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
unset CDPATH
set -euo pipefail

HERE="$(cd -P -- "$(/usr/bin/dirname -- "$0")" && pwd -P)"
PROJECT="${BENCH_PROJECT:-$(cd -P -- "$HERE/.." && pwd -P)}"
INTERVAL="${POWER_INTERVAL_MS:-1000}"
case "$PROJECT" in /*) ;; *) echo "power_sampler.sh: BENCH_PROJECT must be an absolute path" >&2; exit 1 ;; esac
SAMPLER_DIR="$PROJECT/sampler"                 # not $HERE: the root-owned copy lives elsewhere
PARSER="$SAMPLER_DIR/.build/release/bench-power"

if [ "$(/usr/bin/id -u)" -ne 0 ]; then
  echo "power_sampler.sh: powermetrics needs root. Run:  sudo $0" >&2
  exit 1
fi
RUN_AS="${SUDO_USER:-}"
if [ -z "$RUN_AS" ] || [ "$RUN_AS" = "root" ] || ! /usr/bin/id -u -- "$RUN_AS" >/dev/null 2>&1; then
  echo "power_sampler.sh: run this via sudo from your own account (SUDO_USER is not set)." >&2
  exit 1
fi
case "$INTERVAL" in
  ''|*[!0-9]*) echo "power_sampler.sh: POWER_INTERVAL_MS must be a whole number of milliseconds" >&2; exit 1 ;;
esac
if [ ! -d "$PROJECT/runs" ]; then
  echo "power_sampler.sh: $PROJECT/runs does not exist — wrong project folder? (set BENCH_PROJECT)" >&2
  exit 1
fi
if [ ! -x "$PARSER" ]; then
  echo "power_sampler.sh: building bench-power as $RUN_AS (one-off)…" >&2
  /usr/bin/sudo -u "$RUN_AS" -H /usr/bin/swift build --package-path "$SAMPLER_DIR" -c release --product bench-power >&2
fi

echo "power_sampler.sh: sampling every ${INTERVAL} ms → $PROJECT/runs/current/power.jsonl and runs/power_all.jsonl (Ctrl-C to stop)" >&2

PM=(/usr/bin/powermetrics --samplers cpu_power,gpu_power,thermal -i "$INTERVAL" -f plist)   # thermal: pressure level


if [ "${POWER_PTY:-0}" = "1" ]; then
  # script(1) gives powermetrics a pty so stdio is line/flush-buffered. The parser tolerates CRLF.
  /usr/bin/script -q /dev/null "${PM[@]}" | /usr/bin/sudo -u "$RUN_AS" -- "$PARSER" --project "$PROJECT"
else
  "${PM[@]}" | /usr/bin/sudo -u "$RUN_AS" -- "$PARSER" --project "$PROJECT"
fi
