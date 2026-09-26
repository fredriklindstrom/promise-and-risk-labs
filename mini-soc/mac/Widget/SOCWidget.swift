import SwiftUI
import WidgetKit

struct SOCEntry: TimelineEntry {
    let date: Date
    let state: SOCState?
}

struct SOCProvider: TimelineProvider {
    func placeholder(in context: Context) -> SOCEntry { SOCEntry(date: Date(), state: nil) }

    func getSnapshot(in context: Context, completion: @escaping (SOCEntry) -> Void) {
        completion(SOCEntry(date: Date(), state: SOCStore.load()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<SOCEntry>) -> Void) {
        let now = Date()
        let state = SOCStore.load()
        // The companion app reloads on real changes; these entries only let "No data" appear on
        // time if the watcher dies while nothing reloads us.
        var entries = [SOCEntry(date: now, state: state)]
        if let last = state?.lastPollDate {
            let staleAt = last.addingTimeInterval(SOCStore.staleAfter + 1)
            if staleAt > now { entries.append(SOCEntry(date: staleAt, state: state)) }
        }
        completion(Timeline(entries: entries, policy: .after(now.addingTimeInterval(15 * 60))))
    }
}

struct StatusBadge: View {
    let status: String
    var big = false
    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: SOCStyle.symbol(status))
                .font(big ? .title : .title3)
                .foregroundStyle(SOCStyle.color(status))
            Text(SOCStyle.headline(status))
                .font(big ? .title2.weight(.semibold) : .headline)
        }
    }
}

struct CountsRow: View {
    let counts: [String: Int]
    var body: some View {
        HStack(spacing: 8) {
            ForEach(["high", "medium", "low"], id: \.self) { sev in
                HStack(spacing: 3) {
                    Circle().fill(SOCStyle.severityColor(sev)).frame(width: 7, height: 7)
                    Text("\(counts[sev] ?? 0)").font(.caption.monospacedDigit())
                }
            }
        }
    }
}

struct LastPoll: View {
    let state: SOCState
    var body: some View {
        if let d = state.lastPollDate {
            Text("polled \(d, style: .relative) ago").font(.caption2).foregroundStyle(.secondary)
        } else {
            Text("never polled").font(.caption2).foregroundStyle(.secondary)
        }
    }
}

struct AlertRow: View {
    let alert: SOCAlert
    var body: some View {
        HStack(alignment: .top, spacing: 6) {
            Circle().fill(SOCStyle.severityColor(alert.severity)).frame(width: 7, height: 7).padding(.top, 4)
            VStack(alignment: .leading, spacing: 1) {
                Text(alert.title).font(.caption).lineLimit(1)
                // one line: the recommended action, then the model's view (red when escalated)
                HStack(spacing: 4) {
                    if let act = alert.action {
                        // say who recommends it: the rule, or Qwen
                        Text(alert.actionSource == "model" ? "→ Qwen: \(act)" : "→ \(act)")
                            .font(.caption2.weight(.medium)).foregroundStyle(.primary).lineLimit(1)
                    }
                    if alert.escalated == true {
                        Text("· escalated").font(.caption2.weight(.semibold)).foregroundStyle(.red).fixedSize()
                    } else if let a = alert.assessment {
                        Text("· \(a.replacingOccurrences(of: "_", with: " "))")
                            .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
            }
        }
    }
}

struct SOCWidgetView: View {
    @Environment(\.widgetFamily) var family
    let entry: SOCEntry

    var body: some View {
        if let s = entry.state {
            let status = s.effectiveStatus(now: entry.date)
            switch family {
            case .systemSmall: small(s, status)
            case .systemLarge: list(s, status, max: 6)
            default: list(s, status, max: 3)
            }
        } else {
            VStack(spacing: 6) {
                StatusBadge(status: "unknown")
                Text("state.json not found").font(.caption2).foregroundStyle(.secondary)
            }
        }
    }

    func small(_ s: SOCState, _ status: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(s.site).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            StatusBadge(status: status, big: true)
            CountsRow(counts: s.counts)
            Spacer(minLength: 0)
            if s.credentialWarning != nil {
                Label("owner key", systemImage: "key.fill").font(.caption2).foregroundStyle(.orange)
            }
            LastPoll(state: s)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    func list(_ s: SOCState, _ status: String, max: Int) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                StatusBadge(status: status)
                Spacer()
                CountsRow(counts: s.counts)
            }
            let alerts = Array(s.visibleAlerts.prefix(max))
            if alerts.isEmpty {
                Text("No open alerts · \(s.devicesActive) devices online").font(.caption).foregroundStyle(.secondary)
            } else {
                ForEach(alerts) { a in Link(destination: SOCLinks.alert(a.id)) { AlertRow(alert: a) } }
            }
            Spacer(minLength: 0)
            HStack {
                Text(s.site).font(.caption2).foregroundStyle(.secondary)
                if s.credentialWarning != nil {
                    Label("owner key", systemImage: "key.fill").font(.caption2).foregroundStyle(.orange)
                }
                Spacer()
                LastPoll(state: s)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

struct SOCWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "SOCWidget", provider: SOCProvider()) { entry in
            SOCWidgetView(entry: entry)
                .containerBackground(.fill.tertiary, for: .widget)
                .widgetURL(SOCLinks.open)  // tapping anywhere else opens the web UI
        }
        .configurationDisplayName("Qwen Mini SOC")
        .description("Network security status and open alerts.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}

@main
struct SOCWidgetBundle: WidgetBundle {
    var body: some Widget { SOCWidget() }
}
