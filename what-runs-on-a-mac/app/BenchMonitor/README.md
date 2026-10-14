# Bench Monitor

A single-window dashboard for watching a local-model benchmark run on an Apple-silicon Mac. It
follows the benchmark harness through `runs/current`, shows what is being tested, how fast it
runs and what it does to memory, the GPU, power and temperature, and keeps a running results
table. It is built to be filmed: large type, no scrolling, nothing smaller than 14 pt.

- Native SwiftUI, macOS 14 or later, Apple frameworks only.
- The only dependency is the sampler package next to it (`../../sampler`, product `BenchProbe`),
  referenced by path. Nothing is fetched from the network at build time or at run time.
- Runs as you; the only administrator prompt is the GPU Memory menu's, and only when you pick a preset. Its only writes are its own power lines (`runs/power_all.jsonl`, `runs/current/power.jsonl`;
  see the Power menu) and snapshots when you ask for one. It never changes what the harness writes.

## Build from source

Requirements: an Apple-silicon Mac, macOS 14+, Xcode or the Command Line Tools (Swift 5.10+).

```bash
# from the project root (the folder that contains bench/harness.py)
cd sampler && swift build -c release && cd ..     # bench-sampler, bench-power
cd app/BenchMonitor
./build.sh            # → build/Bench Monitor.app (ad-hoc signed)
./build.sh --open     # build and launch
```

**App icon (optional).** Put a 1024 × 1024 PNG at `Resources/AppIcon-1024.png` before `./build.sh` and it becomes the app icon (`sips` and `iconutil` make every size macOS needs). `tools/make_icon.swift` turns any square artwork into one: `swift tools/make_icon.swift artwork.png Resources/AppIcon-1024.png`. Without it the app builds with the generic icon.

The harness starts the sampler itself (`sampler/.build/release/bench-sampler --root <project>`).
Bench Monitor only reads what the harness and the sampler write.

## Run

Open `build/Bench Monitor.app`. With no settings it finds its project on its own: it walks up from
the app bundle to the nearest folder that contains `bench/harness.py` (the app is built into
`<project>/app/BenchMonitor/build/`). If the app has been moved somewhere else, set the project
folder in Settings.

To pass environment variables, run the binary inside the bundle:

```bash
BENCH_HISTORY=history.fake.jsonl "build/Bench Monitor.app/Contents/MacOS/BenchMonitor"
```

### Settings (⌘,)

| Setting (defaults key) | Default | Effect |
|---|---|---|
| Project folder (`projectDir`) | empty = discovered (see above) | reads `<project>/runs/current/events.jsonl`, `…/samples.jsonl` and `<project>/runs/<history file>` |
| History file (`historyFile`) | `history.jsonl` | file name inside `runs/` (or an absolute path) for the results table; several, comma-separated, are merged |
| Extra volume (`extraVolume`) | empty = none | a second volume, e.g. an external drive with models. While it is mounted the footer shows "*name* free N GB" |
| Footer note (`footerNote`) | empty = none | a short note shown in amber in the footer, e.g. a disclaimer about the test machine |
| Allow remote control (`remoteControl`) | off | accept the distributed notifications described under Controls |

Project folder, history file and extra volume take effect when you press **Apply**; footer note
and remote control take effect at once. Settings are stored in the app's preferences
(domain `io.github.fredriklindstrom.benchmonitor`), so they can also be set from a script:

```bash
defaults write io.github.fredriklindstrom.benchmonitor footerNote "Everyday machine, not a dedicated AI node"
defaults write io.github.fredriklindstrom.benchmonitor extraVolume "/Volumes/Models"
```

### Environment overrides (per launch)

| Variable | Effect |
|---|---|
| `BENCH_PROJECT=/path` | project folder (wins over Settings and discovery) |
| `BENCH_HISTORY=history.fake.jsonl` | results table from this file instead of `history.jsonl` |
| `BENCH_EXTRA_VOLUME=/Volumes/Name` | extra volume for the footer (wins over Settings) |
| `BENCH_STALE_S=10` | seconds of event silence before "No data" (default 60) |
| `BENCH_PRESENTATION=1` | start in Presentation mode |
| `BENCH_COMPARE="Jais 3 32B; Qwen 3.8 27B · MLX"` | start in Compare with this selection (not saved; format in section 5) |
| `BENCH_SNAPSHOT_DIR=/path` | where snapshots are written (default `<project>/app/BenchMonitor/screenshots/`) |
| `BENCH_REMOTE=1` | enable remote control for this launch |
| `BENCH_NO_PRIVATE_SENSORS=1` | don't read on-chip temperatures (see Temperatures below) |

