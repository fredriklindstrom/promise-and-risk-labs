// bench-power — parses `powermetrics -f plist` output from stdin and appends JSON power lines.
//
// powermetrics (root only) emits one XML property list per sample, separated by NUL bytes.
// power_sampler.sh runs powermetrics as root and pipes it into this parser running as the
// normal user, so the files it creates are owned by that user, not root.
//
// Output line: {"t":ISO8601,"type":"power","pkg_power_w":..,"cpu_power_w":..,"gpu_power_w":..,"ane_power_w":..}
// appended to <project>/runs/current/power.jsonl (symlink re-resolved per sample; skipped if it
// does not exist) and to <project>/runs/power_all.jsonl.
//
// Key names (Apple-silicon powermetrics; `man powermetrics` does not document plist keys):
//   processor.cpu_power, processor.gpu_power, processor.ane_power, processor.combined_power  — milliwatts
//   processor.cpu_energy / gpu_energy / ane_energy — millijoules over elapsed_ns (fallback)
//   gpu.gpu_power / gpu.gpu_energy                    — fallback location seen on some releases
// "pkg_power_w" = combined_power (CPU + GPU + ANE) — Apple silicon has no RAPL-style package
// counter; if combined_power is absent we sum whatever components are present.
// Every key is optional; anything missing becomes null.
// Input is capped: 16 MB without a NUL separator is dropped (counted as unparseable) and the parser
// skips to the next NUL, so a broken or hostile stream cannot grow memory without bound.
//
// Usage: bench-power [--project DIR | --root DIR] [--stdout]
//        bench-power --ioreport [--interval-ms N] [--watch-pid PID] [--project DIR] [--stdout]
//   --project   project root (default: $BENCH_PROJECT, else the project this binary was built in —
//               the nearest folder above it that contains bench/harness.py — else the cwd)
//   --ioreport  no powermetrics and no root: sample the energy counters directly (BenchProbe.EnergySampler,
//               2026-10-24) every --interval-ms (default 1000) and write the same lines with "source":"ioreport".
//               Runs until Ctrl-C, or until process --watch-pid exits. Refuses root. Bench Monitor stops
//               writing while this runs (it defers to any other writer), so the two never interleave.

import BenchProbe
import Darwin
import Foundation

var args = Array(CommandLine.arguments.dropFirst())
func takeValue(_ flag: String) -> String? {
    guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
    let v = args[i + 1]; args.removeSubrange(i...(i + 1)); return v
}
let project = BenchProject.resolve(explicit: takeValue("--project") ?? takeValue("--root"))
let alsoStdout = args.contains("--stdout")

if args.contains("--ioreport") {
    // Writes as you only: as root it would follow runs/current and leave root-owned files in the project.
    guard geteuid() != 0 else {
        FileHandle.standardError.write("bench-power: --ioreport needs no root; run it as yourself\n".data(using: .utf8)!)
        exit(1)
    }
    let interval = min(60_000, max(100, Int(takeValue("--interval-ms") ?? "") ?? 1000))
    let watch = takeValue("--watch-pid").flatMap { Int32($0) }
    if let w = watch, w <= 1 {       // 0 / -1 / 1 would name a process group, every process, or launchd
        FileHandle.standardError.write("bench-power: --watch-pid must be a process id above 1\n".data(using: .utf8)!)
        exit(1)
    }
    let sampler = EnergySampler()
    guard sampler.available else {
        FileHandle.standardError.write("bench-power: energy counters unavailable (\(sampler.unavailableReason ?? "unknown"))\n".data(using: .utf8)!)
        exit(1)
    }
    signal(SIGPIPE, SIG_IGN)
    _ = sampler.sample()                       // baseline
    var written = 0
    while watch.map({ kill($0, 0) == 0 }) ?? true {
        usleep(useconds_t(interval * 1000))
        autoreleasepool {
            guard let r = sampler.sample() else { return }
            let line = PowerLog.line(r)
            if alsoStdout { FileHandle.standardOutput.write(line.data(using: .utf8)!) }
            PowerLog.emit(line, project: project)
            written += 1
        }
    }
    FileHandle.standardError.write("bench-power: \(written) samples written (ioreport)\n".data(using: .utf8)!)
    exit(0)
}

func num(_ any: Any?) -> Double? { (any as? NSNumber)?.doubleValue }
var dumpedKeys = false

/// Returns watts for a component: prefer "<name>_power" (mW), else "<name>_energy" (mJ) / seconds.
func watts(_ dicts: [[String: Any]], _ name: String, seconds: Double?) -> Double? {
    for d in dicts { if let mw = num(d["\(name)_power"]) { return mw / 1000 } }
    if let s = seconds, s > 0 {
        for d in dicts { if let mj = num(d["\(name)_energy"]) { return mj / 1000 / s } }
    }
    return nil
}

