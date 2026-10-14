import BenchProbe
import Foundation

// MARK: - JSON field helpers (unknown fields ignored, missing fields tolerated, numbers-as-strings accepted)

typealias JSON = [String: Any]

func parseJSONLine(_ data: Data) -> JSON? {
    guard !data.isEmpty else { return nil }
    return (try? JSONSerialization.jsonObject(with: data)) as? JSON
}

/// Int from a Double read from a file: nil for NaN/±inf, otherwise clamped to ±1e15 (`Int(Double)`
/// traps outside Int's range; the margin leaves room for the sums and differences done on these).
func clampedInt(_ d: Double) -> Int? {
    guard d.isFinite else { return nil }
    return Int(Swift.max(-1e15, Swift.min(1e15, d)))
}

extension Dictionary where Key == String, Value == Any {
    func str(_ k: String) -> String? {
        switch self[k] {
        case let s as String: return s.isEmpty ? nil : s
        case let n as NSNumber: return n.stringValue
        default: return nil
        }
    }
    func dbl(_ k: String) -> Double? {
        switch self[k] {
        case let n as NSNumber:
            // NSNumber booleans are not numbers for our purposes
            if CFGetTypeID(n) == CFBooleanGetTypeID() { return nil }
            let d = n.doubleValue; return d.isFinite ? d : nil
        case let s as String:
            guard let d = Double(s), d.isFinite else { return nil }   // "inf", "nan", "1e999"
            return d
        default: return nil
        }
    }
    func int(_ k: String) -> Int? { dbl(k).flatMap(clampedInt) }
    func date(_ k: String) -> Date? { str(k).flatMap(BenchTime.parse) }
}

// MARK: - Run

struct RunInfo: Equatable {
    var runId: String
    var model: String
    var arch: String?            // "dense" | "moe" | …
    var activeParams: String?    // raw; formatted for display
    var quant: String?
    var runner: String?
    var runnerVersion: String?
    var contextTokens: Int?
    var loadCondition: String?
    var rep: Int?
    var of: Int?
    var queuePos: Int?
    var queueTotal: Int?
    var kind: String             // "llm" | "media"
    var gpuBudgetGB: Double?
    var diskFloorGB: Double?     // from the harness manifest's disk_guard (run_start)
    var diskWarnGB: Double?
    var startedAt: Date
    var warmup: Bool
    // media
    var width: Int?, height: Int?, seconds: Double?, steps: Int?, lora: String?, clip: Int?, clips: Int?

    init(_ j: JSON, received: Date) {
        runId = j.str("run_id") ?? "run"
        model = j.str("model") ?? "Unknown model"
        arch = j.str("arch")
        activeParams = Self.activeParams(j)
        quant = j.str("quant")
        runner = j.str("runner")
        runnerVersion = j.str("runner_version")
        contextTokens = j.int("context_tokens")
        loadCondition = j.str("load_condition")
        rep = j.int("rep"); of = j.int("of")
        queuePos = j.int("queue_pos"); queueTotal = j.int("queue_total")
        kind = j.str("kind") ?? "llm"
        gpuBudgetGB = j.dbl("gpu_budget_gb").map(ThisMac.decimalToChartGB)   // harness writes 10^9-byte GB
        diskFloorGB = j.dbl("disk_floor_gb"); diskWarnGB = j.dbl("disk_warn_gb")
        startedAt = j.date("t") ?? received
        warmup = (j["warmup"] as? Bool) == true || runId.hasSuffix("warmup")
        width = j.int("width"); height = j.int("height"); seconds = j.dbl("seconds")
        steps = j.int("steps"); lora = j.str("lora"); clip = j.int("clip"); clips = j.int("clips")
    }

    var isMedia: Bool { kind == "media" }

    /// "4B" from `active_params`: numbers < 1000 are taken as billions, larger as a raw parameter count.
    static func activeParams(_ j: JSON) -> String? {
        guard let n = j.dbl("active_params") else { return j.str("active_params") }
        let b = n >= 1000 ? n / 1e9 : n
        return b.rounded() == b ? "\(clampedInt(b) ?? 0)B" : String(format: "%.1fB", b)
    }

    static func archLabel(_ arch: String?, _ activeParams: String?) -> String? {
        guard let a = arch?.lowercased() else { return nil }
        if a == "moe" || a.contains("mixture") {
            if let p = activeParams { return "MoE · \(p) active" }
            return "MoE"
        }
        if a == "dense" { return "Dense" }
        return arch
    }

