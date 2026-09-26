import AppKit
import SwiftUI
import WidgetKit

/// Menu-bar companion. Watches state.json and tells the widget to redraw only when something a
/// human would care about changes (macOS throttles widget refreshes too hard for a 2-min poll).
@MainActor
final class Monitor: ObservableObject {
    @Published var state: SOCState?
    @Published var now = Date()
    private var lastSignature = ""
    private var timer: Timer?

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func refresh() {
        now = Date()
        state = SOCStore.load()
        let sig = (state?.signature ?? "none") + "|" + (state?.effectiveStatus(now: now) ?? "none")
        if sig != lastSignature {
            lastSignature = sig
            WidgetCenter.shared.reloadAllTimelines()
        }
    }

    var status: String { state?.effectiveStatus(now: now) ?? "unknown" }
}

/// Receives qwenminisoc:// links from the widget and opens the matching web UI page.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func application(_ application: NSApplication, open urls: [URL]) {
        for url in urls {
            if let web = SOCLinks.webURL(for: url) { NSWorkspace.shared.open(web) }
        }
    }
}

@main
struct QwenMiniSOCApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var monitor = Monitor()

    var body: some Scene {
        MenuBarExtra {
            MenuContent(monitor: monitor)
        } label: {
            Image(systemName: SOCStyle.symbol(monitor.status))
        }
        .menuBarExtraStyle(.menu)
    }
}

struct MenuContent: View {
    @ObservedObject var monitor: Monitor

    var body: some View {
        if let s = monitor.state {
            let st = s.effectiveStatus(now: monitor.now)
            Text("\(s.site): \(SOCStyle.headline(st))")
            Text("Open: \(s.counts["high"] ?? 0) high · \(s.counts["medium"] ?? 0) medium · \(s.counts["low"] ?? 0) low")
            if let d = s.lastPollDate {
                Text("Last poll: \(d.formatted(date: .omitted, time: .shortened))")
            }
            if let w = s.credentialWarning { Text("⚠︎ \(w)") }
            if let e = s.lastError { Text("Last error: \(e)") }
            Divider()
            ForEach(s.visibleAlerts.prefix(10)) { a in
                Button("#\(a.id) [\(a.severity)] \(a.title)" + (a.assessment.map { " — model: \($0)" } ?? "")
                       + (a.action.map { "\n    → \($0) (" + (a.actionSource == "model" ? "Qwen" : "rule") + ")" } ?? "")) {
                    if let web = SOCLinks.webURL(for: SOCLinks.alert(a.id)) { NSWorkspace.shared.open(web) }
                }
            }
            if s.visibleAlerts.isEmpty { Text("No open alerts") }
        } else {
            Text("No state.json yet")
        }
        Divider()
        Button("Open Mini SOC") {
            if let web = SOCLinks.webURL(for: SOCLinks.open) { NSWorkspace.shared.open(web) }
        }
        Button("Refresh widget") {
            monitor.refresh()
            WidgetCenter.shared.reloadAllTimelines()
        }
        Button("Open project folder") {
            NSWorkspace.shared.open(URL(fileURLWithPath: SOCStore.realHome + "/mini-soc"))
        }
        Button("Quit") { NSApplication.shared.terminate(nil) }
    }
}