func parseSample(_ data: Data) -> String? {
    guard let plist = try? PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any] else {
        return nil
    }
    let processor = plist["processor"] as? [String: Any] ?? [:]
    let gpuDict = plist["gpu"] as? [String: Any] ?? [:]
    let seconds = num(plist["elapsed_ns"]).map { $0 / 1e9 }
    let cpu = watts([processor], "cpu", seconds: seconds)
    let gpu = watts([processor, gpuDict], "gpu", seconds: seconds)
    let ane = watts([processor], "ane", seconds: seconds)
    var pkg = watts([processor], "combined", seconds: seconds)
    if pkg == nil, cpu != nil || gpu != nil {
        pkg = (cpu ?? 0) + (gpu ?? 0) + (ane ?? 0)
    }
    // Thermal + clocks (the `thermal` sampler and the cpu/gpu samplers). Key names are read defensively: absent -> null.
    let pressure = (plist["thermal_pressure"] as? String)?.lowercased()
    let gpuMHz = num(gpuDict["freq_hz"]).map { $0 / 1e6 }
    let gpuActive = num(gpuDict["idle_ratio"]).map { (1 - $0) * 100 }
    var pMHz: Double? = nil, eMHz: Double? = nil
    for c in processor["clusters"] as? [[String: Any]] ?? [] {
        guard let name = c["name"] as? String, let f = num(c["freq_hz"]).map({ $0 / 1e6 }) else { continue }
        if name.hasPrefix("P") { pMHz = max(pMHz ?? 0, f) } else if name.hasPrefix("E") { eMHz = max(eMHz ?? 0, f) }
    }
    if !dumpedKeys {      // once per start: which keys this macOS actually emits, so the names above can be checked
        dumpedKeys = true
        let top = plist.keys.sorted().joined(separator: ",")
        let g = gpuDict.keys.sorted().joined(separator: ",")
        FileHandle.standardError.write("bench-power: plist keys [\(top)] gpu [\(g)] thermal_pressure=\(pressure.map(jsonString) ?? "absent")\n".data(using: .utf8)!)
    }
    // Use the sample's own timestamp if present (Date in the plist), else now.
    let t = (plist["timestamp"] as? Date) ?? Date()
    let tp = jsonString(pressure)
    return "{\"t\":\(jsonString(BenchTime.string(t))),\"type\":\"power\",\"pkg_power_w\":\(jsonNumber(pkg)),\"cpu_power_w\":\(jsonNumber(cpu)),\"gpu_power_w\":\(jsonNumber(gpu)),\"ane_power_w\":\(jsonNumber(ane)),\"thermal_pressure\":\(tp),\"gpu_freq_mhz\":\(jsonNumber(gpuMHz, 0)),\"gpu_active_pct\":\(jsonNumber(gpuActive, 1)),\"cpu_p_freq_mhz\":\(jsonNumber(pMHz, 0)),\"cpu_e_freq_mhz\":\(jsonNumber(eMHz, 0)),\"source\":\"powermetrics\"}\n"
}

func emit(_ line: String) {
    if alsoStdout { FileHandle.standardOutput.write(line.data(using: .utf8)!) }
    PowerLog.emit(line, project: project)
}

signal(SIGPIPE, SIG_IGN)
var buffer = Data()
var parsed = 0, failed = 0
let readSize = 1024 * 1024          // read(2) at most this much per pass (FileHandle.availableData reads a whole file)
var readBuf = [UInt8](repeating: 0, count: readSize)
let maxBuffer = 16 * 1024 * 1024    // one powermetrics plist is a few KB; 16 MB without a NUL is not a sample
var resyncing = false               // after a drop: discard input up to the next NUL (mid-sample bytes)
var warnedOverflow = false
// Each pass runs inside its own autorelease pool. A top-level loop never drains one by itself, so every
// parsed plist (NSDictionary/NSData/NSDate) used to stay alive: 2.1 GB after 37 h (found 2026-10-13).
var eof = false
while !eof {
    autoreleasepool {
        // Blocks until some data or EOF; returns what is there (a pipe delivers each sample as it comes).
        let n = readBuf.withUnsafeMutableBytes { read(STDIN_FILENO, $0.baseAddress, readSize) }
        if n < 0, errno == EINTR { return }
        if n <= 0 { eof = true; return }
        var chunk = Data(readBuf[0..<n])
        if resyncing {
            guard let nul = chunk.firstIndex(of: 0) else { return }
            chunk = chunk.subdata(in: (nul + 1)..<chunk.endIndex)
            resyncing = false
        }
        // Only the new bytes can hold a NUL the previous pass has not seen.
        var searchFrom = buffer.endIndex
        buffer.append(chunk)
        while let nul = buffer[searchFrom...].firstIndex(of: 0) {
            let one = buffer[buffer.startIndex..<nul]
            if one.count > maxBuffer {
                failed += 1                      // oversize segment that arrived in one read: never parsed
            } else if !one.allSatisfy({ $0 == 0x0A || $0 == 0x0D || $0 == 0x20 }) {
                if let line = parseSample(Data(one)) { emit(line); parsed += 1 } else { failed += 1 }
            }
            buffer.removeSubrange(buffer.startIndex...nul)
            searchFrom = buffer.startIndex
        }
        if buffer.count > maxBuffer {
            buffer = Data(); failed += 1; resyncing = true
            if !warnedOverflow {
                warnedOverflow = true
                FileHandle.standardError.write("bench-power: over \(maxBuffer / 1_048_576) MB without a sample separator; dropped, resyncing at the next NUL\n".data(using: .utf8)!)
            }
        }
    }
}
// A final sample without a trailing NUL (e.g. a hand-made test file).
if !resyncing, !buffer.allSatisfy({ $0 == 0x0A || $0 == 0x0D || $0 == 0x20 || $0 == 0 }) {
    if let line = parseSample(buffer) { emit(line); parsed += 1 } else { failed += 1 }
}
FileHandle.standardError.write("bench-power: \(parsed) samples parsed, \(failed) unparseable\n".data(using: .utf8)!)
