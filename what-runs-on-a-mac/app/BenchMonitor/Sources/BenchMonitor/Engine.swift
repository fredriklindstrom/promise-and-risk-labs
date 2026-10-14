import BenchProbe
import Darwin
import Foundation

// MARK: - Configuration & snapshot

struct EngineConfig: Equatable {
    var projectDir: String
    var historyFile: String          // file name inside runs/ (or absolute path); several, comma-separated, are merged
    var extraVolume: String?         // optional second volume for the footer ("<name> free N GB"); nil = none
    var staleSeconds: Double = 60    // events silence during a run before "No data"
    var chartWindow: Double = 120
    var maxRows = 5_000              // result rows kept for the table (totals still count every run)
    var maxSamples = 600             // chart points kept (2-minute window; 5 per second is plenty)

    var runsDir: String { projectDir + "/runs" }
    var currentLink: String { runsDir + "/current" }
    var historyPaths: [String] {
        historyFile.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            .map { $0.hasPrefix("/") ? $0 : runsDir + "/" + $0 }
    }
}

enum RunStatus: Equatable {
    case noRunFolder, waiting, running, stale, queueComplete
}

struct Snapshot {
    var now = Date()
    var status: RunStatus = .noRunFolder
    var run: RunInfo?
    var runEndedAt: Date?
    var phase: String?
    var live: LiveSpeed?
    var media: MediaProgress?
    var lastCompleted: ResultRow?
    var finding: ResultRow?          // latest run_fail of the current/most recent run
    var findingFlashAt: Date?        // when to start the amber flash (nil = don't flash)
    var findingFlashing = false      // true for the first ~2 s after a fresh run_fail
    var eventsAge: Double?
    var queueETA: Double?
    var avgRunSeconds: Double?
    var sessionCompleted = 0

    var resultsVersion = 0
    var results: [ResultRow] = []
    var groups: [GroupAverage] = []
    var summary = ResultsSummary()
    var fastest: Set<String> = []

    var samples: [SamplePoint] = []
    var latest: SamplePoint?
    var sampleSource = "—"
    var gpuBudgetGB: Double = ThisMac.defaultBudgetGB
    var currentRunFolder: String?
    var peaks = RunPeaks()
    var extraVolumeFreeGB: Double?   // statfs of config.extraVolume, nil when unset or not mounted
    // Agent-loop cache test: the current turn, this run's TTFT per turn, and the longest TTFT seen per
    // model+runner (so condition A filmed after B is drawn on B's scale).
    var cacheTurn: CacheTurn?
    var cacheTTFTs: [Int: Double] = [:]
    var cacheScaleMax: [String: Double] = [:]
}

// MARK: - Engine (runs on its own utility queue, publishes one Snapshot per second)

final class Engine {
    // .workItem: drain an autorelease pool after every tick (JSONSerialization/IOKit objects).
    private let queue = DispatchQueue(label: "benchmonitor.engine", qos: .utility, autoreleaseFrequency: .workItem)
    private var timer: DispatchSourceTimer?
    private var config: EngineConfig
    private let publish: (Snapshot) -> Void
    private let probe = Probe()
    private let temps = Temperatures()

    private var snap = Snapshot()
    private var currentResolved: String?
    private var events = LineTailer()
    private var samplesTail = LineTailer()
    private var histories: [LineTailer] = []       // one per history file (Settings may list several)
    private var powerTail = LineTailer()
    private var lastPower: (t: Date, gpu: Double?, pkg: Double?)?
    private var lastSampleReceived: Date = .distantPast
    private var lastGPUBusy: Date = .distantPast    // last fresh sample with the GPU busy (prefill liveness)
    private var folderChangedAt = Date()
    private var startTimes: [String: Date] = [:]    // runId → run_start t; removed once the run's duration is counted
    private var durationSum = 0.0
    private var durationCount = 0
    private var resultIndex: [String: Date] = [:]   // runId|type → t, for de-duplicating events vs history (kept rows only)
    private var rows: [ResultRow] = []              // newest config.maxRows, for the table
    private var totals = ResultTotals()             // every result, for summary / averages / fastest
    private var rowsDirty = false
    private var samplesUnsorted = false

    init(config: EngineConfig, publish: @escaping (Snapshot) -> Void) {
        self.config = config
        self.publish = publish
    }

    func start() {
        queue.async { [self] in
            resetAll()
            tick()
            let t = DispatchSource.makeTimerSource(queue: queue)
            t.schedule(deadline: .now() + 1, repeating: 1.0, leeway: .milliseconds(100))
            t.setEventHandler { [weak self] in self?.tick() }
            t.resume()
            timer = t
        }
    }

