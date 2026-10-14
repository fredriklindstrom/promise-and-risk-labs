import BenchProbe
import Foundation
import Observation
import SwiftUI

/// Main-thread view model. The Engine publishes one Snapshot per second; properties are only
/// assigned when they change so SwiftUI redraws just the parts that moved (≤ 1 Hz).
@Observable
final class MonitorModel {
    var now = Date()
    var status: RunStatus = .noRunFolder
    var run: RunInfo?
    var runEndedAt: Date?
    var phase: String?
    var live: LiveSpeed?
    var media: MediaProgress?
    var lastCompleted: ResultRow?
    var finding: ResultRow?
    var findingFlashAt: Date?
    var findingFlashing = false
    var eventsAge: Double?
    var queueETA: Double?
    var avgRunSeconds: Double?
    var sessionCompleted = 0

    var results: [ResultRow] = []
    var groups: [GroupAverage] = []
    var summary = ResultsSummary()
    var fastest: Set<String> = []
    private var resultsVersion = -1

    var samples: [SamplePoint] = []
    var latest: SamplePoint?
    var sampleSource = "—"
    var gpuBudgetGB: Double = ThisMac.defaultBudgetGB
    var machineLabel: String = ThisMac.info.label       // the model id until system_profiler names it ("MacBook Air")
    var currentRunFolder: String?
    var peaks = RunPeaks()
    var powerSampling = false          // any power value in the chart window
    var externalDiskGB: Int?           // extra volume free space, rounded, so the footer only changes when the number does
    var extraVolumeName: String?       // footer label for that volume ("Models" for /Volumes/Models)
    var cacheTurn: CacheTurn?          // agent-loop cache test: current turn (nil outside that test)
    var cacheTTFTs: [Int: Double] = [:]
    var cacheScaleMax: [String: Double] = [:]

    // GPU Memory menu: the kernel's iogpu.wired_limit_mb (0 = macOS default), a preset waiting for confirmation,
    // a pending return to the default, and the last error to show.
    var gpuLimitMiB = GPULimit.currentMiB()
    var gpuLimitPending: GPULimitPreset?
    var gpuLimitDefaultPending = false
    var gpuLimitError: String?
    var powerMessage: String?          // Power menu: start/stop problems to show
    var powerOn = false                // the built-in power sampler is running
    var maxRuns = MaxRuns.enabled      // GPU Memory › Max Runs: sleep the display when each run starts
    @ObservationIgnored private var displaySleptFor: String?

    var presentation = false
    var compareMode = false            // ⌘K: up to five models side by side instead of speed/system/results
    var compareSpec = ""               // see CompareSelection.resolve; "" = the most recently run (up to five)

    @ObservationIgnored private var engine: Engine?

    /// GPU Memory menu: nil = macOS Default. Opens the confirmation; nothing changes until it is confirmed.
    func requestGPULimit(_ preset: GPULimitPreset?) {
        gpuLimitMiB = GPULimit.currentMiB()
        if let preset {
            guard GPULimit.plan(preset).available else { return }
            gpuLimitPending = preset
        } else if gpuLimitMiB != 0 {
            gpuLimitDefaultPending = true
        }
    }

    func startPowerSampling() {
        UserDefaults.standard.set(true, forKey: PowerSampler.enabledKey)
        if let e = PowerSampler.shared.start(project: config.projectDir) { powerMessage = e }
        powerOn = PowerSampler.shared.running
    }

    func stopPowerSampling() {
        UserDefaults.standard.set(false, forKey: PowerSampler.enabledKey)
        PowerSampler.shared.stop()
        powerOn = false
    }

    @MainActor
    func applyGPULimit(mib: Int) {
        if let e = GPULimit.apply(mib: mib) { gpuLimitError = e }
        gpuLimitMiB = GPULimit.currentMiB()
        ThisMac.refreshBudget()
        if status != .running && status != .stale { gpuBudgetGB = ThisMac.liveBudgetGB }
    }
    @ObservationIgnored private(set) var config: EngineConfig

