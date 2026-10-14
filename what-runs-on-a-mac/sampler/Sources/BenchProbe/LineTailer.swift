import Darwin
import Foundation

/// Reads only the bytes appended to a file since the last poll. Handles the file being replaced
/// (inode change) or truncated (size shrinks) by starting over.
/// The file descriptor stays open between polls: each tick costs one stat(2) on the path and one
/// fstat(2) — never an open(2) (opens are slow here and can wake endpoint-security scanners).
/// Only regular files are opened: a FIFO, device or socket at the path is ignored (never blocks).
public struct LineTailer {
    public init() {}
    public private(set) var path: String?
    private var fd: Int32 = -1
    private var offset: UInt64 = 0
    private var inode: UInt64 = 0
    private var partial = Data()
    private var skipFirstLine = false
    private var startFromEndBytes: Int?
    public private(set) var lastGrowth: Date?

    /// `startFromEndBytes`: on first open, begin this many bytes before the end (for files where
    /// only recent lines matter, e.g. samples). nil = read the whole file.
    public mutating func reset(path: String?, startFromEndBytes: Int? = nil) {
        closeFD()
        self.path = path
        self.startFromEndBytes = startFromEndBytes
        offset = 0; inode = 0; partial = Data(); skipFirstLine = false; lastGrowth = nil
    }

    private mutating func closeFD() {
        if fd >= 0 { close(fd); fd = -1 }
    }

    public mutating func poll(now: Date) -> [Data] {
        guard let path else { return [] }
        var st = stat()
        guard stat(path, &st) == 0, (st.st_mode & S_IFMT) == S_IFREG else { closeFD(); inode = 0; return [] }
        let ino = UInt64(st.st_ino)
        if fd < 0 || ino != inode {
            let firstOpen = inode == 0
            closeFD()
            // Re-checked on the descriptor itself: the path may have been swapped since stat().
            guard let f = BenchFiles.openRegularFile(path) else { return [] }
            var fst = stat()
            guard fstat(f, &fst) == 0, UInt64(fst.st_ino) == ino else { close(f); return [] }
            fd = f
            inode = ino
            partial = Data(); skipFirstLine = false; offset = 0
            if firstOpen {
                if let back = startFromEndBytes, UInt64(st.st_size) > UInt64(back) {
                    offset = UInt64(st.st_size) - UInt64(back); skipFirstLine = true
                }
                lastGrowth = Date(timeIntervalSince1970: Double(st.st_mtimespec.tv_sec))
            }
        }
        let size = UInt64(st.st_size)
        if size < offset { offset = 0; partial = Data(); skipFirstLine = false }   // truncated
        guard size > offset else { return [] }
        let want = Int(min(size - offset, 8 * 1024 * 1024))
        var data = Data(count: want)
        let n = data.withUnsafeMutableBytes { pread(fd, $0.baseAddress, want, off_t(offset)) }
        guard n > 0 else { return [] }
        data.count = n
        offset += UInt64(n)
        lastGrowth = now
        partial.append(data)
        var lines: [Data] = []
        while let nl = partial.firstIndex(of: 0x0A) {
            let line = partial[partial.startIndex..<nl]
            partial.removeSubrange(partial.startIndex...nl)
            if skipFirstLine { skipFirstLine = false; continue }
            if !line.isEmpty { lines.append(Data(line)) }
        }
        if partial.count > 1_000_000 { partial = Data() }   // runaway line without newline: drop it
        return lines
    }
}

