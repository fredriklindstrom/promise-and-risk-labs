// bench-sampler — 1 Hz unprivileged system sampler for the benchmark harness.
//
// Writes one JSON line per tick to <out> (default runs/current/samples.jsonl). The runs/current
// symlink is re-resolved every tick, so when the harness repoints it at a new run folder the next
// sample lands in that run's own samples.jsonl. If runs/current does not exist the tick is skipped.
//
// Usage: bench-sampler [--project DIR | --root DIR] [--out PATH] [--interval SECONDS] [--stdout] [--once]
//   --project   project root (default: $BENCH_PROJECT, else the project this binary was built in — the
//               nearest folder above it that contains bench/harness.py — else the cwd)
//   --out       output file; relative paths are relative to the project root
//   --stdout    also print each line to stdout
//   --once      take one sample, print it to stdout, exit (no file written)
// Environment:
//   BENCH_EXTRA_VOLUME=/Volumes/Name   also report that volume's free space (disk_free_gb_external)
//   BENCH_NO_PRIVATE_SENSORS=1         skip the private on-chip temperature API (temperatures become null)

import BenchProbe
import Darwin
import Foundation

// MARK: - Arguments

var args = Array(CommandLine.arguments.dropFirst())
func takeValue(_ flag: String) -> String? {
    guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
    let v = args[i + 1]; args.removeSubrange(i...(i + 1)); return v
}
func takeFlag(_ flag: String) -> Bool {
    guard let i = args.firstIndex(of: flag) else { return false }
    args.remove(at: i); return true
}
if takeFlag("--help") || takeFlag("-h") {
    print("usage: bench-sampler [--project DIR | --root DIR] [--out PATH] [--interval SECONDS] [--stdout] [--once] | --machine")
    exit(0)
}
// --sensors: every on-chip temperature sensor this Mac exposes (check the die/ssd classification on a new chip).
if takeFlag("--sensors") {
    let t = Temperatures()
    guard t.available else { print("temperature sensors unavailable: \(t.unavailableReason ?? "unknown reason")"); exit(1) }
    for (n, v) in t.all().sorted(by: { $0.0 < $1.0 }) { print(String(format: "%-36@ %6.1f °C", n as NSString, v)) }
    let r = t.read()
    print("die (\(r.dieSensorNames)): max \(r.dieMaxC.map { String(format: "%.1f", $0) } ?? "—") avg \(r.dieAvgC.map { String(format: "%.1f", $0) } ?? "—") °C · ssd \(r.ssdC.map { String(format: "%.1f", $0) } ?? "—") °C · \(r.sensorCount) sensors")
    exit(0)
}
// --machine: print what this Mac is (the same reading Bench Monitor uses at launch) and exit.
if takeFlag("--machine") {
    let m = MachineInfo.detect(includeName: true)
    func n(_ v: Double?) -> String { v.map { String(format: "%.2f", $0) } ?? "null" }
    func i(_ v: Int?) -> String { v.map(String.init) ?? "null" }
    print("{\"name\":\(jsonString(m.name)),\"model_id\":\(jsonString(m.modelID)),\"chip\":\(jsonString(m.chip)),\"memory_gb\":\(n(m.memoryGB)),\"gpu_cores\":\(i(m.gpuCores)),\"p_cores\":\(i(m.performanceCores)),\"e_cores\":\(i(m.efficiencyCores)),\"metal_budget_gb\":\(n(m.metalBudgetGB)),\"disk_total_gb\":\(n(m.diskTotalGB)),\"disk_floor_default_gb\":\(n(m.defaultDiskFloorGB)),\"disk_warn_default_gb\":\(n(m.defaultDiskWarnGB)),\"label\":\(jsonString(m.label))}")
    exit(0)
}

// --root is what bench/harness.py passes; --project is the documented name. Same meaning.
let project = BenchProject.resolve(explicit: takeValue("--project") ?? takeValue("--root"))
let extraVolume = Probe.extraVolumeFromEnvironment
let outArg = takeValue("--out") ?? "runs/current/samples.jsonl"
let outPath = outArg.hasPrefix("/") ? outArg : project + "/" + outArg
let interval = max(0.25, Double(takeValue("--interval") ?? "1") ?? 1)
let alsoStdout = takeFlag("--stdout")
let once = takeFlag("--once")
if !args.isEmpty { FileHandle.standardError.write("bench-sampler: ignoring unknown arguments \(args)\n".data(using: .utf8)!) }

let runsCurrent = project + "/runs/current"

// MARK: - Sampler state

let probe = Probe()
var openFD: Int32 = -1
var openResolvedPath = ""
var warnedNoDir = false
var warnedOpenFailure = ""   // path last reported as unopenable (report once, not every second)