    func reconfigure(_ c: EngineConfig) {
        queue.async { [self] in
            guard c != config else { return }
            config = c
            resetAll()
            tick()
        }
    }

    private func resetAll() {
        snap = Snapshot()
        currentResolved = nil
        events.reset(path: nil)
        samplesTail.reset(path: nil)
        histories = config.historyPaths.map { p in var t = LineTailer(); t.reset(path: p); return t }
        lastSampleReceived = .distantPast
        startTimes = [:]; durationSum = 0; durationCount = 0
        resultIndex = [:]; rows = []; totals = ResultTotals(); rowsDirty = true; samplesUnsorted = false
    }

    // MARK: tick

    private func tick() {
        let now = Date()
        snap.now = now

        // 1. History (persistent results)
        for i in histories.indices {
            for line in histories[i].poll(now: now) {
                if let j = parseJSONLine(line), let row = ResultRow(j, received: now) { addResult(row, fromHistory: true) }
            }
        }

        // 2. Re-resolve runs/current
        let resolved = BenchFiles.resolvedDirectory(config.currentLink)
        if resolved != currentResolved {
            currentResolved = resolved
            folderChangedAt = now
            snap.currentRunFolder = resolved.map { ($0 as NSString).lastPathComponent }
            events.reset(path: resolved.map { $0 + "/events.jsonl" })
            samplesTail.reset(path: resolved.map { $0 + "/samples.jsonl" }, startFromEndBytes: 64 * 1024)
            powerTail.reset(path: nil)   // opened lazily, only while the sampler is quiet
            lastPower = nil
            // A new folder means a new run is coming; the old run's live numbers are no longer live.
            if resolved == nil { snap.status = .noRunFolder }
        }

        // 3. Events
        for line in events.poll(now: now) {
            if let j = parseJSONLine(line) { handleEvent(j, now: now) }
        }

        // 4. Samples (sampler), else read directly
        var gotSample = false
        for line in samplesTail.poll(now: now) {
            if let j = parseJSONLine(line), let s = SamplePoint(j, received: now) {
                appendSample(s); gotSample = true
            }
        }
        if gotSample { lastSampleReceived = now; snap.sampleSource = "samples.jsonl" }
        if now.timeIntervalSince(lastSampleReceived) > 5 {
            appendSample(directSample(now: now))
            snap.sampleSource = "direct read (sampler quiet)"
        }
        tidySamples(now: now)
        snap.latest = snap.samples.last
        if let s = snap.latest, (s.gpuUtilPct ?? 0) >= 20, now.timeIntervalSince(s.t) < 10 { lastGPUBusy = now }
        snap.extraVolumeFreeGB = config.extraVolume.flatMap { Probe.diskFreeGB($0, requireMountPoint: true) }

        // 5. Status
        var lastEvent = events.lastGrowth ?? folderChangedAt
        // Prefill sends nothing until the first token (a 128K prefill on an M2 Max takes ~20 min), so
        // there a busy GPU also counts as a sign of life. A hung run (GPU idle, no events) still goes stale.
        if snap.phase == "prefill" { lastEvent = max(lastEvent, lastGPUBusy) }
        let eventsAge = now.timeIntervalSince(lastEvent)
        if currentResolved == nil {
            snap.status = .noRunFolder
        } else if snap.run != nil, snap.runEndedAt == nil, snap.status != .queueComplete {
            var limit = config.staleSeconds
            // Media steps can legitimately take minutes between media_progress events.
            if snap.run?.isMedia == true, let sps = snap.media?.sPerStep { limit = max(limit, sps * 1.5 + 15) }
            snap.status = eventsAge > limit ? .stale : .running
        } else if snap.status != .queueComplete {
            snap.status = .waiting
        }

        // Only published while stale (it drives the "NO DATA · n s" badge); nil otherwise so the
        // view model doesn't change every second for nothing.
        snap.eventsAge = snap.status == .stale ? eventsAge.rounded() : nil

        snap.findingFlashing = snap.findingFlashAt.map { now.timeIntervalSince($0) < 2.5 } ?? false

        // 6. ETA: mean duration of runs completed this session × what's left
        snap.avgRunSeconds = durationCount == 0 ? nil : durationSum / Double(durationCount)
        snap.sessionCompleted = durationCount
        if let avg = snap.avgRunSeconds, let run = snap.run, let pos = run.queuePos, let total = run.queueTotal,
           snap.status != .queueComplete {
            let after = Double(max(0, total - pos))
            if snap.runEndedAt == nil {
                let elapsed = now.timeIntervalSince(run.startedAt)
                snap.queueETA = after * avg + max(0, avg - elapsed)
            } else {
                snap.queueETA = after * avg
            }
        } else {
            snap.queueETA = nil
        }

        // 7. Results aggregates (only recomputed when rows change)
        if rowsDirty {
            rowsDirty = false
            rows.sort { $0.t > $1.t }
            if rows.count > config.maxRows {
                // Keep the newest rows; forget the dropped rows' dedup keys too (bounded memory).
                for r in rows[config.maxRows...] {
                    let key = "\(r.runId)|\(r.failed ? "fail" : "end")"
                    if resultIndex[key] == r.t { resultIndex[key] = nil }
                }
                rows.removeSubrange(config.maxRows...)
            }
            snap.results = rows
            snap.groups = totals.groupAverages
            snap.summary = totals.resultsSummary
            snap.fastest = totals.fastest
            snap.resultsVersion += 1
            if snap.lastCompleted == nil { snap.lastCompleted = rows.first }
        }

        publish(snap)
    }

