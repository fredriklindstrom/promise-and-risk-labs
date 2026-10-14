// MachineInfo — what this Mac actually is, read once at startup (no root).
// Added 2026-10-13: Bench Monitor used to show hard-coded figures (96 GB, M2 Max, 38-core GPU) on every Mac.
import Darwin
import Foundation
import IOKit
import Metal

/// macOS's own thermal state for this machine (public API, no root): nominal < fair < serious < critical.
/// "fair"/"serious" mean the system is already reducing performance to shed heat.
public enum Thermal {
    public static func state() -> String {
        switch ProcessInfo.processInfo.thermalState {
        case .nominal: return "nominal"
        case .fair: return "fair"
        case .serious: return "serious"
        case .critical: return "critical"
        @unknown default: return "unknown"
        }
    }
    public static let rank: [String: Int] = ["nominal": 0, "fair": 1, "moderate": 1, "serious": 2, "heavy": 2, "critical": 3, "trapping": 3, "sleeping": 3]
}

public struct MachineInfo: Sendable {
    public var name: String            // "Mac Studio", "MacBook Air" (system_profiler); model id until that answers
    public var modelID: String         // hw.model (the Mac model identifier)
    public var chip: String            // machdep.cpu.brand_string, e.g. "Apple M2 Max"
    public var memoryGB: Double        // hw.memsize, binary GB (96, 16, …)
    public var gpuCores: Int?          // AGXAccelerator "gpu-core-count"
    public var performanceCores: Int?  // hw.perflevel0.physicalcpu
    public var efficiencyCores: Int?   // hw.perflevel1.physicalcpu
    public var metalBudgetGB: Double?  // GPU memory ceiling, 10^9-byte GB: iogpu.wired_limit_mb when raised, else MTLDevice.recommendedMaxWorkingSetSize
    public var diskTotalGB: Double?    // internal data volume capacity

    /// e.g. "MacBook Air · Apple M3 · 10-core GPU · 16 GB"
    public var label: String {
        var parts = [name, chip]
        if let g = gpuCores { parts.append("\(g)-core GPU") }
        parts.append("\(Int(memoryGB.rounded())) GB")
        return parts.joined(separator: " · ")
    }

    /// Disk warning levels when no run has announced its own (the harness sends its manifest's disk_guard in run_start):
    /// floor = 5 % of the disk, at most 150 GB; warn = floor × 5/3.
    public var defaultDiskFloorGB: Double { min(150, ((diskTotalGB ?? 1000) * 0.05).rounded()) }
    public var defaultDiskWarnGB: Double { (defaultDiskFloorGB * 5 / 3).rounded() }

    public static func detect(includeName: Bool = false) -> MachineInfo {
        let mem = Double(sysctlInt("hw.memsize") ?? 0) / 1_073_741_824
        let model = sysctlString("hw.model") ?? "Mac"
        var info = MachineInfo(
            name: includeName ? (marketingName() ?? model) : model,
            modelID: model,
            chip: sysctlString("machdep.cpu.brand_string") ?? "Apple silicon",
            memoryGB: mem,
            gpuCores: gpuCoreCount(),
            performanceCores: sysctlInt("hw.perflevel0.physicalcpu"),
            efficiencyCores: sysctlInt("hw.perflevel1.physicalcpu"),
            metalBudgetGB: metalBudget(),
            diskTotalGB: nil)
        if let v = try? URL(fileURLWithPath: Probe.internalDataVolume).resourceValues(forKeys: [.volumeTotalCapacityKey]),
           let cap = v.volumeTotalCapacity {
            info.diskTotalGB = Double(cap) / 1e9
        }
        return info
    }

    /// GPU memory ceiling in 10^9-byte GB, read the way the harness (bench/system.py gpu_budget_gb) and oMLX read it:
    /// a raised `iogpu.wired_limit_mb` wins when set (> 0); otherwise Metal's recommendedMaxWorkingSetSize.
    public static func metalBudget() -> Double? {
        if let mb = sysctlInt("iogpu.wired_limit_mb"), mb > 0 { return Double(mb) * 1_048_576 / 1e9 }
        return MTLCreateSystemDefaultDevice().map { Double($0.recommendedMaxWorkingSetSize) / 1e9 }
    }

    /// "Mac Studio", "MacBook Air" — from system_profiler (≈0.5 s; call off the main thread).
    public static func marketingName() -> String? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/sbin/system_profiler")
        p.arguments = ["SPHardwareDataType", "-json", "-detailLevel", "mini"]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        guard let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let items = j["SPHardwareDataType"] as? [[String: Any]],
              let name = items.first?["machine_name"] as? String, !name.isEmpty else { return nil }
        return name
    }

    static func gpuCoreCount() -> Int? {
        let service = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("AGXAccelerator"))
        guard service != 0 else { return nil }
        defer { IOObjectRelease(service) }
        let v = IORegistryEntryCreateCFProperty(service, "gpu-core-count" as CFString, kCFAllocatorDefault, 0)?.takeRetainedValue()
        return (v as? NSNumber)?.intValue
    }

    static func sysctlString(_ name: String) -> String? {
        var size = 0
        guard sysctlbyname(name, nil, &size, nil, 0) == 0, size > 0 else { return nil }
        var buf = [CChar](repeating: 0, count: size)
        guard sysctlbyname(name, &buf, &size, nil, 0) == 0 else { return nil }
        return String(cString: buf)
    }

    static func sysctlInt(_ name: String) -> Int? {
        var v: Int64 = 0
        var size = MemoryLayout<Int64>.size
        guard sysctlbyname(name, &v, &size, nil, 0) == 0 else {
            var v32: Int32 = 0
            var s32 = MemoryLayout<Int32>.size
            return sysctlbyname(name, &v32, &s32, nil, 0) == 0 ? Int(v32) : nil
        }
        return Int(v)
    }
}