    /// Project folder when nothing is configured: the project this app was built in (nearest folder
    /// above the app bundle that contains bench/harness.py), else the current directory.
    static var discoveredProjectDir: String {
        BenchProject.discover() ?? FileManager.default.currentDirectoryPath
    }

    init() {
        let env = ProcessInfo.processInfo.environment
        let defaults = UserDefaults.standard
        func nonEmpty(_ s: String?) -> String? { s.flatMap { $0.trimmingCharacters(in: .whitespaces).isEmpty ? nil : $0 } }
        // BENCH_PROJECT, then Settings, then discovery.
        let project = nonEmpty(env["BENCH_PROJECT"])
            ?? nonEmpty(defaults.string(forKey: "projectDir"))
            ?? Self.discoveredProjectDir
        let history = nonEmpty(env["BENCH_HISTORY"])
            ?? nonEmpty(defaults.string(forKey: "historyFile"))
            ?? "history.jsonl"
        let extra = Probe.extraVolume(nonEmpty(env["BENCH_EXTRA_VOLUME"]) ?? defaults.string(forKey: "extraVolume"))
        var c = EngineConfig(projectDir: (project as NSString).expandingTildeInPath, historyFile: history, extraVolume: extra)
        if let s = env["BENCH_STALE_S"].flatMap(Double.init), s > 0 { c.staleSeconds = s }
        config = c
        extraVolumeName = extra.map { ($0 as NSString).lastPathComponent }
        presentation = env["BENCH_PRESENTATION"] == "1"
        // BENCH_COMPARE="Jais 3 32B; Qwen 3.8 27B · MLX" starts in Compare with that selection (not saved).
        if let spec = nonEmpty(env["BENCH_COMPARE"]) {
            compareSpec = spec
            compareMode = true
        } else {
            compareSpec = defaults.string(forKey: CompareSelection.defaultsKey) ?? ""
        }
    }

    /// Menu / remote control: change and remember the Compare selection.
    func setCompareSpec(_ spec: String) {
        compareSpec = spec
        UserDefaults.standard.set(spec, forKey: CompareSelection.defaultsKey)
    }