### Controls

- **View menu:** **Dashboard** (⌘1) and **Compare Models** (⌘2), the one on screen ticked; **Switch View**
  (⌘K) flips between them; **Presentation** (⌘P, dashboard only).
- **Presentation** (View menu, toolbar button or ⌘P): hides the results table and enlarges the speed numbers.
- **Compare** (View menu, toolbar button, ⌘2 or ⌘K): up to five models side by side instead of the speed,
  system and results panels (the header and footer stay live). Pick them with **Models** in the toolbar;
  see section 5 below.
- **Save Snapshot PNG** (⌘⇧S): renders the dashboard offscreen to a PNG in the screenshots folder.
  No Screen Recording permission is needed.
- **Remote control** (opt-in, off by default): with the Settings toggle on, or `BENCH_REMOTE=1`,
  the app listens for two distributed notifications, for example from a Stream Deck or a script:

  | Name | Object |
  |---|---|
  | `io.github.fredriklindstrom.benchmonitor.presentation` | `on`, `off` or `toggle` |
  | `io.github.fredriklindstrom.benchmonitor.snapshot` | PNG file name |
  | `io.github.fredriklindstrom.benchmonitor.compare` | `on`, `off`, `toggle`, or `models:<selection>` (at most 512 characters; also turns Compare on) |

  ```bash
  osascript -l JavaScript -e "ObjC.import('Foundation'); \$.NSDistributedNotificationCenter.defaultCenter.postNotificationNameObjectUserInfoDeliverImmediately('io.github.fredriklindstrom.benchmonitor.snapshot', 'decode-live', \$(), true)"
  ```

  Any process running as your user can post these notifications, which is why they are off by
  default. A snapshot name must be 1–64 characters from `A–Z a–z 0–9 . _ -` and must not start
  with a dot; `.png` is appended if missing, anything else is rejected. Snapshots are only ever
  created in the screenshots folder, never overwrite an existing file and never follow a symlink
  (a taken name gets `-2`, `-3`, … appended). At most one remote snapshot per second is taken.

## What is on screen, and where each number comes from

### 1. Now testing (header)

All from the latest `run_start` in `runs/current/events.jsonl`.

| Shown | Source |
|---|---|
| Model name | `model` |
| Dense / "MoE · 3B active" | `arch`, `active_params` (a number below 1000 is read as billions) |
| Runner and version | `runner`, `runner_version` |
| Quantization | `quant` |
| "32K context" | `context_tokens` (multiples of 1024 → K) |
| Clean / Everyday / Concurrent | `load_condition` (anything other than Clean is amber) |
| Run x of N · rep r/n | `queue_pos`/`queue_total`, `rep`/`of` ("warm-up" when the run is a warm-up) |
| Elapsed | now minus the `run_start` timestamp. Freezes as "Took" at `run_end`/`run_fail` |
| Queue ETA | mean wall time of the runs completed **since the app was launched** × the runs left, plus what remains of the current one. "after first run" until one run has completed |
| Badge | LIVE · phase (from `phase` events), NO DATA, IDLE, RECORDED, NO ACTIVE RUN |

**No data:** during a run, if `events.jsonl` has not grown for 60 s the badge turns amber
("NO DATA · 73 s") and the speed label says "last values (no data)". For media runs the threshold
is stretched to 1.5 × the last seconds-per-step + 15 s, because one diffusion step can take minutes.
During **prefill** the harness sends nothing until the first token (a 128K prefill on an M2 Max takes
~20 minutes), so there a busy GPU (≥ 20 % in a sample under 10 s old) also counts as a sign of life:
the badge stays "LIVE · PREFILL" while the GPU works, and goes amber 60 s after both the events and
the GPU have gone quiet.

**run_fail:** the header flashes amber once, then keeps an amber tint until the next run starts.
A memory failure (`did not fit: …`, OOM, watchdog) is shown as **"Result · does not fit"**: a
finding, not a crash. Reasons starting `error:` are shown as "Run error" and kept out of the fit
counts. `skipped:` rows don't flash.

### 2. Speed

