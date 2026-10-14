import BenchProbe
import Foundation

/// Max Runs (2026-10-22): sleep the display each time a run starts, so the GPU has no screen to draw.
/// macOS stops long GPU jobs that hold the GPU while the display needs it ("Impacting Interactivity"); with the
/// display asleep that pressure is gone. Off by default and remembered; no password (`pmset displaysleepnow` needs none).
/// Every sleep is logged to runs/.display_sleep.log with the run id, so results can say the display was asleep.
enum MaxRuns {
    static let defaultsKey = "maxRunsSleepDisplay"

    static var enabled: Bool {
        get { UserDefaults.standard.bool(forKey: defaultsKey) }
        set { UserDefaults.standard.set(newValue, forKey: defaultsKey) }
    }

    /// Sleeps the display now and logs it against `runId`.
    static func sleepDisplay(runId: String, project: String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/pmset")
        p.arguments = ["displaysleepnow"]
        do { try p.run() } catch { NSLog("Bench Monitor: pmset displaysleepnow failed: %@", error.localizedDescription) }
        // The run id comes from a file: printable, no tabs or newlines, bounded. Written off the main thread through
        // BenchFiles.openForAppend (regular files only, no link followed, never blocks on a FIFO) (review M4).
        let id = String(runId.unicodeScalars.map { $0.properties.isWhitespace || CharacterSet.controlCharacters.contains($0) ? "_" : String($0) }
                        .joined().prefix(200))
        let line = "\(ISO8601DateFormatter().string(from: Date()))\t\(id)\tdisplay asleep (Max Runs)\n"
        let path = project + "/runs/.display_sleep.log"
        DispatchQueue.global(qos: .utility).async {
            guard let fd = BenchFiles.openForAppend(path) else { return }
            let bytes = Array(line.utf8)
            _ = bytes.withUnsafeBufferPointer { write(fd, $0.baseAddress, $0.count) }
            close(fd)
        }
    }
}