    // MARK: events

    private func handleEvent(_ j: JSON, now: Date) {
        guard let type = j.str("type") else { return }
        switch type {
        case "run_start":
            let run = RunInfo(j, received: now)
            snap.run = run
            snap.runEndedAt = nil
            snap.phase = "loading"
            snap.live = nil
            snap.media = nil
            snap.finding = nil
            snap.findingFlashAt = nil
            snap.peaks = RunPeaks()
            if let b = run.gpuBudgetGB { snap.gpuBudgetGB = b }
            snap.cacheTurn = nil
            snap.cacheTTFTs = [:]
            startTimes[run.runId] = run.startedAt
            if startTimes.count > 1000 {        // runs that started and never ended: keep the newest (review L4)
                for k in startTimes.sorted(by: { $0.value < $1.value }).prefix(startTimes.count - 1000).map(\.key) { startTimes[k] = nil }
            }
            snap.status = .running
        case "phase":
            snap.phase = j.str("phase")
        case "progress":
            var l = snap.live ?? LiveSpeed()
            if let v = j.int("tokens") { l.tokens = v }
            if let v = j.dbl("decode_tps") { l.decodeTPS = v }
            if let v = j.dbl("prefill_tps") { l.prefillTPS = v }
            if let v = j.dbl("ttft_s") { l.ttftS = v }
            snap.live = l
        case "media_progress":
            snap.media = MediaProgress(step: j.int("step"), steps: j.int("steps"), sPerStep: j.dbl("s_per_step"),
                                       etaS: j.dbl("eta_s"), clip: j.int("clip"), clips: j.int("clips"), receivedAt: now)
        case "run_end", "run_fail":
            guard var row = ResultRow(j, received: now) else { return }
            // Rows from harness builds before 2026-10-12 carry no peaks: use what this app saw during the run.
            if row.memUsedMaxGB == nil { row.memUsedMaxGB = snap.peaks.memUsedGB }
            if row.peakPkgPowerW == nil { row.peakPkgPowerW = snap.peaks.pkgPowerW }
            if row.peakGPUPowerW == nil { row.peakGPUPowerW = snap.peaks.gpuPowerW }
            addResult(row, fromHistory: false)
            if snap.run?.runId == row.runId || snap.run == nil { snap.runEndedAt = row.t }
            snap.phase = "done"
            snap.lastCompleted = row
            if row.failed {
                snap.finding = row
                // Flash only for fresh failures, not when replaying an old events file at launch,
                // and not for "skipped" (nothing happened).
                snap.findingFlashAt = (now.timeIntervalSince(row.t) < 20 && row.failKind != .skipped) ? now : nil
            }
        case "cache_turn":
            guard let ct = CacheTurn(j) else { return }
            snap.cacheTurn = ct
            if let t = ct.ttftS {
                snap.cacheTTFTs[ct.turn] = t
                if let r = snap.run {
                    let key = r.model + "|" + (r.runner ?? "")
                    snap.cacheScaleMax[key] = max(snap.cacheScaleMax[key] ?? 0, t)
                }
            }
        case "queue_end":
            snap.status = .queueComplete
            if snap.runEndedAt == nil, snap.run != nil { snap.runEndedAt = j.date("t") ?? now }
        default:
            break   // unknown event types are ignored
        }
    }