| Shown | Source |
|---|---|
| Decode tok/s | `progress.decode_tps` |
| Prefill tok/s | `progress.prefill_tps` |
| Time to first token | `progress.ttft_s` |
| Small line under each | running average for this model + runner + quant + context over completed runs |
| Media: Step x / N, seconds per step | `media_progress.step`/`steps`, `s_per_step` |
| Media: ETA this clip | `media_progress.eta_s`, counted down between events |
| Media: Clips done | `clip − 1` of `clips` |

Between runs, and while a new run is loading, the last completed run stays on screen (dimmed,
labelled "Last run"). A failed last run shows the finding instead.

### 3. System (last 2 minutes)

From `runs/current/samples.jsonl` (written once a second by `bench-sampler`). If no sample has
arrived for 5 s the app reads the same values itself through `BenchProbe`, and the footer says
"samples: direct read".

| Shown | Meaning and source |
|---|---|
| Memory used (amber line) | `mem_used_gb` = app memory + wired + compressed (formula below) |
| Physical RAM line | `hw.memsize` of this Mac |
| GPU budget line | `run_start.gpu_budget_gb`; before any run, Metal's `recommendedMaxWorkingSetSize` (else ¾ of RAM) |
| Background tint | memory pressure: green normal, amber warn, red critical |
| Swap | `swap_gb` (amber above 1 GB) |
| Disk free | free space on the internal data volume. Red below the harness's disk floor, amber below its warn level (both from the manifest's `disk_guard`, sent in `run_start`); before any run, floor = 5 % of the disk (at most 150 GB) and warn = floor × 5/3 |
| GPU % busy (cyan) | `gpu_util_pct` |
| GPU power (white line, right axis) and Package | `gpu_power_w`, `pkg_power_w` from the built-in power sampler (on by default; "power: not sampling" when it is switched off). The axis is capped at 1000 W |
| Thermal | macOS thermal state (nominal, fair, serious, critical) plus the hottest on-chip die temperature |
| Clock | GPU clock: residency-weighted average over the GPU's active time, from the built-in sampler (or powermetrics, if that is running) |
| Peak line | the highest memory, power and temperature, the worst thermal state and the lowest GPU clock seen during the current run, held after it ends |
| Footer | machine (detected at launch), optional footer note, optional extra volume, sample source, current run folder |

How each reading is taken (all unprivileged):

- **Memory used** = `(internal_page_count − purgeable_count) + wire_count + compressor_page_count`,
  × page size, from `host_statistics64(HOST_VM_INFO64)`. This is the usual reconstruction of
  Activity Monitor's "Memory Used"; Apple does not publish the exact formula. `top`'s "PhysMem
  used" is larger because it also counts file cache.
- **Pressure:** `sysctl kern.memorystatus_vm_pressure_level` (1 normal, 2 warn, 4 critical).
- **Swap:** `sysctl vm.swapusage`.
- **GPU %:** IOKit `IOAccelerator` → `PerformanceStatistics` → `Device Utilization %`.
- **Disk free:** `statfs` `f_bavail × f_bsize`, so space macOS *could* purge is not counted (the
  conservative figure, and cheap enough to read every second).
- **Thermal state:** `ProcessInfo.thermalState` (public API). "Fair" and above mean macOS is
  already slowing the machine down to shed heat.
- **Temperatures:** read through a **private, undocumented, read-only** Apple API (the IOHID
  event-system functions that menu-bar monitors such as Stats and iStat Menus use), looked up at
  run time with `dlsym`. It is only used on macOS 14 and later; if the functions are missing every
  temperature is simply null, and `BENCH_NO_PRIVATE_SENSORS=1` turns it off. Sensors named
  `tdie` are the SoC die (the number shown); `NAND*` is the SSD. `bench-sampler --sensors` lists
  every sensor your Mac exposes.
- **Power and GPU clock:** from Apple silicon's energy counters through **IOReport**, the private, undocumented,
  read-only library that `powermetrics` itself reads (macmon and mactop use it the same way), looked up at run time
  with `dlsym` (`BenchProbe/EnergySampler.swift`). No root. CPU = channel `CPU Energy`, GPU = `GPU Energy`, ANE =
  `ANE*`, DRAM = `DRAM*`; package = CPU + GPU + ANE, the same definition as powermetrics' combined power. GPU clock
  and active share from `GPU Stats/GPUPH` residencies and the `pmgr` `voltage-states9` frequency table. Same
  opt-out as the temperatures (`BENCH_NO_PRIVATE_SENSORS=1`); macOS 14 and later only. Checked 2026-10-24 on the
  M2 Max: idle GPU 0.05 W; an 8192² matmul loop read GPU 62.5 W, 100 % active, 1,398 MHz.
