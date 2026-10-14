// EnergySampler — CPU / GPU / ANE / DRAM power from Apple silicon's energy counters, without root.
// Added 2026-10-24 so Bench Monitor can sample power itself instead of asking for powermetrics (root).
//
// Source: IOReport, the counter library powermetrics itself reads. It is PRIVATE and undocumented (macmon and
// mactop use it the same way), so every function is looked up at runtime with dlsym from the absolute path
// /usr/lib/libIOReport.dylib: a macOS that drops or renames it makes `available` false and every reading nil,
// never a crash. Read-only: the counters are subscribed to and sampled, nothing is written. Not used before
// macOS 14, or when BENCH_NO_PRIVATE_SENSORS=1 is set (the same opt-out as the temperature sensors).
//
// Channels (group "Energy Model"; names checked on M2 Max, macOS 27):
//   cpu  = names ending "CPU Energy"   (sum of the CPU clusters; mJ. Ultra chips name it per die: "DIE_0_CPU Energy")
//   gpu  = names ending "GPU Energy"   (nJ; same per-die rule)
//   ane  = every channel starting "ANE"  (ANE0, ANE1 on multi-die chips)
//   dram = every channel starting "DRAM"
// pkg = cpu + gpu + ane, the same definition as powermetrics' combined_power (Apple silicon has no package
// counter). DRAM is reported separately and is not in pkg.
// GPU clock and active share come from group "GPU Stats" / "GPU Performance States" channel "GPUPH" (residency
// per state: OFF, P1…P15) and the GPU DVFS table in the IORegistry ("pmgr" property "voltage-states9":
// little-endian (Hz, mV) pairs, a 0 Hz entry first). P-state n maps to the n-th non-zero frequency; if time is
// spent in a state the table does not cover, the clock is nil for that sample. A table outside 100–5000 MHz is
// treated as unreadable (some chips publish it in other units).
// Intervals are measured on CLOCK_MONOTONIC_RAW (immune to clock changes); an interval over 5 s (sleep, a stall,
// a restart) is not reported, because its average would not describe any one second.
import Darwin
import Foundation
import IOKit

public struct PowerReading: Sendable {
    public var t: Date
    public var seconds: Double            // length of the interval the energy was measured over
    public var cpuW: Double?
    public var gpuW: Double?
    public var aneW: Double?
    public var dramW: Double?
    public var pkgW: Double?
    public var gpuFreqMHz: Double?        // residency-weighted average over the active (non-OFF) time
    public var gpuActivePct: Double?      // share of the interval the GPU was not OFF
}

public final class EnergySampler {
    private typealias CopyChannelsF = @convention(c) (CFString, CFString?, UInt64, UInt64, UInt64) -> Unmanaged<CFMutableDictionary>?
    private typealias MergeF = @convention(c) (CFMutableDictionary, CFMutableDictionary, CFTypeRef?) -> Void
    private typealias CreateSubF = @convention(c) (UnsafeMutableRawPointer?, CFMutableDictionary,
                                                   UnsafeMutablePointer<Unmanaged<CFMutableDictionary>?>, UInt64, CFTypeRef?) -> OpaquePointer?
    private typealias CreateSamplesF = @convention(c) (OpaquePointer, CFMutableDictionary, CFTypeRef?) -> Unmanaged<CFDictionary>?
    private typealias DeltaF = @convention(c) (CFDictionary, CFDictionary, CFTypeRef?) -> Unmanaged<CFDictionary>?
    private typealias StringF = @convention(c) (CFDictionary) -> Unmanaged<CFString>?
    private typealias IntegerF = @convention(c) (CFDictionary, Int32) -> Int64
    private typealias StateCountF = @convention(c) (CFDictionary) -> Int32
    private typealias StateNameF = @convention(c) (CFDictionary, Int32) -> Unmanaged<CFString>?
    private typealias StateResidencyF = @convention(c) (CFDictionary, Int32) -> Int64

    private struct API {
        let copyChannels: CopyChannelsF, merge: MergeF, createSub: CreateSubF, createSamples: CreateSamplesF
        let delta: DeltaF, group: StringF, name: StringF, unit: StringF, integer: IntegerF
        let stateCount: StateCountF, stateName: StateNameF, stateResidency: StateResidencyF
    }

    private let api: API?
    private var subscription: OpaquePointer?
    private var subscribed: CFMutableDictionary?
    private var previous: (samples: CFDictionary, ns: UInt64)?
    private let gpuFreqsMHz: [Double]
    private let lock = NSLock()