    var archLabel: String? { Self.archLabel(arch, activeParams) }

    var runnerLabel: String? {
        switch (runner, runnerVersion) {
        case let (r?, v?): return v.lowercased().hasPrefix(r.lowercased()) ? v : "\(r) · \(v)"
        case let (r?, nil): return r
        case let (nil, v?): return v
        default: return nil
        }
    }

    var loadLabel: String? {
        guard let l = loadCondition, !l.isEmpty else { return nil }
        return l.prefix(1).uppercased() + l.dropFirst()
    }
}

// MARK: - Results

enum FailKind: Equatable {
    case none          // run_end
    case didNotFit     // a measured limit (OOM, watchdog, "did not fit: …") — a result, not a crash
    case error         // harness/runner error ("error: …") — the harness retries these
    case skipped       // "skipped: …" (a smaller context already did not fit)
}

/// Peaks over one run: reset at run_start, frozen at run_end so they stay on screen.
struct RunPeaks: Equatable {
    var memUsedGB: Double?
    var pkgPowerW: Double?
    var gpuPowerW: Double?
    var thermalWorst: String?     // worst macOS thermal state seen during the run
    var gpuFreqMinMHz: Double?    // lowest GPU clock during the run (throttling evidence)
    var socTempMaxC: Double?      // hottest die reading during the run
    mutating func absorb(_ s: SamplePoint) {
        if let c = s.socTempMaxC { socTempMaxC = max(socTempMaxC ?? c, c) }
        if let t = s.thermalState, (Thermal.rank[t] ?? -1) > (thermalWorst.flatMap { Thermal.rank[$0] } ?? -1) { thermalWorst = t }
        if let f = s.gpuFreqMHz, f > 0 { gpuFreqMinMHz = min(gpuFreqMinMHz ?? f, f) }
        if let v = s.memUsedGB { memUsedGB = max(memUsedGB ?? v, v) }
        if let v = s.pkgPowerW { pkgPowerW = max(pkgPowerW ?? v, v) }
        if let v = s.gpuPowerW { gpuPowerW = max(gpuPowerW ?? v, v) }
    }
}

struct ResultRow: Identifiable, Equatable {
    var id: String               // runId|type|t
    var runId: String
    var t: Date
    var failed: Bool
    var model: String
    var arch: String?
    var activeParams: String?
    var runner: String?
    var quant: String?
    var contextTokens: Int?
    var kind: String
    var decodeTPS: Double?
    var prefillTPS: Double?
    var ttftS: Double?
    var peakMemGB: Double?
    var memUsedMaxGB: Double?
    var peakPkgPowerW: Double?
    var peakGPUPowerW: Double?
    var energyWh: Double?
    var wallS: Double?
    var sPerStep: Double?
    var fit: String?
    var reason: String?
    var failKind: FailKind
    var warmup: Bool
    var dryRun: Bool             // set "0" smoke tests: listed, but kept out of averages and "Fastest"
    var needle: Bool             // needle-in-a-haystack run (64-token answer): averaged apart from the speed runs

    init?(_ j: JSON, received: Date) {
        guard let type = j.str("type"), type == "run_end" || type == "run_fail" else { return nil }
        failed = type == "run_fail"
        runId = j.str("run_id") ?? "run"
        t = j.date("t") ?? received
        id = "\(runId)|\(type)|\(clampedInt(t.timeIntervalSince1970) ?? 0)"
        model = j.str("model") ?? runId
        arch = j.str("arch"); activeParams = RunInfo.activeParams(j)
        runner = j.str("runner"); quant = j.str("quant")
        contextTokens = j.int("context_tokens")
        kind = j.str("kind") ?? (j.dbl("s_per_step") != nil ? "media" : "llm")
        decodeTPS = j.dbl("decode_tps"); prefillTPS = j.dbl("prefill_tps"); ttftS = j.dbl("ttft_s")
        // peak_mem_gb is 10^9-byte GB from every harness source (mx peak, llama.cpp buffers, Ollama size_vram, GPU
        // allocated); shown in binary GB like the rest of the app. mem_used_gb_max comes from the sampler, already binary.
        peakMemGB = j.dbl("peak_mem_gb").map(ThisMac.decimalToChartGB); energyWh = j.dbl("energy_wh")
        memUsedMaxGB = j.dbl("mem_used_gb_max"); peakPkgPowerW = j.dbl("package_power_w_max"); peakGPUPowerW = j.dbl("gpu_power_w_max")
        wallS = j.dbl("wall_s"); sPerStep = j.dbl("s_per_step")
        fit = j.str("fit") ?? (failed ? "no" : nil)
        reason = j.str("reason")
        warmup = (j["warmup"] as? Bool) == true || runId.hasSuffix("warmup")
        dryRun = j.str("set") == "0" || runId.contains("dryrun") || model.lowercased().contains("(dry run)")
        needle = runId.contains("-needle-") || j["needle_correct"] is Bool
        let r = (reason ?? "").lowercased()
        if !failed { failKind = .none }
        else if r.hasPrefix("error") { failKind = .error }
        else if r.hasPrefix("skipped") || fit == "skipped" { failKind = .skipped }
        else { failKind = .didNotFit }
    }

