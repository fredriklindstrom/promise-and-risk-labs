// Temperatures — Apple-silicon on-chip sensors through the IOHID event system (read-only, no root).
// Added 2026-10-13 for the thermal logger. These are PRIVATE, undocumented IOKit functions (the same ones
// Stats, iStat Menus and macmon use). They are looked up at runtime with dlsym, so a macOS that drops or
// renames them makes every reading nil instead of crashing the app or the sampler.
// They are only touched on macOS versions they have been seen to work on (14 and later), and never when
// BENCH_NO_PRIVATE_SENSORS=1 is set; in both cases every temperature is null.
//
// Sensor names differ per chip generation. Classification:
//   die  = names containing "tdie"  (SoC die sensors: "PMU tdie0…" on M2 Max) — the headline number
//   ssd  = names starting "NAND"
//   skip = "tcal" (calibration reference, not a temperature), battery/gas gauge
// If a Mac exposes no "tdie" sensor, die falls back to every non-skipped sensor (logged via `bench-sampler --sensors`).
import Darwin
import Foundation

public struct TemperatureReading: Sendable {
    public var dieMaxC: Double?
    public var dieAvgC: Double?
    public var ssdC: Double?
    public var sensorCount: Int
    public var dieSensorNames: String     // "tdie" or "fallback:all"
}

public final class Temperatures {
    private typealias CreateF = @convention(c) (CFAllocator?) -> Unmanaged<AnyObject>?
    private typealias SetMatchF = @convention(c) (AnyObject, CFDictionary) -> Int32
    private typealias CopyServicesF = @convention(c) (AnyObject) -> Unmanaged<CFArray>?
    private typealias CopyPropF = @convention(c) (AnyObject, CFString) -> Unmanaged<AnyObject>?
    private typealias CopyEventF = @convention(c) (AnyObject, Int64, Int32, Int64) -> Unmanaged<AnyObject>?
    private typealias FloatF = @convention(c) (AnyObject, Int32) -> Double

    private static let kTemperatureEvent: Int64 = 15                    // kIOHIDEventTypeTemperature
    private static let kTemperatureField: Int32 = Int32(15 << 16)       // IOHIDEventFieldBase(kIOHIDEventTypeTemperature)

    private let copyServices: CopyServicesF?
    private let copyProp: CopyPropF?
    private let copyEvent: CopyEventF?
    private let floatValue: FloatF?
    private let client: AnyObject?
    private var services: [(name: String, svc: AnyObject)] = []
    private var lastRefresh = Date.distantPast

    /// Oldest macOS major version the private API has been seen working on.
    public static let minimumMacOSMajor = 14
    /// Why `available` is false (for `bench-sampler --sensors`); nil when sensors are readable.
    public private(set) var unavailableReason: String?

    public init() {
        if ProcessInfo.processInfo.environment["BENCH_NO_PRIVATE_SENSORS"] == "1" {
            copyServices = nil; copyProp = nil; copyEvent = nil; floatValue = nil; client = nil
            unavailableReason = "disabled by BENCH_NO_PRIVATE_SENSORS=1"
            return
        }
        let major = ProcessInfo.processInfo.operatingSystemVersion.majorVersion
        guard major >= Self.minimumMacOSMajor else {
            copyServices = nil; copyProp = nil; copyEvent = nil; floatValue = nil; client = nil
            unavailableReason = "not used on macOS \(major) (only enabled on macOS \(Self.minimumMacOSMajor) and later)"
            return
        }
        let h = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit", RTLD_NOW)
        func sym<T>(_ n: String, _ t: T.Type) -> T? { h.flatMap { dlsym($0, n) }.map { unsafeBitCast($0, to: t) } }
        let create = sym("IOHIDEventSystemClientCreate", CreateF.self)
        let setMatch = sym("IOHIDEventSystemClientSetMatching", SetMatchF.self)
        copyServices = sym("IOHIDEventSystemClientCopyServices", CopyServicesF.self)
        copyProp = sym("IOHIDServiceClientCopyProperty", CopyPropF.self)
        copyEvent = sym("IOHIDServiceClientCopyEvent", CopyEventF.self)
        floatValue = sym("IOHIDEventGetFloatValue", FloatF.self)
        if let create, let setMatch, copyServices != nil, copyProp != nil, copyEvent != nil, floatValue != nil,
           let c = create(kCFAllocatorDefault)?.takeRetainedValue() {
            // Apple-vendor usage page 0xff00, usage 5 = temperature sensors.
            _ = setMatch(c, ["PrimaryUsagePage": 0xff00, "PrimaryUsage": 5] as CFDictionary)
            client = c
        } else {
            client = nil
            unavailableReason = "private IOHID API not found on this macOS"
        }
    }

    public var available: Bool { client != nil }

    private func refreshServices() {
        guard let client, let copyServices, let copyProp else { return }
        let list = (copyServices(client)?.takeRetainedValue() as? [AnyObject]) ?? []
        services = list.map { s in ((copyProp(s, "Product" as CFString)?.takeRetainedValue() as? String) ?? "?", s) }
        lastRefresh = Date()
    }

    /// Every sensor as (name, °C). Plausibility filter: 0 < t < 130 °C.
    public func all() -> [(String, Double)] {
        guard client != nil, let copyEvent, let floatValue else { return [] }
        if services.isEmpty || Date().timeIntervalSince(lastRefresh) > 300 { refreshServices() }
        var out: [(String, Double)] = []
        for (name, s) in services {
            guard let e = copyEvent(s, Self.kTemperatureEvent, 0, 0)?.takeRetainedValue() else { continue }
            let v = floatValue(e, Self.kTemperatureField)
            if v > 0, v < 130 { out.append((name, v)) }
        }
        return out
    }

    public func read() -> TemperatureReading {
        let a = all()
        let skip = { (n: String) -> Bool in
            let l = n.lowercased(); return l.contains("tcal") || l.contains("battery") || l.contains("gas gauge")
        }
        var die = a.filter { $0.0.lowercased().contains("tdie") }.map { $0.1 }
        var source = "tdie"
        if die.isEmpty {
            die = a.filter { !skip($0.0) && !$0.0.uppercased().hasPrefix("NAND") }.map { $0.1 }
            source = "fallback:all"
        }
        let ssd = a.filter { $0.0.uppercased().hasPrefix("NAND") }.map { $0.1 }.max()
        return TemperatureReading(dieMaxC: die.max(), dieAvgC: die.isEmpty ? nil : die.reduce(0, +) / Double(die.count),
                                  ssdC: ssd, sensorCount: a.count, dieSensorNames: source)
    }
}
