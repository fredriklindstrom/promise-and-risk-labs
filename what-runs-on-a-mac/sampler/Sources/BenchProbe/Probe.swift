// BenchProbe — unprivileged system readings shared by bench-sampler and Bench Monitor.
// Everything here works as a normal user: no root, no entitlements, Apple frameworks only.

import Darwin
import Foundation
import IOKit

public let bytesPerGB = 1_073_741_824.0  // GiB, same unit Activity Monitor labels "GB"

// MARK: - Memory

public struct MemoryReading: Sendable {
    public var usedGB: Double        // app + wired + compressed (see formula below)
    public var appGB: Double
    public var wiredGB: Double
    public var compressedGB: Double
    public var freeGB: Double        // truly free pages (vm_stat "Pages free")
    public var cachedGB: Double      // file-backed pages + purgeable (reclaimable cache)
    public var totalGB: Double
}

public enum MemoryPressure: String, Sendable {
    case normal, warn, critical
}

public final class Probe {
    private let host: mach_port_t = mach_host_self()   // fetched once; mach_host_self() leaks a send right per call
    private let pageSize: Double = Double(sysconf(_SC_PAGESIZE))
    private let totalBytes: Double
    private var gpuService: io_registry_entry_t = 0

    public init() {
        var mem: UInt64 = 0
        var size = MemoryLayout<UInt64>.size
        sysctlbyname("hw.memsize", &mem, &size, nil, 0)
        totalBytes = Double(mem)
    }

    deinit {
        if gpuService != 0 { IOObjectRelease(gpuService) }
    }