- **Runner memory:** pid from `runs/current/runner.pid` (or the last `run_start.runner_pid`), via
  `proc_pid_rusage` (RSS and phys_footprint).

### 4. Results so far

Built from the history file at launch, then kept current from both the history file and live
`run_end`/`run_fail` events (duplicates dropped: the harness writes each result to both).

- **Left:** every run, newest first: model, runner, quant, context, decode tok/s (s/step for
  media), peak memory, peak power, fit. The fastest decode row per model is marked "▲ fastest".
  Warm-ups and dry runs are listed and tagged but not counted.
- **Right:** running average per model + runner + quant + context. Needle-test runs (64-token answers) are
  averaged on their own row, tagged "needle", so they don't pull down the decode average of the speed runs.
- **Summary line:** runs, fit, did not fit, errors, mean decode, mean peak, fastest run.

There is no scrolling: the table shows as many of the newest rows as fit and an "+ n earlier"
count. The app keeps the newest 5,000 rows in memory; the summary and averages count every run.

### 4b. Agent-loop cache test (only while `cache_turn` events arrive)

| Shown | Source |
|---|---|
| Header line "B · Broken at the top · Turn 4 of 10 · cached 0 of 45,120 tokens" (replaces the context chip; amber when a turn after the first got no cache hit) | latest `cache_turn`: `condition`, `turn`, `turns_total`, `cached_tokens`, `context_tokens` ("…" until the turn finishes) |
| Bar row under the speed numbers: time to first token per turn of the current run | `cache_turn.ttft_s` of each finished turn. Scale = the longest TTFT seen this session for the same model + runner, so condition A filmed after B is drawn on B's scale. Bars after turn 1 that reach half the scale are amber |

The time-to-first-token big number is unchanged.

### 5. Compare (⌘2)

Up to five **contenders** side by side. A contender is one model + runner + quant, the unit the harness
measures, so "Qwen 3.8 27B · MLX · MLX 4-bit" and "Qwen 3.8 27B · llama.cpp · GGUF Q4_K_M" are two
contenders. Numbers come from the same running averages as the right-hand table in section 4 (timed
reps only; warm-ups, dry runs and errors are left out). Language-model runs only: media runs have no
context axis.

| Shown | Source |
|---|---|
| Column header | model; runner · quant; Dense / MoE · N active (`arch`, `active_params` of the result rows); "fits up to" = largest context with a completed run; "✕ 128K" = smallest context that only ever failed |
| One row per context | the union of the contexts any picked contender ran, so rows line up across columns. "not run" = no result at that context; "did not fit" = only failures there |
| Big numbers | mean decode tok/s (cyan) and mean peak memory (amber) |
| Bar | decode as a share of the fastest picked contender **at that context**; "▲ fastest" marks it (only when two or more ran that context) |
| Detail lines | mean prefill tok/s, mean TTFT (m:ss above a minute), mean peak package power and mean energy per run (Wh, whole request). Power and energy only appear for runs recorded while the power sampler ran |

**The live cell.** While a language-model run is in progress and its contender is on screen, that
contender's cell for the running context is outlined in cyan and shows, instead of the averages:
a "LIVE · PREFILL / DECODE" badge (amber "NO DATA" when the run goes stale, see section 1); the live decode
tok/s from the harness's progress events, or during prefill the elapsed time (prefill sends no numbers
until the first token); the live package power from the newest sample (under 10 s old; "—" without the
power sampler); a progress meter; and this cell's averages underneath ("first run at this context" when
there are none). The meter during decode is tokens generated ÷ 512 (64 for needle runs), exact. During
prefill it is elapsed ÷ the expected time to first token: this cell's average TTFT when it has one,
otherwise **an estimate** ("est."): prompt length (context − 576) over a prefill rate extrapolated from the
two largest smaller contexts this contender ran, with their slowdown per doubling of context. The estimate
is capped at 99 % and drawn dimmer; prefill slows down faster than that extrapolation for some
architectures, so treat it as a rough guide.

**Picking contenders.** With nothing picked (**Auto**), the five most recently run are shown. In the
**Models** menu, ticking a model while on Auto starts a new selection with it; after that each click
adds or removes one (at most five). The selection is saved in the preferences key `compareModels`, so it
can also be set from a script:

```bash
defaults write io.github.fredriklindstrom.benchmonitor compareModels "Jais 3 32B; Jais 3 MoVA; Qwen 3.8 27B · MLX · MLX 4-bit; Llama 3.3 70B Instruct · MLX"
```

The format: entries separated by `;` (or new lines). An entry is a full label ("Model · Runner · Quant",
case-insensitive) or else any part of one; the newest contender matching it that is not already picked
wins. Entries that match nothing are skipped. The setting is read at launch; while the app runs, use the
menu or the `compare` remote-control notification with `models:<selection>`.

**Several history files.** The history setting (and `BENCH_HISTORY`) takes a comma-separated list, merged
into one results table, so a separate campaign can sit next to an earlier one without writing into its
file: `BENCH_HISTORY="history.jsonl, history.jais3.jsonl"`. A run present in more than one file is
counted once.

## The sampler (`../../sampler`)

`bench-sampler` writes one JSON line per second to `runs/current/samples.jsonl`, re-resolving the
symlink on every tick so each run gets its own file. It runs unprivileged at nice 10.

```
bench-sampler [--project DIR | --root DIR] [--out PATH] [--interval S] [--stdout] [--once]
bench-sampler --machine     # what this Mac is, as JSON (the same reading the app uses)
bench-sampler --sensors     # every temperature sensor and how it is classified
```

The project defaults to `$BENCH_PROJECT`, else the project the binary was built in, else the
current directory. `BENCH_EXTRA_VOLUME=/Volumes/Name` adds that volume's free space as
`disk_free_gb_external` (null when unset or not mounted).

### Power without the app: `bench-power --ioreport` (no root)

```bash
sampler/.build/release/bench-power --ioreport --project "$PWD"     # Ctrl-C to stop; --watch-pid N stops with process N
```

Writes the same lines as the app (`"source":"ioreport"`) to `runs/current/power.jsonl` and `runs/power_all.jsonl`.

### Power with powermetrics: `sampler/power_sampler.sh` (optional, with sudo)

Only needed for powermetrics' extra fields (thermal pressure level, CPU cluster clocks).

```bash
sudo sampler/power_sampler.sh      # from the project root; Ctrl-C to stop
```

Only `/usr/bin/powermetrics --samplers cpu_power,gpu_power,thermal -f plist` runs as root. Its
output (one plist per sample, separated by NUL bytes) is piped into `bench-power`, which runs as
you (`sudo -u $SUDO_USER`), so the output files are owned by you. The script uses a fixed `PATH`
and absolute command paths. `bench-power` appends power, thermal pressure and clock lines to
`runs/current/power.jsonl` and `runs/power_all.jsonl`, and `bench-sampler` copies the newest one
(if under 3 s old) into its samples. A stream with 16 MB and no separator is dropped and counted
as unparseable.

If `power.jsonl` updates in bursts rather than every second, powermetrics is block-buffering
into the pipe; run it as `sudo POWER_PTY=1 sampler/power_sampler.sh`.

To test the parser without root:
`{ cat sampler/tests/powermetrics_sample1.plist; printf '\0'; } | sampler/.build/release/bench-power --project /tmp/x --stdout`
(`/tmp/x/runs` must exist).

## Testing without the harness: `tools/fake_run.sh`

```bash
# Terminal 1: sampler.  Terminal 2: app on the fake history.  Terminal 3: the fake harness.
sampler/.build/release/bench-sampler
BENCH_HISTORY=history.fake.jsonl BENCH_STALE_S=10 "app/BenchMonitor/build/Bench Monitor.app/Contents/MacOS/BenchMonitor"
app/BenchMonitor/tools/fake_run.sh --stall 20
```