    /// Why `available` is false; nil when the counters are readable.
    public private(set) var unavailableReason: String?
    public var available: Bool { subscription != nil }

    public init() {
        gpuFreqsMHz = EnergySampler.gpuFrequencyTable()
        if ProcessInfo.processInfo.environment["BENCH_NO_PRIVATE_SENSORS"] == "1" {
            api = nil; unavailableReason = "disabled by BENCH_NO_PRIVATE_SENSORS=1"; return
        }
        let major = ProcessInfo.processInfo.operatingSystemVersion.majorVersion
        guard major >= Temperatures.minimumMacOSMajor else {
            api = nil; unavailableReason = "not used on macOS \(major) (only on macOS \(Temperatures.minimumMacOSMajor) and later)"; return
        }
        guard let h = dlopen("/usr/lib/libIOReport.dylib", RTLD_NOW) else {
            api = nil; unavailableReason = "libIOReport not found on this macOS"; return
        }
        func sym<T>(_ n: String, _ t: T.Type) -> T? { dlsym(h, n).map { unsafeBitCast($0, to: t) } }
        guard let copyChannels = sym("IOReportCopyChannelsInGroup", CopyChannelsF.self),
              let merge = sym("IOReportMergeChannels", MergeF.self),
              let createSub = sym("IOReportCreateSubscription", CreateSubF.self),
              let createSamples = sym("IOReportCreateSamples", CreateSamplesF.self),
              let delta = sym("IOReportCreateSamplesDelta", DeltaF.self),
              let group = sym("IOReportChannelGetGroup", StringF.self),
              let name = sym("IOReportChannelGetChannelName", StringF.self),
              let unit = sym("IOReportChannelGetUnitLabel", StringF.self),
              let integer = sym("IOReportSimpleGetIntegerValue", IntegerF.self),
              let stateCount = sym("IOReportStateGetCount", StateCountF.self),
              let stateName = sym("IOReportStateGetNameForIndex", StateNameF.self),
              let stateResidency = sym("IOReportStateGetResidency", StateResidencyF.self) else {
            api = nil; unavailableReason = "IOReport functions not found on this macOS"; return
        }
        let a = API(copyChannels: copyChannels, merge: merge, createSub: createSub, createSamples: createSamples,
                    delta: delta, group: group, name: name, unit: unit, integer: integer,
                    stateCount: stateCount, stateName: stateName, stateResidency: stateResidency)
        api = a
        guard let channels = a.copyChannels("Energy Model" as CFString, nil, 0, 0, 0)?.takeRetainedValue() else {
            unavailableReason = "no \"Energy Model\" counters on this Mac"; return
        }
        if let gpu = a.copyChannels("GPU Stats" as CFString, "GPU Performance States" as CFString, 0, 0, 0)?.takeRetainedValue() {
            a.merge(channels, gpu, nil)
        }
        var out: Unmanaged<CFMutableDictionary>?
        // The subscribed-channels out-parameter's ownership is undocumented; take it +0 and retain it ourselves
        // (at worst one dictionary leaks, never an over-release).
        guard let sub = a.createSub(nil, channels, &out, 0, nil), let subbed = out?.takeUnretainedValue() else {
            unavailableReason = "could not subscribe to the energy counters"; return
        }
        subscription = sub
        subscribed = subbed
    }

    deinit {
        // IOReportSubscriptionRef is a CF object returned by a Create function.
        if let s = subscription { Unmanaged<AnyObject>.fromOpaque(UnsafeRawPointer(s)).release() }
    }

    /// Forget the baseline: the next `sample()` only sets a new one. Call when sampling restarts.
    public func reset() {
        lock.lock(); defer { lock.unlock() }
        previous = nil
    }

    /// Energy since the previous call, as average watts. The first call (and the first after `reset()` or a gap of
    /// more than 5 s) only sets the baseline and returns nil.
    /// Safe to call from any thread (one caller at a time is enforced).
    public func sample() -> PowerReading? {
        lock.lock(); defer { lock.unlock() }
        guard let a = api, let sub = subscription, let subbed = subscribed,
              let now = a.createSamples(sub, subbed, nil)?.takeRetainedValue() else { return nil }
        let t = Date()
        let ns = clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW)
        defer { previous = (now, ns) }
        guard let prev = previous, ns > prev.ns else { return nil }
        let seconds = Double(ns - prev.ns) / 1e9
        guard seconds > 0.05, seconds <= 5, let d = a.delta(prev.samples, now, nil)?.takeRetainedValue(),
              let raw = (d as NSDictionary)["IOReportChannels"] as? [AnyObject] else { return nil }
        // Element types can't be checked by a Swift cast on a CF array: check each one before handing it to IOReport.
        let items = raw.compactMap { CFGetTypeID($0) == CFDictionaryGetTypeID() ? ($0 as! CFDictionary) : nil }

