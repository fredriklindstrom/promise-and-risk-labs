import Foundation
import SwiftUI

/// Mirror of state/state.json, written by the Python watcher every poll.
struct SOCAlert: Codable, Identifiable, Hashable {
    let id: Int
    let severity: String
    let title: String
    let rule: String
    let created: Double
    let assessment: String?
    let confidence: String?
    let escalated: Bool?
    let actionId: String?
    let action: String?
    let actionSource: String?
}

struct SOCState: Codable, Hashable {
    let site: String
    let generated: Double
    let status: String
    let lastPollOk: Double?
    let lastError: String?
    let credentialWarning: String?
    let counts: [String: Int]
    let devicesActive: Int
    let openAlerts: [SOCAlert]
}

enum SOCStore {
    /// The widget is sandboxed, so NSHomeDirectory() is its container. Use the real home.
    static var realHome: String {
        if let pw = getpwuid(getuid()), let dir = pw.pointee.pw_dir { return String(cString: dir) }
        return NSHomeDirectory()
    }

    static var stateURL: URL {
        URL(fileURLWithPath: realHome).appendingPathComponent("mini-soc/state/state.json")
    }

    static func load() -> SOCState? {
        guard let data = try? Data(contentsOf: stateURL) else { return nil }
        let dec = JSONDecoder()
        dec.keyDecodingStrategy = .convertFromSnakeCase
        return try? dec.decode(SOCState.self, from: data)
    }

    /// The file's own status can't say the watcher has died; its age can.
    static let staleAfter: TimeInterval = 10 * 60
}

extension SOCState {
    var lastPollDate: Date? { lastPollOk.map { Date(timeIntervalSince1970: $0) } }

    func effectiveStatus(now: Date = Date()) -> String {
        guard let last = lastPollDate, now.timeIntervalSince(last) < SOCStore.staleAfter else { return "unknown" }
        return status
    }

    /// Alerts worth showing in the widget: medium and up first, then low.
    var visibleAlerts: [SOCAlert] { openAlerts.filter { $0.severity != "info" } }

    /// Changes only when something a human would care about changes (not every poll).
    var signature: String {
        "\(status)|\(counts.sorted { $0.key < $1.key })|\(openAlerts.map { "\($0.id):\($0.assessment ?? "")" })|\(credentialWarning ?? "")"
    }
}

enum SOCLinks {
    /// The widget can only hand URLs to its app; the app turns these into the local web UI.
    static let open = URL(string: "qwenminisoc://open")!
    static func alert(_ id: Int) -> URL { URL(string: "qwenminisoc://alert/\(id)")! }

    /// qwenminisoc://open -> the web UI; qwenminisoc://alert/<digits> -> that alert. Anything else: nil.
    static func webURL(for url: URL) -> URL? {
        guard url.scheme == "qwenminisoc" else { return nil }
        let base = "http://127.0.0.1:8095/"
        if url.host == "open" { return URL(string: base) }
        if url.host == "alert", let id = Int(url.lastPathComponent), id > 0 { return URL(string: base + "#/alert/\(id)") }
        return nil
    }
}

enum SOCStyle {
    static func color(_ status: String) -> Color {
        switch status {
        case "green": return .green
        case "amber": return .orange
        case "red": return .red
        default: return .gray
        }
    }

    static func headline(_ status: String) -> String {
        switch status {
        case "green": return "All clear"
        case "amber": return "Review"
        case "red": return "Alert"
        default: return "No data"
        }
    }

    static func symbol(_ status: String) -> String {
        switch status {
        case "green": return "checkmark.shield.fill"
        case "amber": return "exclamationmark.shield.fill"
        case "red": return "xmark.shield.fill"
        default: return "questionmark.diamond.fill"
        }
    }

    static func severityColor(_ sev: String) -> Color {
        switch sev {
        case "high": return .red
        case "medium": return .orange
        case "low": return .yellow
        default: return .gray
        }
    }
}