    /// Reason for display: a plain-words headline (ReasonText), e.g. "Doesn't fit: needs 77.76 GB, oMLX allows 77.63 GB"
    /// instead of the raw Python repr of the server's error.
    var shortReason: String {
        guard let r = reason, !r.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            switch failKind {
            case .error: return "run error"
            case .skipped: return "skipped"
            default: return "did not fit"
            }
        }
        return ReasonText.explain(r).headline
    }

    /// One line of context under the headline (what refused it, the limit, where to look), when there is one.
    var reasonDetail: String? {
        guard let r = reason, !r.isEmpty else { return nil }
        return ReasonText.explain(r).detail
    }

    /// Counts toward averages / summary / fastest (warm-ups and errors don't).
    var countsAsResult: Bool { !warmup && !dryRun && (failKind == .none || failKind == .didNotFit) }

    var groupKey: String {
        [model, runner ?? "", quant ?? "", contextTokens.map(String.init) ?? "", kind].joined(separator: "|") + (needle ? "|needle" : "")
    }
    var isMedia: Bool { kind == "media" }
}

struct GroupAverage: Identifiable, Equatable {
    var id: String { key }
    var key: String
    var model: String
    var runner: String?
    var quant: String?
    var contextTokens: Int?
    var kind: String
    var runs: Int          // completed (run_end)
    var fails: Int         // run_fail
    var decodeTPS: Double?
    var prefillTPS: Double?
    var ttftS: Double?
    var peakMemGB: Double?
    var sPerStep: Double?
    var lastT: Date
    // Compare view (2026-10-17)
    var arch: String? = nil
    var activeParams: String? = nil
    var peakPkgPowerW: Double? = nil
    var energyWh: Double? = nil
    var memUsedMaxGB: Double? = nil
    var needle = false
}

struct ResultsSummary: Equatable {
    var total = 0
    var fits = 0
    var fails = 0          // did not fit
    var errors = 0         // harness/runner errors (excluded from the result counts)
    var meanDecode: Double?
    var meanPeak: Double?
    var best: ResultRow?
}

/// Running sum for one averaged field (nil values are skipped, as in a mean over the non-nil ones).
struct RunningMean: Equatable {
    var sum = 0.0
    var count = 0
    mutating func add(_ v: Double?) { if let v { sum += v; count += 1 } }
    var mean: Double? { count == 0 ? nil : sum / Double(count) }
}

/// Totals over every result ever added. Kept incrementally, so the engine can cap the rows it holds
/// for the table (newest N) while the summary, averages and "fastest" still count every run.
struct ResultTotals {
    private struct Group {
        var avg: GroupAverage
        var decode = RunningMean(), prefill = RunningMean(), ttft = RunningMean(), peak = RunningMean(), sps = RunningMean()
        var power = RunningMean(), energy = RunningMean(), memUsed = RunningMean()
    }
    static let maxGroups = 5_000                 // distinct configs; more than that are not averaged
    private var groups: [String: Group] = [:]
    private var summary = ResultsSummary()
    private var meanDecode = RunningMean(), meanPeak = RunningMean()
    private var fastestByModel: [String: (id: String, tps: Double)] = [:]