        var cpuJ: Double?, gpuJ: Double?, aneJ: Double?, dramJ: Double?
        var gpuStates: [(name: String, residency: Int64)] = []
        for ch in items {
            let group = a.group(ch)?.takeUnretainedValue() as String? ?? ""
            let name = a.name(ch)?.takeUnretainedValue() as String? ?? ""
            if group == "Energy Model" {
                guard let joules = Self.joules(a.integer(ch, 0), unit: a.unit(ch)?.takeUnretainedValue() as String?) else { continue }
                if name.hasSuffix("CPU Energy") { cpuJ = (cpuJ ?? 0) + joules }
                else if name.hasSuffix("GPU Energy") { gpuJ = (gpuJ ?? 0) + joules }
                else if name.hasPrefix("ANE") { aneJ = (aneJ ?? 0) + joules }
                else if name.hasPrefix("DRAM") { dramJ = (dramJ ?? 0) + joules }
            } else if group == "GPU Stats", name == "GPUPH" {
                gpuStates = (0..<max(0, a.stateCount(ch))).map { i in
                    (a.stateName(ch, i)?.takeUnretainedValue() as String? ?? "", a.stateResidency(ch, i))
                }
            }
        }
        var r = PowerReading(t: t, seconds: seconds)
        r.cpuW = cpuJ.map { $0 / seconds }
        r.gpuW = gpuJ.map { $0 / seconds }
        r.aneW = aneJ.map { $0 / seconds }
        r.dramW = dramJ.map { $0 / seconds }
        if r.cpuW != nil || r.gpuW != nil { r.pkgW = (r.cpuW ?? 0) + (r.gpuW ?? 0) + (r.aneW ?? 0) }
        (r.gpuActivePct, r.gpuFreqMHz) = gpuClock(gpuStates)
        return r
    }

    /// Joules from an IOReport energy value and its unit label; nil for a unit we do not know.
    private static func joules(_ value: Int64, unit: String?) -> Double? {
        switch unit {
        case "mJ": return Double(value) / 1e3
        case "uJ", "µJ": return Double(value) / 1e6
        case "nJ": return Double(value) / 1e9
        default: return nil
        }
    }

    private func gpuClock(_ states: [(name: String, residency: Int64)]) -> (Double?, Double?) {
        let total = states.reduce(0.0) { $0 + Double(max(0, $1.residency)) }
        guard total > 0 else { return (nil, nil) }
        var active = 0.0, weighted = 0.0, covered = true
        for s in states where s.name.hasPrefix("P") {
            let res = Double(max(0, s.residency))
            guard res > 0 else { continue }
            active += res
            if let n = Int(s.name.dropFirst()), n >= 1, n <= gpuFreqsMHz.count {
                weighted += res * gpuFreqsMHz[n - 1]
            } else {
                covered = false
            }
        }
        let pct = active / total * 100
        let mhz = active > 0 && covered ? weighted / active : nil
        return (pct, mhz)
    }

    /// The GPU's DVFS frequencies in MHz, ascending, without the 0 Hz entry; empty when unreadable.
    private static func gpuFrequencyTable() -> [Double] {
        var iter: io_iterator_t = 0
        guard IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("AppleARMIODevice"), &iter) == KERN_SUCCESS else { return [] }
        defer { IOObjectRelease(iter) }
        while case let entry = IOIteratorNext(iter), entry != 0 {
            defer { IOObjectRelease(entry) }
            var nameBuf = [CChar](repeating: 0, count: 128)
            guard IORegistryEntryGetName(entry, &nameBuf) == KERN_SUCCESS, String(cString: nameBuf) == "pmgr" else { continue }
            guard let data = IORegistryEntryCreateCFProperty(entry, "voltage-states9" as CFString, kCFAllocatorDefault, 0)?
                    .takeRetainedValue() as? Data, data.count % 8 == 0 else { return [] }
            var out: [Double] = []
            for i in stride(from: 0, to: data.count, by: 8) {
                let hz = data.subdata(in: i..<(i + 4)).withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) }.littleEndian
                if hz > 0 { out.append(Double(hz) / 1e6) }
            }
            return out.allSatisfy({ $0 >= 100 && $0 <= 5000 }) ? out : []
        }
        return []
    }
}
