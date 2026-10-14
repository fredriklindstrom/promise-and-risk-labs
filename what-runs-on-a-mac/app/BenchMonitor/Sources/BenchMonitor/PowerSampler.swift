import BenchProbe
import Foundation

/// Power (rewritten 2026-10-24): sampled by the app itself, not by a root helper the user has to start.
///
/// Sampled inside the app from Apple silicon's energy counters (BenchProbe.EnergySampler, the counters powermetrics
/// reads): no root, no administrator prompt, no one-time install. One reading per second on its own queue. Each is
/// written as a power line (BenchProbe.PowerLog) to runs/current/power.jsonl and runs/power_all.jsonl, where the old
/// root sampler wrote, so bench-sampler, the harness's peaks and energy, and this app's charts read it unchanged.
/// Checked against powermetrics on 2026-10-24 (same seconds, two GPU loads): energy within 0.9 % (package).
///
/// On by default; Power › Stop turns it off and the choice is remembered. Whenever another process is writing power
/// lines (a powermetrics sampler, or bench-power started by hand: power_all.jsonl's newest line is under 3 s old and
/// not ours), this sampler still feeds the app's display but writes nothing, so the files never carry two streams.
/// It checks before every write, so it takes over within seconds once the other writer stops.
final class PowerSampler {
    static let shared = PowerSampler()
    static let enabledKey = "powerSamplingEnabled"
    static var enabledByDefault: Bool {
        UserDefaults.standard.object(forKey: enabledKey) as? Bool ?? true
    }

    private let queue = DispatchQueue(label: "benchmonitor.power", qos: .utility, autoreleaseFrequency: .workItem)
    private let energy = EnergySampler()
    private let lock = NSLock()
    private var timer: DispatchSourceTimer?
    private var project = ""
    private var latestReading: PowerReading?
    private var deferring = false            // another writer owns the power files: display only

    var running: Bool { lock.withLock { timer != nil } }
    /// True while another process is writing the power files (this one only feeds the display).
    var deferringToExternal: Bool { lock.withLock { deferring } }

    /// Starts sampling into `project`. Returns nil on success, or why it could not start.
    func start(project: String) -> String? {
        guard energy.available else {
            return "Power sampling is not available on this Mac: \(energy.unavailableReason ?? "the energy counters could not be read")."
        }
        lock.withLock {
            self.project = project
            guard timer == nil else { return }
            energy.reset()                        // the first tick only sets a fresh baseline
            let t = DispatchSource.makeTimerSource(queue: queue)
            t.schedule(deadline: .now(), repeating: 1.0, leeway: .milliseconds(50))
            t.setEventHandler { [weak self] in self?.tick() }
            timer = t
            t.resume()
        }
        return nil
    }

    func stop() {
        lock.withLock {
            timer?.cancel()
            timer = nil
            latestReading = nil
            deferring = false
        }
    }

    func setProject(_ p: String) { lock.withLock { project = p } }

    /// The newest reading, if it is at most `maxAge` seconds old.
    func latest(maxAge: TimeInterval = 3) -> PowerReading? {
        lock.withLock {
            guard let r = latestReading, Date().timeIntervalSince(r.t) <= maxAge else { return nil }
            return r
        }
    }

    private func tick() {
        guard let r = energy.sample() else { return }      // nil: baseline, or a gap over 5 s
        let p: String? = lock.withLock {
            guard timer != nil else { return nil }          // stopped while this tick was in flight
            latestReading = r
            return project
        }
        guard let p, !p.isEmpty else { return }
        let other = PowerLog.otherWriterActive(project: p)
        lock.withLock { deferring = other }
        if !other { PowerLog.emit(PowerLog.line(r), project: p) }
    }
}