    mutating func add(_ r: ResultRow) {
        if !r.warmup && !r.dryRun && r.failKind == .error { summary.errors += 1 }
        guard r.countsAsResult else { return }
        summary.total += 1
        if r.failed { summary.fails += 1 } else { summary.fits += 1 }
        if !r.failed {
            meanPeak.add(r.peakMemGB)
            if !r.isMedia {
                meanDecode.add(r.decodeTPS)
                if let d = r.decodeTPS {
                    if d > (summary.best?.decodeTPS ?? -.infinity) { summary.best = r }
                    if d > (fastestByModel[r.model]?.tps ?? -.infinity) { fastestByModel[r.model] = (r.id, d) }
                }
            }
        }
        summary.meanDecode = meanDecode.mean
        summary.meanPeak = meanPeak.mean

        let key = r.groupKey
        if groups[key] == nil {
            guard groups.count < Self.maxGroups else { return }
            groups[key] = Group(avg: GroupAverage(key: key, model: r.model, runner: r.runner, quant: r.quant,
                                                  contextTokens: r.contextTokens, kind: r.kind, runs: 0, fails: 0, lastT: r.t,
                                                  needle: r.needle))
        }
        guard var g = groups[key] else { return }
        if g.avg.arch == nil { g.avg.arch = r.arch }
        if g.avg.activeParams == nil { g.avg.activeParams = r.activeParams }
        if r.failed {
            g.avg.fails += 1
        } else {
            g.avg.runs += 1
            g.decode.add(r.decodeTPS); g.prefill.add(r.prefillTPS); g.ttft.add(r.ttftS); g.peak.add(r.peakMemGB); g.sps.add(r.sPerStep)
            g.power.add(r.peakPkgPowerW); g.energy.add(r.energyWh); g.memUsed.add(r.memUsedMaxGB)
            g.avg.decodeTPS = g.decode.mean; g.avg.prefillTPS = g.prefill.mean; g.avg.ttftS = g.ttft.mean
            g.avg.peakMemGB = g.peak.mean; g.avg.sPerStep = g.sps.mean
            g.avg.peakPkgPowerW = g.power.mean; g.avg.energyWh = g.energy.mean; g.avg.memUsedMaxGB = g.memUsed.mean
        }
        g.avg.lastT = max(g.avg.lastT, r.t)
        groups[key] = g
    }

    var resultsSummary: ResultsSummary { summary }
    /// Newest config first.
    var groupAverages: [GroupAverage] { groups.values.map(\.avg).sorted { $0.lastT > $1.lastT } }
    /// Fastest decode run per model (ids of those rows).
    var fastest: Set<String> { Set(fastestByModel.values.map(\.id)) }
}

// MARK: - Agent-loop cache test

/// One `cache_turn` event: the harness sends one when a turn starts (ttft/cached still null) and one when it ends.
struct CacheTurn: Equatable {
    var condition: String
    var turn: Int
    var turnsTotal: Int
    var contextTokens: Int?
    var cachedTokens: Int?
    var newTokens: Int?
    var ttftS: Double?

    static let names = ["A": "Cache-friendly", "B": "Broken at the top", "C": "Broken mid-context", "D": "Thinking stripped"]

    init?(_ j: JSON) {
        guard j.str("type") == "cache_turn", let c = j.str("condition"), let t = j.int("turn") else { return nil }
        // Drawn as one bar per turn: clamp, so a crafted count can't ask for millions of views (review M2).
        condition = String(c.prefix(16)); turn = min(max(t, 1), 200); turnsTotal = min(max(j.int("turns_total") ?? t, 1), 200)
        contextTokens = j.int("context_tokens"); cachedTokens = j.int("cached_tokens"); newTokens = j.int("new_tokens")
        ttftS = j.dbl("ttft_s")
    }

    var conditionLabel: String { "\(condition) · \(Self.names[condition] ?? "condition")" }
}

// MARK: - Live progress

struct LiveSpeed: Equatable {
    var tokens: Int?
    var decodeTPS: Double?
    var prefillTPS: Double?
    var ttftS: Double?
    var hasNumbers: Bool { decodeTPS != nil || prefillTPS != nil || ttftS != nil }
}

struct MediaProgress: Equatable {
    var step: Int?
    var steps: Int?
    var sPerStep: Double?
    var etaS: Double?
    var clip: Int?
    var clips: Int?
    var receivedAt: Date
}

// MARK: - Samples

struct SamplePoint: Equatable {
    var t: Date
    var memUsedGB: Double?
    var pressure: String          // normal | warn | critical
    var swapGB: Double?
    var gpuUtilPct: Double?
    var gpuPowerW: Double?
    var pkgPowerW: Double?
    var diskFreeGB: Double?
    var diskFreeExternalGB: Double?
    var runnerRSSGB: Double?
    var direct: Bool              // true = read in-app (sampler quiet), false = from samples.jsonl
    // Thermal (2026-10-13): macOS thermal state every sample; pressure + GPU clock only while the power sampler runs.
    var thermalState: String? = nil
    var thermalPressure: String? = nil
    var gpuFreqMHz: Double? = nil
    var socTempMaxC: Double? = nil        // hottest SoC die sensor