    /// Memory from host_statistics64(HOST_VM_INFO64).
    ///
    /// Formula (chosen to match Activity Monitor's "Memory Used" = App Memory + Wired Memory + Compressed):
    ///   app memory  = internal_page_count - purgeable_count      (anonymous pages, minus purgeable)
    ///   wired       = wire_count
    ///   compressed  = compressor_page_count                      (physical pages the compressor occupies)
    ///   used        = app memory + wired + compressed
    ///
    /// This is the commonly used reconstruction of Activity Monitor's figure (the exact formula
    /// is not published). The "active + wired + compressed" shortcut over-counts file cache that
    /// sits on the active list and under-counts anonymous memory on the inactive list, so it
    /// drifts from Activity Monitor on a busy machine; the internal-page form is the closer match.
    /// (Not yet checked side by side against the Activity Monitor window on this Mac.)
    /// Note `top`'s "PhysMem used" is a different, larger figure: it also counts file cache.
    /// free = free_count - speculative_count (what `vm_stat` prints as "Pages free").
    public func memory() -> MemoryReading? {
        var stats = vm_statistics64()
        var count = mach_msg_type_number_t(MemoryLayout<vm_statistics64_data_t>.size / MemoryLayout<integer_t>.size)
        let kr = withUnsafeMutablePointer(to: &stats) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                host_statistics64(host, HOST_VM_INFO64, $0, &count)
            }
        }
        guard kr == KERN_SUCCESS else { return nil }
        let p = pageSize
        let anonymous = Double(stats.internal_page_count)
        let purgeable = Double(stats.purgeable_count)
        let app = max(0, anonymous - purgeable) * p
        let wired = Double(stats.wire_count) * p
        let compressed = Double(stats.compressor_page_count) * p
        let free = max(0, Double(stats.free_count) - Double(stats.speculative_count)) * p
        let cached = (Double(stats.external_page_count) + purgeable) * p
        return MemoryReading(
            usedGB: (app + wired + compressed) / bytesPerGB,
            appGB: app / bytesPerGB,
            wiredGB: wired / bytesPerGB,
            compressedGB: compressed / bytesPerGB,
            freeGB: free / bytesPerGB,
            cachedGB: cached / bytesPerGB,
            totalGB: totalBytes / bytesPerGB)
    }

    /// kern.memorystatus_vm_pressure_level: 1 = normal, 2 = warn, 4 = critical.
    public func pressure() -> MemoryPressure {
        var level: Int32 = 0
        var size = MemoryLayout<Int32>.size
        guard sysctlbyname("kern.memorystatus_vm_pressure_level", &level, &size, nil, 0) == 0 else { return .normal }
        switch level {
        case 4: return .critical
        case 2: return .warn
        default: return .normal
        }
    }

    /// vm.swapusage → xsu_used, in GB.
    public func swapUsedGB() -> Double? {
        var usage = xsw_usage()
        var size = MemoryLayout<xsw_usage>.size
        guard sysctlbyname("vm.swapusage", &usage, &size, nil, 0) == 0 else { return nil }
        return Double(usage.xsu_used) / bytesPerGB
    }

    // MARK: - GPU

    public struct GPUReading: Sendable {
        public var utilizationPct: Double?
        public var inUseSystemMemoryGB: Double?   // "In use system memory": GPU-resident allocations (Metal)
        public var allocSystemMemoryGB: Double?   // "Alloc system memory"
    }

    /// IOAccelerator → PerformanceStatistics → "Device Utilization %". Readable without root.
    public func gpu() -> GPUReading {
        if gpuService == 0 {
            var iter: io_iterator_t = 0
            guard IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("IOAccelerator"), &iter) == KERN_SUCCESS else {
                return GPUReading()
            }
            defer { IOObjectRelease(iter) }
            while true {
                let svc = IOIteratorNext(iter)
                if svc == 0 { break }
                if gpuService == 0, readStats(svc) != nil {
                    gpuService = svc            // keep the first accelerator that has statistics
                } else {
                    IOObjectRelease(svc)
                }
            }
        }
        guard gpuService != 0, let stats = readStats(gpuService) else {
            if gpuService != 0 { IOObjectRelease(gpuService); gpuService = 0 }  // re-discover next time
            return GPUReading()
        }
        func num(_ k: String) -> Double? { (stats[k] as? NSNumber)?.doubleValue }
        return GPUReading(
            utilizationPct: num("Device Utilization %"),
            inUseSystemMemoryGB: num("In use system memory").map { $0 / bytesPerGB },
            allocSystemMemoryGB: num("Alloc system memory").map { $0 / bytesPerGB })
    }

    private func readStats(_ svc: io_registry_entry_t) -> [String: Any]? {
        guard let cf = IORegistryEntryCreateCFProperty(svc, "PerformanceStatistics" as CFString, kCFAllocatorDefault, 0) else { return nil }
        return cf.takeRetainedValue() as? [String: Any]
    }

    // MARK: - Process memory

    public struct ProcessMemory: Sendable {
        public var residentGB: Double     // RSS (ri_resident_size)
        public var footprintGB: Double    // phys_footprint — Activity Monitor's "Memory" column
    }

    /// proc_pid_rusage(RUSAGE_INFO_V4) — works for the user's own processes without root.
    /// NOTE: MLX / Metal unified-memory allocations are GPU (IOAccelerator) memory mapped into the
    /// process; they may not show fully in RSS. phys_footprint usually includes them, so both are
    /// reported. For whole-GPU allocations see GPUReading.inUseSystemMemoryGB.
    public static func processMemory(pid: Int32) -> ProcessMemory? {
        guard pid > 0 else { return nil }
        var info = rusage_info_v4()
        let r = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: rusage_info_t?.self, capacity: 1) { proc_pid_rusage(pid, RUSAGE_INFO_V4, $0) }
        }
        guard r == 0 else { return nil }
        return ProcessMemory(residentGB: Double(info.ri_resident_size) / bytesPerGB,
                             footprintGB: Double(info.ri_phys_footprint) / bytesPerGB)
    }

    // MARK: - Disk

    /// Free space via statfs(2): f_bavail * f_bsize — blocks available to a non-root user right now.
    /// Deliberately NOT volumeAvailableCapacityForImportantUsage: that figure adds purgeable space
    /// the OS *could* reclaim (and can be expensive to compute at 1 Hz). statfs is the conservative,
    /// cheap number — what a 30 GB model download can actually use without waiting for a purge.
    /// Returns nil if `requireMountPoint` and the path is not itself a mount point (e.g. an
    /// unmounted /Volumes/X that is just an empty folder on the boot volume).
    public static func diskFreeGB(_ path: String, requireMountPoint: Bool = false) -> Double? {
        var s = statfs()
        guard statfs(path, &s) == 0 else { return nil }
        if requireMountPoint {
            let mnt = withUnsafePointer(to: &s.f_mntonname) {
                $0.withMemoryRebound(to: CChar.self, capacity: Int(MAXPATHLEN)) { String(cString: $0) }
            }
            if mnt != path { return nil }
        }
        return Double(s.f_bavail) * Double(s.f_bsize) / bytesPerGB
    }

    public static let internalDataVolume = "/System/Volumes/Data"

    /// Optional second volume to report (JSON field `disk_free_gb_external`), e.g. an external
    /// drive that holds models. No default: set BENCH_EXTRA_VOLUME=/Volumes/Name to enable it.
    /// Returned without a trailing slash so the mount-point comparison in diskFreeGB matches.
    public static func extraVolume(_ raw: String?) -> String? {
        guard var p = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !p.isEmpty else { return nil }
        p = (p as NSString).expandingTildeInPath
        while p.count > 1, p.hasSuffix("/") { p.removeLast() }
        return p.hasPrefix("/") ? p : nil
    }
    public static var extraVolumeFromEnvironment: String? {
        extraVolume(ProcessInfo.processInfo.environment["BENCH_EXTRA_VOLUME"])
    }
}