// Every per-tick read below avoids open(2): files are stat()ed, and only re-read (runner.pid) or
// read through a persistent descriptor (power.jsonl, events.jsonl) when they change.
var tailedDir: String? = nil
var powerTail = LineTailer()
var eventsTail = LineTailer()
var lastPower: (gpu: Double?, pkg: Double?)? = nil
var lastThermal: (pressure: String?, gpuMHz: Double?, pMHz: Double?)? = nil
let temps = Temperatures()
var eventsPid: Int32? = nil
var pidFileKey = ""
var pidFromFile: Int32? = nil

func retarget(_ dir: String?) {
    guard dir != tailedDir else { return }
    tailedDir = dir
    powerTail.reset(path: dir.map { $0 + "/power.jsonl" }, startFromEndBytes: 4096)
    eventsTail.reset(path: dir.map { $0 + "/events.jsonl" })
    lastPower = nil; eventsPid = nil; pidFileKey = ""; pidFromFile = nil
}

/// Runner pid: runs/current/runner.pid (written by the harness); fallback: the latest run_start's runner_pid.
func runnerPid(currentDir: String?) -> Int32? {
    guard let dir = currentDir else { return nil }
    for line in eventsTail.poll(now: Date()) where line.range(of: Data("\"run_start\"".utf8)) != nil {
        if let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any] {
            eventsPid = (obj["runner_pid"] as? NSNumber)?.int32Value
        }
    }
    let pidPath = dir + "/runner.pid"
    var st = stat()
    if stat(pidPath, &st) == 0 {
        let key = "\(st.st_ino)|\(st.st_size)|\(st.st_mtimespec.tv_sec)|\(st.st_mtimespec.tv_nsec)"
        if key != pidFileKey {
            pidFileKey = key
            // Regular file only (a FIFO here must not block the sampler), first 64 bytes only.
            pidFromFile = BenchFiles.readSmallRegularFile(pidPath, maxBytes: 64)
                .flatMap { String(data: $0, encoding: .utf8) }
                .flatMap { Int32($0.trimmingCharacters(in: .whitespacesAndNewlines)) }
        }
        if let p = pidFromFile, p > 0 { return p }
    }
    if let p = eventsPid, p > 0 { return p }
    return nil
}

/// Latest power line from runs/current/power.jsonl (written by power_sampler.sh), if the file was
/// written within the last 3 s; else nil.
func latestPower(currentDir: String?) -> (gpu: Double?, pkg: Double?) {
    guard let dir = currentDir else { return (nil, nil) }
    for line in powerTail.poll(now: Date()) {
        if let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any] {
            lastPower = ((obj["gpu_power_w"] as? NSNumber)?.doubleValue, (obj["pkg_power_w"] as? NSNumber)?.doubleValue)
            lastThermal = (obj["thermal_pressure"] as? String, (obj["gpu_freq_mhz"] as? NSNumber)?.doubleValue, (obj["cpu_p_freq_mhz"] as? NSNumber)?.doubleValue)
        }
    }
    guard let p = lastPower, let m = BenchFiles.mtime(dir + "/power.jsonl"), Date().timeIntervalSince(m) < 3 else { return (nil, nil) }
    return p
}