About 2.5 minutes: an LLM run, a 128K run that ends in `run_fail "OOM at 128K"`, a two-clip media
run, a second LLM run, then `queue_end`. Results go to `runs/history.fake.jsonl`, never the real
history. On exit (including Ctrl-C) it deletes the `runs/fake-*` folders it created and restores
`runs/current`. Options: `--stall N` (go silent mid-decode), `--synthetic-samples` (fake memory,
pressure, power and disk; don't run `bench-sampler` at the same time), `--project DIR`,
`--clean-history`.

## Privacy

Bench Monitor and the sampler make no network connections and collect no telemetry. They read
system counters and files in the project folder, and write only: the app's own preferences,
snapshot PNGs in the screenshots folder (when you ask for one), and — for the sampler and
`bench-power` — the JSON lines described above.

## Signing and distribution

`build.sh` signs the app ad hoc, which is enough to run it on the Mac that built it. Before
distributing a binary, sign it with a Developer ID certificate and notarize it; otherwise
Gatekeeper will block it on other Macs. Building from source avoids this.

## Cost

Measured on an Apple-silicon Mac with `ps -o time` (cumulative CPU time over 30 s, idle, with a
results table of 235 runs on screen):

| Process | CPU (one core) | Memory |
|---|---|---|
| Bench Monitor (reading the system itself, no sampler writing) | ≈ 1.6 % | ≈ 200 MB RSS |
| bench-sampler (including temperatures) | ≈ 0.5 % | ≈ 11 MB RSS |

The display updates once a second. Charts are Canvas paths with no animation, the static grid is
a separate layer that isn't redrawn, and tailed files stay open between ticks.

## License

Apache License 2.0.

## Units (2026-10-22)

Every memory figure in the app is binary GB (2^30 bytes), the unit Activity Monitor and oMLX call "GB" and the one
"96 GB" refers to. The harness writes `peak_mem_gb` and `gpu_budget_gb` in 10^9-byte GB, so the app converts both when
it reads them (`ThisMac.decimalToChartGB`): the Studio's GPU budget shows as 77.8 GB, not 83.5, and a 44.8 GB peak in
`results/summary.csv` shows as 41.7 GB. `results/report.md` stays in 10^9-byte GB. Disk figures stay decimal, as Finder
shows them. Failure reasons are rewritten into plain words by `ReasonText.swift`.

## GPU Memory menu (2026-10-22)

Sets the kernel's GPU memory limit (`iogpu.wired_limit_mb`). Four choices: **macOS Default** (0) and three presets
defined by what macOS keeps: **Aggressive** 10% of RAM, **DangerAggressive** a fixed 5 GB, **SuperAggressive** the
midpoint of the two (2026-10-22). GPU / kept for macOS:

| RAM | Aggressive | SuperAggressive | DangerAggressive |
|---|---|---|---|
| 16 GB | greyed out | greyed out | greyed out |
| 96 GB | 86.4 / 9.6 GB | 88.7 / 7.3 GB | 91.0 / 5.0 GB |
| 512 GB | 460.8 / 51.2 GB | 483.9 / 28.1 GB | 507.0 / 5.0 GB |

A preset is greyed out when it would leave macOS under 4 GB or would not raise the limit above the default
(on the 16 GB Air, DangerAggressive's 11 GB is below the 11.8 GB default).

Every change shows a warning that it may take down the machine and asks for an administrator password through
macOS's own dialog (`do shell script … with administrator privileges`); the app never sees the password. The command
is built from an integer only, checked against 0 or (macOS default, RAM − 4 GB]. Remote control cannot trigger it.
The setting lasts until restart. The memory chart's budget line follows it and says "raised" while it is in effect.

## Power menu (2026-10-24: built in)

Power sampling is built into the app: one reading per second from the energy counters (see "How each reading is
taken"), no password, no install, on at launch. Each reading is also written to `runs/current/power.jsonl` and
`runs/power_all.jsonl`, where the old root sampler wrote, so `bench-sampler` and the harness's peak power and energy
columns work unchanged. **Stop Power Sampling** switches it off and the choice is remembered; **Start Power Sampling**
switches it back on.

If `powermetrics` is running (someone started `sudo sampler/power_sampler.sh`), the app still shows power but writes
no lines, so the files never hold two streams; it checks again every 30 s and takes over once powermetrics stops. The
Power menu says when it is deferring.

## Max Runs (2026-10-22)

**GPU Memory › Max Runs: Sleep the Display When Each Run Starts** runs `pmset displaysleepnow` (no password) once per
run, as it starts. With the display asleep, macOS has no screen to draw, which is the pressure behind its GPU watchdog
error `ImpactingInteractivity` on long prefills. Moving the mouse wakes the display; it sleeps again at the next run.
Each sleep is logged to `runs/.display_sleep.log` with the run id, so a result can be marked "display asleep". Off by
default, remembered between launches; the header shows a "Max Runs" chip while it is on. Results with it on are not
default-condition results.
