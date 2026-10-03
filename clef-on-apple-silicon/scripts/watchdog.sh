#!/bin/bash
# Kill the benchmark if swap grows > 4 GB over baseline or free memory drops below 8%.
PAT="$1"; SWAPMB=${2:-4096}; FREEPCT=${3:-8}; LOG="$(dirname "$0")/../runs/logs/watchdog.log"
swap_mb() { sysctl -n vm.swapusage | sed -E 's/.*used = ([0-9.]+)M.*/\1/' | cut -d. -f1; }
BASE=$(swap_mb); echo "$(date +%T) watchdog start pattern=$PAT baseline_swap=${BASE}MB" >> $LOG
while pgrep -f "$PAT" >/dev/null; do
  S=$(swap_mb); F=$(memory_pressure | awk '/free percentage/{gsub("%","",$NF);print $NF}')
  if [ $((S-BASE)) -gt $SWAPMB ] || [ "${F:-100}" -lt $FREEPCT ]; then
    echo "$(date +%T) KILL swap=${S}MB (+$((S-BASE))) free=${F}%" >> $LOG; pkill -9 -f "$PAT"; break
  fi
  sleep 3
done
echo "$(date +%T) watchdog end" >> $LOG