    func start() {
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let name = MachineInfo.marketingName() else { return }
            var info = ThisMac.info
            info.name = name
            let label = info.label
            DispatchQueue.main.async { self?.machineLabel = label }
        }
        guard engine == nil else { return }
        // Power sampling is built in and on unless it was switched off; a failure here is silent (Power menu says why).
        if PowerSampler.enabledByDefault, PowerSampler.shared.start(project: config.projectDir) == nil { powerOn = true }
        let e = Engine(config: config) { [weak self] snap in
            DispatchQueue.main.async { self?.apply(snap) }
        }
        engine = e
        e.start()
    }

    func updateConfig(projectDir: String, historyFile: String, extraVolume: String) {
        var c = config
        let p = projectDir.trimmingCharacters(in: .whitespaces)
        c.projectDir = p.isEmpty ? Self.discoveredProjectDir : (p as NSString).expandingTildeInPath
        c.historyFile = historyFile.isEmpty ? "history.jsonl" : historyFile
        c.extraVolume = Probe.extraVolume(extraVolume)
        config = c
        extraVolumeName = c.extraVolume.map { ($0 as NSString).lastPathComponent }
        resultsVersion = -1
        PowerSampler.shared.setProject(c.projectDir)
        engine?.reconfigure(c)
    }

    private func set<T: Equatable>(_ kp: ReferenceWritableKeyPath<MonitorModel, T>, _ v: T) {
        if self[keyPath: kp] != v { self[keyPath: kp] = v }
    }

    func setMaxRuns(_ on: Bool) {
        maxRuns = on
        MaxRuns.enabled = on
    }

    private func apply(_ s: Snapshot) {
        set(\.now, s.now)
        // Max Runs: once per run, as it starts.
        if maxRuns, s.status == .running, let id = s.run?.runId, id != displaySleptFor {
            displaySleptFor = id
            MaxRuns.sleepDisplay(runId: id, project: config.projectDir)
        }
        set(\.status, s.status)
        set(\.run, s.run)
        set(\.runEndedAt, s.runEndedAt)
        set(\.phase, s.phase)
        set(\.live, s.live)
        set(\.media, s.media)
        set(\.lastCompleted, s.lastCompleted)
        set(\.finding, s.finding)
        set(\.findingFlashAt, s.findingFlashAt)
        set(\.findingFlashing, s.findingFlashing)
        set(\.eventsAge, s.eventsAge)
        set(\.queueETA, s.queueETA)
        set(\.avgRunSeconds, s.avgRunSeconds)
        set(\.sessionCompleted, s.sessionCompleted)
        set(\.samples, s.samples)
        set(\.latest, s.latest)
        set(\.sampleSource, s.sampleSource)
        set(\.peaks, s.peaks)
        // The kernel limit may also change outside the app (Terminal): re-read it each tick (one sysctl, cheap).
        let limit = GPULimit.currentMiB()
        if limit != gpuLimitMiB { gpuLimitMiB = limit; ThisMac.refreshBudget() }
        // A running run reports the budget it ran under; otherwise this Mac's current ceiling (GPU Memory menu).
        set(\.gpuBudgetGB, (s.status == .running || s.status == .stale) ? s.gpuBudgetGB : ThisMac.liveBudgetGB)
        set(\.currentRunFolder, s.currentRunFolder)
        set(\.powerSampling, s.samples.contains { $0.gpuPowerW != nil } || s.latest?.gpuPowerW != nil)
        set(\.externalDiskGB, s.extraVolumeFreeGB.flatMap { clampedInt($0.rounded()) })
        set(\.cacheTurn, s.cacheTurn)
        set(\.cacheTTFTs, s.cacheTTFTs)
        set(\.cacheScaleMax, s.cacheScaleMax)
        if s.resultsVersion != resultsVersion {
            resultsVersion = s.resultsVersion
            results = s.results
            groups = s.groups
            summary = s.summary
            fastest = s.fastest
        }
    }

    // MARK: derived

    var isActive: Bool { status == .running || status == .stale }

    /// Average for the current run's model+runner+quant+context group.
    var currentGroup: GroupAverage? {
        guard let r = run else { return nil }
        let key = [r.model, r.runner ?? "", r.quant ?? "", r.contextTokens.map(String.init) ?? "", r.kind].joined(separator: "|")
        return groups.first { $0.key == key }
    }

    /// Every model + runner + quant with language-model results, newest first.
    var contenders: [Contender] { Contender.build(groups) }

    /// The language-model run in progress, for its cell in Compare (nil between runs and for media runs).
    var liveCompareCell: LiveCell? {
        guard isActive, let r = run, !r.isMedia, let ctx = r.contextTokens, runEndedAt == nil else { return nil }
        let fresh = latest.flatMap { now.timeIntervalSince($0.t) < 10 ? $0 : nil }
        return LiveCell(contenderID: [r.model, r.runner, r.quant].compactMap { $0 }.joined(separator: " · "), ctx: ctx,
                        phase: phase, decodeTPS: live?.decodeTPS, tokens: live?.tokens, watts: fresh?.pkgPowerW,
                        elapsed: now.timeIntervalSince(r.startedAt), stale: status == .stale, warmup: r.warmup,
                        maxTokens: r.runId.contains("-needle-") ? 64 : 512)
    }

    /// What Compare shows: the resolved selection (at most five).
    var comparedContenders: [Contender] { CompareSelection.resolve(compareSpec, contenders) }

    func group(for row: ResultRow?) -> GroupAverage? {
        guard let row else { return nil }
        return groups.first { $0.key == row.groupKey }
    }
}