    init(t: Date, memUsedGB: Double?, pressure: String, swapGB: Double?, gpuUtilPct: Double?,
         gpuPowerW: Double?, pkgPowerW: Double?, diskFreeGB: Double?, diskFreeExternalGB: Double?,
         runnerRSSGB: Double?, direct: Bool, thermal: String? = nil, socTempC: Double? = nil) {
        self.t = t; self.memUsedGB = memUsedGB; self.pressure = pressure; self.swapGB = swapGB
        self.gpuUtilPct = gpuUtilPct; self.gpuPowerW = gpuPowerW; self.pkgPowerW = pkgPowerW
        self.diskFreeGB = diskFreeGB; self.diskFreeExternalGB = diskFreeExternalGB
        self.runnerRSSGB = runnerRSSGB; self.direct = direct; self.thermalState = thermal; self.socTempMaxC = socTempC
    }

    init?(_ j: JSON, received: Date) {
        guard j.str("type") == "sample" else { return nil }
        self.init(t: j.date("t") ?? received, memUsedGB: j.dbl("mem_used_gb"),
                  pressure: j.str("mem_pressure") ?? "normal", swapGB: j.dbl("swap_gb"),
                  gpuUtilPct: j.dbl("gpu_util_pct"), gpuPowerW: j.dbl("gpu_power_w"),
                  pkgPowerW: j.dbl("pkg_power_w"), diskFreeGB: j.dbl("disk_free_gb"),
                  diskFreeExternalGB: j.dbl("disk_free_gb_external"),
                  runnerRSSGB: j.dbl("runner_rss_gb"), direct: false)
        thermalState = j.str("thermal_state"); thermalPressure = j.str("thermal_pressure"); gpuFreqMHz = j.dbl("gpu_freq_mhz")
        socTempMaxC = j.dbl("soc_temp_max_c")
    }
}

// MARK: - Formatting

enum Fmt {
    private static let grouped: NumberFormatter = {
        let f = NumberFormatter(); f.numberStyle = .decimal; f.maximumFractionDigits = 0; return f
    }()
    static func context(_ tokens: Int?) -> String? {
        guard let t = tokens, t > 0 else { return nil }
        if t >= 1_048_576, t % 1_048_576 == 0 { return "\(t / 1_048_576)M" }
        if t >= 1_000_000, t % 1_000_000 == 0 { return "\(t / 1_000_000)M" }
        if t % 1024 == 0 { return "\(t / 1024)K" }
        if t % 1000 == 0 { return "\(t / 1000)K" }
        return String(format: "%.1fK", Double(t) / 1024)
    }

    static func num(_ v: Double?, _ decimals: Int = 1) -> String {
        guard let v, v.isFinite else { return "—" }
        return String(format: "%.\(decimals)f", v)
    }

    /// Speeds: one decimal below 100, none above.
    static func speed(_ v: Double?) -> String {
        guard let v, v.isFinite else { return "—" }
        return v >= 100 ? String(format: "%.0f", v) : String(format: "%.1f", v)
    }

    static func gb(_ v: Double?) -> String {
        guard let v, v.isFinite else { return "—" }
        return v >= 100 ? String(format: "%.0f GB", v) : String(format: "%.1f GB", v)
    }

    static func diskGB(_ v: Double?) -> String {
        guard let v, v.isFinite else { return "—" }
        return (grouped.string(from: NSNumber(value: v.rounded())) ?? String(clampedInt(v) ?? 0)) + " GB"
    }

    static func duration(_ s: Double?) -> String {
        guard let s, s >= 0, let total = clampedInt(s.rounded()) else { return "—" }
        let h = total / 3600, m = (total % 3600) / 60, sec = total % 60
        if h > 0 { return String(format: "%ld:%02ld:%02ld", h, m, sec) }
        return String(format: "%ld:%02ld", m, sec)
    }

    static func eta(_ s: Double?) -> String {
        guard let s, s >= 0, let mins = clampedInt((s / 60).rounded()) else { return "—" }
        if mins >= 60 { return "\(mins / 60) h \(String(format: "%02d", mins % 60)) m" }
        if mins >= 1 { return "\(mins) min" }
        return "< 1 min"
    }
}