    private func addResult(_ row: ResultRow, fromHistory: Bool) {
        // Session run durations for the queue ETA (run_start seen in an events file → run_end/run_fail).
        // The start is removed once counted, so the same run arriving from history is not counted twice.
        if let start = startTimes[row.runId] {
            let d = row.t.timeIntervalSince(start)
            if d > 0, d < 86_400 { durationSum += d; durationCount += 1; startTimes[row.runId] = nil }
        }
        let key = "\(row.runId)|\(row.failed ? "fail" : "end")"
        if let seen = resultIndex[key], abs(seen.timeIntervalSince(row.t)) < 600 {
            return   // same run already recorded (events and history both carry it)
        }
        resultIndex[key] = row.t
        rows.append(row)
        totals.add(row)
        rowsDirty = true
        if !fromHistory, snap.lastCompleted == nil || row.t >= (snap.lastCompleted?.t ?? .distantPast) {
            snap.lastCompleted = row
        }
    }

    // MARK: samples

    private func appendSample(_ s: SamplePoint) {
        if let run = snap.run, snap.runEndedAt == nil, s.t >= run.startedAt.addingTimeInterval(-1) {
            snap.peaks.absorb(s)
        }
        // Append only; an out-of-order sample marks the list for one sort per tick (tidySamples).
        if let last = snap.samples.last, s.t <= last.t { samplesUnsorted = true }
        snap.samples.append(s)
    }

    /// Once per tick: sort if needed, drop near-duplicates (sampler and direct reading of the same
    /// second; the later arrival wins), trim to the chart window and cap the count.
    private func tidySamples(now: Date) {
        if samplesUnsorted {
            samplesUnsorted = false
            // Stable by arrival for equal times, so "later arrival wins" below still holds.
            let sorted = snap.samples.enumerated().sorted { a, b in
                a.element.t != b.element.t ? a.element.t < b.element.t : a.offset < b.offset
            }.map(\.element)
            var out: [SamplePoint] = []
            out.reserveCapacity(sorted.count)
            for s in sorted {
                if let last = out.last, s.t.timeIntervalSince(last.t) < 0.5 { out[out.count - 1] = s } else { out.append(s) }
            }
            snap.samples = out
        }
        let cutoff = now.addingTimeInterval(-config.chartWindow - 5)
        let future = now.addingTimeInterval(5)
        snap.samples.removeAll { $0.t < cutoff || $0.t > future }
        if snap.samples.count > config.maxSamples { snap.samples.removeFirst(snap.samples.count - config.maxSamples) }
    }

    private func directSample(now: Date) -> SamplePoint {
        let mem = probe.memory()
        let gpu = probe.gpu()
        // Power: the built-in sampler's newest reading (< 3 s old), whether or not a run is in progress. Without it
        // (switched off), an external sampler's newest power.jsonl line during a run, if < 3 s old.
        var gpuW: Double?, pkgW: Double?
        let reading = PowerSampler.shared.latest()     // one read: power and clock from the same sample
        if let r = reading {
            gpuW = r.gpuW; pkgW = r.pkgW
        } else {
            if powerTail.path == nil, let cur = currentResolved {
                powerTail.reset(path: cur + "/power.jsonl", startFromEndBytes: 4096)
            }
            for line in powerTail.poll(now: now) {
                if let j = parseJSONLine(line) { lastPower = (now, j.dbl("gpu_power_w"), j.dbl("pkg_power_w")) }
            }
            // Fresh = the file itself was written within 3 s (stat only; ignores old backlog lines).
            let fresh = lastPower != nil && (powerTail.path.flatMap(BenchFiles.mtime).map { now.timeIntervalSince($0) < 3 } ?? false)
            gpuW = fresh ? lastPower?.gpu : nil
            pkgW = fresh ? lastPower?.pkg : nil
        }
        var s = SamplePoint(t: now, memUsedGB: mem?.usedGB, pressure: probe.pressure().rawValue,
                            swapGB: probe.swapUsedGB(), gpuUtilPct: gpu.utilizationPct,
                            gpuPowerW: gpuW, pkgPowerW: pkgW,
                            diskFreeGB: Probe.diskFreeGB(Probe.internalDataVolume),
                            diskFreeExternalGB: snap.extraVolumeFreeGB,
                            runnerRSSGB: nil, direct: true, thermal: Thermal.state(), socTempC: temps.read().dieMaxC)
        s.gpuFreqMHz = reading?.gpuFreqMHz
        return s
    }
}