// MARK: - Small file helpers shared by the sampler and the app

public enum BenchFiles {
    /// open(2) that only ever returns a descriptor for a regular file. O_NONBLOCK makes opening a
    /// FIFO return at once instead of waiting for a writer; fstat on the open descriptor (not the
    /// path, so nothing can be swapped in between) then rejects FIFOs, devices and sockets.
    /// O_NONBLOCK has no effect on reads from a regular file, so it is left set.
    public static func openRegularFile(_ path: String) -> Int32? {
        let fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK)
        guard fd >= 0 else { return nil }
        var st = stat()
        guard fstat(fd, &st) == 0, (st.st_mode & S_IFMT) == S_IFREG else { close(fd); return nil }
        return fd
    }

    /// The first `maxBytes` of a small regular file (pid files and the like); nil for anything else.
    public static func readSmallRegularFile(_ path: String, maxBytes: Int = 4096) -> Data? {
        guard let fd = openRegularFile(path) else { return nil }
        defer { close(fd) }
        var data = Data(count: maxBytes)
        let n = data.withUnsafeMutableBytes { read(fd, $0.baseAddress, maxBytes) }
        guard n >= 0 else { return nil }
        data.count = n
        return data
    }

    /// Read the last `maxBytes` of a regular file and return complete lines (newest last).
    public static func tailLines(_ path: String, maxBytes: Int = 8192) -> [Substring] {
        guard maxBytes > 0, let fd = openRegularFile(path) else { return [] }
        defer { close(fd) }
        var st = stat()
        guard fstat(fd, &st) == 0 else { return [] }
        let end = UInt64(max(0, st.st_size))
        let start = end > UInt64(maxBytes) ? end - UInt64(maxBytes) : 0
        let want = Int(end - start)
        var data = Data(count: want)
        let n = data.withUnsafeMutableBytes { pread(fd, $0.baseAddress, want, off_t(start)) }
        guard n > 0 else { return [] }
        data.count = n
        guard let text = String(data: data, encoding: .utf8) else { return [] }
        var lines = text.split(separator: "\n", omittingEmptySubsequences: true)
        if start > 0, !lines.isEmpty { lines.removeFirst() }   // first line is probably partial
        return lines
    }

    /// Open a file for appending, creating it if needed. Refuses anything that is not a regular
    /// file (a FIFO opened O_NONBLOCK without a reader fails instead of blocking) and a symlink
    /// in the last path component.
    public static func openForAppend(_ path: String) -> Int32? {
        let fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC | O_NONBLOCK | O_NOFOLLOW, 0o644)
        guard fd >= 0 else { return nil }
        var st = stat()
        guard fstat(fd, &st) == 0, (st.st_mode & S_IFMT) == S_IFREG else { close(fd); return nil }
        return fd
    }

    /// Resolve symlinks in a directory path; nil if it does not exist.
    public static func resolvedDirectory(_ path: String) -> String? {
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: path, isDirectory: &isDir), isDir.boolValue else { return nil }
        return URL(fileURLWithPath: path).resolvingSymlinksInPath().path
    }

    public static func mtime(_ path: String) -> Date? {
        var st = stat()
        guard stat(path, &st) == 0 else { return nil }
        return Date(timeIntervalSince1970: Double(st.st_mtimespec.tv_sec) + Double(st.st_mtimespec.tv_nsec) / 1e9)
    }
}