func sampleLine() -> String {
    let currentDir = BenchFiles.resolvedDirectory(runsCurrent)
    retarget(currentDir)
    let mem = probe.memory()
    let gpu = probe.gpu()
    let pid = runnerPid(currentDir: currentDir)
    let rss = pid.flatMap { Probe.processMemory(pid: $0) }
    let power = latestPower(currentDir: currentDir)
    let ext = extraVolume.flatMap { Probe.diskFreeGB($0, requireMountPoint: true) }
    var f: [String] = []
    f.append("\"t\":\(jsonString(BenchTime.string()))")
    f.append("\"type\":\"sample\"")
    f.append("\"mem_used_gb\":\(jsonNumber(mem?.usedGB))")
    f.append("\"mem_free_gb\":\(jsonNumber(mem?.freeGB))")
    f.append("\"mem_compressed_gb\":\(jsonNumber(mem?.compressedGB))")
    f.append("\"mem_wired_gb\":\(jsonNumber(mem?.wiredGB))")
    f.append("\"mem_pressure\":\(jsonString(probe.pressure().rawValue))")
    f.append("\"swap_gb\":\(jsonNumber(probe.swapUsedGB()))")
    f.append("\"gpu_util_pct\":\(jsonNumber(gpu.utilizationPct, 0))")
    f.append("\"gpu_power_w\":\(jsonNumber(power.gpu, 2))")
    f.append("\"pkg_power_w\":\(jsonNumber(power.pkg, 2))")
    // Thermal: macOS thermal state every second (no root); pressure + clocks only while power_sampler.sh runs.
    f.append("\"thermal_state\":\(jsonString(Thermal.state()))")
    let tr = temps.read()       // on-chip sensors, no root (nil fields if this macOS lacks the private API)
    f.append("\"soc_temp_max_c\":\(jsonNumber(tr.dieMaxC, 1))")
    f.append("\"soc_temp_avg_c\":\(jsonNumber(tr.dieAvgC, 1))")
    f.append("\"ssd_temp_c\":\(jsonNumber(tr.ssdC, 1))")
    let th = (power.pkg != nil) ? lastThermal : nil
    f.append("\"thermal_pressure\":\(jsonString(th?.pressure))")   // copied from power.jsonl: escaped
    f.append("\"gpu_freq_mhz\":\(jsonNumber(th?.gpuMHz, 0))")
    f.append("\"cpu_p_freq_mhz\":\(jsonNumber(th?.pMHz, 0))")
    f.append("\"runner_rss_gb\":\(jsonNumber(rss?.residentGB))")
    f.append("\"disk_free_gb\":\(jsonNumber(Probe.diskFreeGB(Probe.internalDataVolume), 1))")
    f.append("\"disk_free_gb_external\":\(jsonNumber(ext, 1))")
    // Extra fields (consumers ignore unknown keys):
    f.append("\"mem_app_gb\":\(jsonNumber(mem?.appGB))")
    f.append("\"mem_cached_gb\":\(jsonNumber(mem?.cachedGB))")
    f.append("\"mem_total_gb\":\(jsonNumber(mem?.totalGB))")
    f.append("\"gpu_mem_in_use_gb\":\(jsonNumber(gpu.inUseSystemMemoryGB))")
    f.append("\"runner_pid\":\(pid.map(String.init) ?? "null")")
    f.append("\"runner_footprint_gb\":\(jsonNumber(rss?.footprintGB))")
    return "{" + f.joined(separator: ",") + "}\n"
}

func write(_ line: String) {
    if alsoStdout { FileHandle.standardOutput.write(line.data(using: .utf8)!) }
    let dir = (outPath as NSString).deletingLastPathComponent
    guard let resolvedDir = BenchFiles.resolvedDirectory(dir) else {
        if openFD >= 0 { close(openFD); openFD = -1; openResolvedPath = "" }
        if !warnedNoDir {
            FileHandle.standardError.write("bench-sampler: \(dir) does not exist yet; waiting (samples skipped)\n".data(using: .utf8)!)
            warnedNoDir = true
        }
        return
    }
    warnedNoDir = false
    let resolved = resolvedDir + "/" + (outPath as NSString).lastPathComponent
    if resolved != openResolvedPath || openFD < 0 {
        if openFD >= 0 { close(openFD) }
        openFD = BenchFiles.openForAppend(resolved) ?? -1     // regular files only, never a FIFO
        openResolvedPath = openFD >= 0 ? resolved : ""
        if openFD < 0 {
            if warnedOpenFailure != resolved {
                warnedOpenFailure = resolved
                FileHandle.standardError.write("bench-sampler: cannot open \(resolved) (must be a regular file): \(String(cString: strerror(errno)))\n".data(using: .utf8)!)
            }
            return
        }
        warnedOpenFailure = ""
    }
    // One write(2) per line on an O_APPEND fd: unbuffered (flushed immediately) and atomic for readers.
    let bytes = Array(line.utf8)
    let n = bytes.withUnsafeBufferPointer { Darwin.write(openFD, $0.baseAddress, $0.count) }
    if n < 0 { close(openFD); openFD = -1; openResolvedPath = "" }
}

if once {
    print(sampleLine(), terminator: "")
    exit(0)
}

signal(SIGPIPE, SIG_IGN)
setpriority(PRIO_PROCESS, 0, 10)   // be polite: never compete with the model under test

// .workItem: drain an autorelease pool after every tick (Foundation/IOKit objects would otherwise pile up).
let timer = DispatchSource.makeTimerSource(queue: DispatchQueue(label: "bench-sampler", qos: .utility, autoreleaseFrequency: .workItem))
timer.schedule(deadline: .now(), repeating: interval, leeway: .milliseconds(50))
timer.setEventHandler { write(sampleLine()) }
timer.resume()

var signalSources: [DispatchSourceSignal] = []
for sig in [SIGINT, SIGTERM] {
    signal(sig, SIG_IGN)
    let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    src.setEventHandler { if openFD >= 0 { close(openFD) }; exit(0) }
    src.resume()
    signalSources.append(src)
}
FileHandle.standardError.write("bench-sampler: project \(project), writing \(outArg) every \(interval)s\n".data(using: .utf8)!)
dispatchMain()
