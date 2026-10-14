// PowerLog — the power line format and where it goes, shared by bench-power and Bench Monitor.
//
// Line: {"t":ISO8601,"type":"power","pkg_power_w":..,"cpu_power_w":..,"gpu_power_w":..,"ane_power_w":..,
//        "dram_power_w":..,"thermal_pressure":..,"gpu_freq_mhz":..,"gpu_active_pct":..,"cpu_p_freq_mhz":..,
//        "cpu_e_freq_mhz":..,"source":"powermetrics"|"ioreport","interval_s":..,"writer_pid":N}
// appended to <project>/runs/current/power.jsonl (symlink re-resolved per line; skipped when absent or when it
// resolves outside runs/) and to <project>/runs/power_all.jsonl. bench-sampler copies the newest line (if < 3 s
// old) into samples.jsonl. IOReport has no thermal-pressure level and its CPU clock tables are not decoded here:
// those fields are null for "ioreport" lines (macOS thermal state is in every sample anyway). An "ioreport" line's
// t is the END of its interval; powermetrics stamps the start (measured 2026-10-24: same watts, one second apart).
//
// power_all.jsonl is rotated to power_all.<UTC date-time>.jsonl once it passes 32 MB, so an always-on sampler
// (about 20 MB a day) never makes the harness read an ever-growing file.
import Darwin
import Foundation

public enum PowerLog {
    public static let rotateBytes: Int64 = 32 * 1024 * 1024

    public static func line(_ r: PowerReading) -> String {
        "{\"t\":\(jsonString(BenchTime.string(r.t))),\"type\":\"power\",\"pkg_power_w\":\(jsonNumber(r.pkgW)),"
            + "\"cpu_power_w\":\(jsonNumber(r.cpuW)),\"gpu_power_w\":\(jsonNumber(r.gpuW)),\"ane_power_w\":\(jsonNumber(r.aneW)),"
            + "\"dram_power_w\":\(jsonNumber(r.dramW)),\"thermal_pressure\":null,\"gpu_freq_mhz\":\(jsonNumber(r.gpuFreqMHz, 0)),"
            + "\"gpu_active_pct\":\(jsonNumber(r.gpuActivePct, 1)),\"cpu_p_freq_mhz\":null,\"cpu_e_freq_mhz\":null,"
            + "\"source\":\"ioreport\",\"interval_s\":\(jsonNumber(r.seconds, 3)),\"writer_pid\":\(getpid())}\n"
    }

    /// Appends one line to runs/power_all.jsonl and, when a run is in progress, runs/current/power.jsonl.
    public static func emit(_ line: String, project: String) {
        guard let runs = BenchFiles.resolvedDirectory(project + "/runs") else { return }
        let all = runs + "/power_all.jsonl"
        rotateIfLarge(all, runs: runs)
        append(line, to: all)
        // runs/current is a link to the active run folder: only follow it to a folder inside runs/.
        if let cur = BenchFiles.resolvedDirectory(runs + "/current"), cur.hasPrefix(runs + "/") {
            append(line, to: cur + "/power.jsonl")
        }
    }

    /// True when another process wrote power_all.jsonl's newest line within `seconds` (a powermetrics sampler, or a
    /// bench-power started by hand). Lines without a writer_pid (powermetrics, older builds) count as another writer.
    public static func otherWriterActive(project: String, seconds: TimeInterval = 3) -> Bool {
        guard let runs = BenchFiles.resolvedDirectory(project + "/runs") else { return false }
        let path = runs + "/power_all.jsonl"
        guard let m = BenchFiles.mtime(path), Date().timeIntervalSince(m) <= seconds,
              let last = lastLine(path) else { return false }
        guard let r = last.range(of: #""writer_pid":(\d+)"#, options: .regularExpression),
              let pid = Int32(last[r].split(separator: ":").last ?? "") else { return true }
        return pid != getpid()
    }

    private static func append(_ line: String, to path: String) {
        guard let fd = BenchFiles.openForAppend(path) else { return }   // regular files only, never a FIFO
        let bytes = Array(line.utf8)
        _ = bytes.withUnsafeBufferPointer { write(fd, $0.baseAddress, $0.count) }
        close(fd)
    }

    private static func rotateIfLarge(_ path: String, runs: String) {
        var st = stat()
        guard lstat(path, &st) == 0, (st.st_mode & S_IFMT) == S_IFREG, Int64(st.st_size) > rotateBytes else { return }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "UTC"); f.dateFormat = "yyyyMMdd-HHmmss"
        let dest = runs + "/power_all." + f.string(from: Date()) + ".jsonl"
        // renamex_np with RENAME_EXCL: never replaces an existing file.
        _ = renamex_np(path, dest, UInt32(RENAME_EXCL))
    }

    /// The last line of a regular file (reads at most its final 4 KB, never follows a link).
    private static func lastLine(_ path: String) -> String? {
        let fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard fd >= 0 else { return nil }
        defer { close(fd) }
        var st = stat()
        guard fstat(fd, &st) == 0, (st.st_mode & S_IFMT) == S_IFREG, st.st_size > 0 else { return nil }
        let n = min(Int(st.st_size), 4096)
        var buf = [UInt8](repeating: 0, count: n)
        guard pread(fd, &buf, n, off_t(st.st_size) - off_t(n)) == n else { return nil }
        let text = String(decoding: buf, as: UTF8.self)
        return text.split(separator: "\n").last.map(String.init)
    }
}