// MARK: - Timestamps

public enum BenchTime {
    private static let plain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime]; return f
    }()
    private static let fractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]; return f
    }()
    public static func string(_ d: Date = Date()) -> String { plain.string(from: d) }
    public static func parse(_ s: String) -> Date? { plain.date(from: s) ?? fractional.date(from: s) }
}

/// Format a Double for JSON with fixed decimals; nil → null.
public func jsonNumber(_ v: Double?, _ decimals: Int = 2) -> String {
    guard let v, v.isFinite else { return "null" }
    return String(format: "%.\(decimals)f", v)
}

/// A JSON string literal, quotes included; nil → null. Escapes `"`, `\` and every control
/// character (RFC 8259 §7), so values read from plists, sysctl or other files cannot break the line.
public func jsonString(_ v: String?) -> String {
    guard let v else { return "null" }
    var out = "\""
    out.reserveCapacity(v.utf8.count + 2)
    for u in v.unicodeScalars {
        switch u {
        case "\"": out += "\\\""
        case "\\": out += "\\\\"
        case "\n": out += "\\n"
        case "\r": out += "\\r"
        case "\t": out += "\\t"
        case "\u{2028}", "\u{2029}": out += String(format: "\\u%04x", u.value)   // JS line separators
        default:
            if u.value < 0x20 || u.value == 0x7F { out += String(format: "\\u%04x", u.value) } else { out.unicodeScalars.append(u) }
        }
    }
    return out + "\""
}

// MARK: - Project folder discovery

/// The project root is the folder that contains `bench/harness.py`. The tools are built into
/// `<project>/sampler/.build/release/` and the app into `<project>/app/BenchMonitor/build/`, so the
/// nearest such ancestor of the running executable is the project it belongs to.
public enum BenchProject {
    public static let marker = "bench/harness.py"

    /// Nearest ancestor of `path` (itself included) that contains the marker file.
    public static func ancestor(of path: String) -> String? {
        var dir = URL(fileURLWithPath: path).standardizedFileURL
        for _ in 0..<64 {
            var st = stat()
            if stat(dir.path + "/" + marker, &st) == 0, (st.st_mode & S_IFMT) == S_IFREG { return dir.path }
            let parent = dir.deletingLastPathComponent()
            if parent.path == dir.path { break }
            dir = parent
        }
        return nil
    }

    /// Discovery without any override: from the executable's real location, then from the
    /// current directory. nil if neither sits inside a project.
    public static func discover() -> String? {
        if let exe = Bundle.main.executableURL?.resolvingSymlinksInPath().deletingLastPathComponent(),
           let p = ancestor(of: exe.path) {
            return p
        }
        return ancestor(of: FileManager.default.currentDirectoryPath)
    }

    /// `explicit` (a command-line flag) wins, then $BENCH_PROJECT, then discovery, then the cwd.
    public static func resolve(explicit: String?) -> String {
        if let p = explicit, !p.isEmpty { return (p as NSString).expandingTildeInPath }
        if let p = ProcessInfo.processInfo.environment["BENCH_PROJECT"], !p.isEmpty {
            return (p as NSString).expandingTildeInPath
        }
        return discover() ?? FileManager.default.currentDirectoryPath
    }
}
