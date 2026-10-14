import SwiftUI

/// 4. Results so far — every completed run (newest first) on the left, running averages per
/// model+runner+quant+context on the right, overall summary on top. No ScrollView: the screen is
/// filmed, so it shows as many of the newest rows as fit.
struct ResultsView: View {
    @Environment(MonitorModel.self) private var m

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SummaryLine(summary: m.summary)
            HStack(alignment: .top, spacing: 18) {
                Panel(padding: 16) { RunsTable(rows: m.results, fastest: m.fastest) }
                    .frame(maxWidth: .infinity)
                Panel(padding: 16) { AveragesTable(groups: m.groups) }
                    .frame(width: 720)
            }
        }
    }
}

struct SummaryLine: View {
    var summary: ResultsSummary
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 22) {
            SectionLabel("Results so far")
            if summary.total == 0 && summary.errors == 0 {
                Text("No completed runs yet").font(Theme.font(15)).foregroundStyle(Theme.dim)
            } else {
                stat("\(summary.total)", "runs")
                stat("\(summary.fits)", "fit", Theme.cyan)
                stat("\(summary.fails)", "did not fit", summary.fails > 0 ? Theme.amber : Theme.text)
                if summary.errors > 0 { stat("\(summary.errors)", "errors (retried)", Theme.dim) }
                if let d = summary.meanDecode { stat(Fmt.speed(d), "tok/s mean decode") }
                if let p = summary.meanPeak { stat(Fmt.num(p, 1), "GB mean peak", Theme.amber) }
                if let b = summary.best {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text("Fastest").font(Theme.font(14)).foregroundStyle(Theme.dim)
                        Text("\(Fmt.speed(b.decodeTPS)) tok/s").font(Theme.numerals(17)).foregroundStyle(Theme.cyan)
                        Text([b.model, b.quant, Fmt.context(b.contextTokens)].compactMap { $0 }.joined(separator: " · "))
                            .font(Theme.font(14)).foregroundStyle(Theme.text.opacity(0.85))
                    }
                    .lineLimit(1)
                }
            }
            Spacer(minLength: 0)
        }
    }

    private func stat(_ v: String, _ label: String, _ color: Color = Theme.text) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(v).font(Theme.numerals(17)).foregroundStyle(color)
            Text(label).font(Theme.font(14)).foregroundStyle(Theme.dim)
        }
    }
}

private let rowHeight: CGFloat = 24   // compact so the whole results list fits one window

/// Fixed-column table built from HStacks (SwiftUI Table is AppKit-backed and too small to film).
struct RunsTable: View {
    var rows: [ResultRow]
    var fastest: Set<String>

    private let cols: [(String, CGFloat, Alignment)] = [
        ("Model", 0, .leading), ("Runner", 100, .leading), ("Quant", 150, .leading), ("Context", 70, .trailing),
        ("Decode tok/s", 110, .trailing), ("Peak mem", 90, .trailing), ("Peak W", 80, .trailing), ("Fit", 170, .leading),
    ]

    var body: some View {
        GeometryReader { geo in
            let fit = max(0, Int((geo.size.height - rowHeight - 22) / rowHeight))
            VStack(alignment: .leading, spacing: 0) {
                header
                if rows.isEmpty {
                    Text("Completed runs appear here, newest first.")
                        .font(Theme.font(14)).foregroundStyle(Theme.faint).padding(.top, 10)
                }
                ForEach(rows.prefix(fit)) { r in row(r) }
                if rows.count > fit, fit > 0 {
                    Text("+ \(rows.count - fit) earlier")
                        .font(Theme.font(13)).foregroundStyle(Theme.faint)
                }
            }
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            ForEach(cols, id: \.0) { c in
                cell(Text(c.0).font(Theme.font(13, .semibold)).foregroundColor(Theme.dim), c.1, c.2)
            }
        }
        .frame(height: rowHeight)
        .overlay(alignment: .bottom) { Rectangle().fill(Theme.panelEdge).frame(height: 1) }
    }

    private func row(_ r: ResultRow) -> some View {
        let isFastest = fastest.contains(r.id)
        return HStack(spacing: 12) {
            cell(HStack(spacing: 8) {
                Text(r.model).font(Theme.font(15, .medium)).foregroundColor(Theme.text).lineLimit(1)
                if r.warmup || r.dryRun {
                    Text(r.warmup ? "warm-up" : "dry run").font(Theme.font(12, .medium)).foregroundColor(Theme.faint)
                }
                if isFastest {
                    Text("▲ fastest").font(Theme.font(12, .semibold)).foregroundColor(Theme.cyan)
                }
            }, 0, .leading)
            cell(Text(r.runner ?? "—").font(Theme.font(14)).foregroundColor(Theme.text.opacity(0.85)).lineLimit(1).truncationMode(.tail), 100, .leading)
            cell(Text(r.quant ?? "—").font(Theme.font(14)).foregroundColor(Theme.text.opacity(0.85)).lineLimit(1).truncationMode(.tail), 150, .leading)
            cell(Text(r.isMedia ? "—" : (Fmt.context(r.contextTokens) ?? "—")).font(Theme.numerals(14, .regular)).foregroundColor(Theme.text), 70, .trailing)
            cell(Text(speedText(r)).font(Theme.numerals(16, .semibold)).foregroundColor(r.failed ? Theme.faint : Theme.cyan), 110, .trailing)
            cell(Text(r.peakMemGB.map { "\(Fmt.num($0, 1)) GB" } ?? "—").font(Theme.numerals(14, .regular)).foregroundColor(Theme.amber), 90, .trailing)
            cell(Text(r.peakPkgPowerW.map { "\(Fmt.num($0, 0)) W" } ?? "—").font(Theme.numerals(14, .regular)).foregroundColor(Theme.text), 80, .trailing)
            cell(fitLabel(r), 170, .leading)
        }
        .frame(height: rowHeight)
        .background(isFastest ? Theme.cyan.opacity(0.07) : .clear)
    }

    private func speedText(_ r: ResultRow) -> String {
        if r.failed { return "—" }
        if r.isMedia { return r.sPerStep.map { "\(Fmt.num($0, 1)) s/step" } ?? "—" }
        return Fmt.speed(r.decodeTPS)
    }

    private func fitLabel(_ r: ResultRow) -> some View {
        let fit = (r.fit ?? "").lowercased()
        let (text, color): (String, Color) = {
            switch r.failKind {
            case .didNotFit: return ("✕ " + r.shortReason, Theme.amber)
            case .error: return ("error · " + r.shortReason, Theme.faint)
            case .skipped: return ("skipped", Theme.faint)
            case .none: break
            }
            switch fit {
            case "yes", "true", "fit": return ("✓ fits", Theme.cyan)
            case "no", "false": return ("✕ no", Theme.amber)
            case "": return ("—", Theme.dim)
            default: return (fit, Theme.amber)
            }
        }()
        return Text(text).font(Theme.font(14, .medium)).foregroundColor(color).lineLimit(1)
    }

    @ViewBuilder
    private func cell<V: View>(_ v: V, _ width: CGFloat, _ align: Alignment) -> some View {
        if width == 0 {
            v.frame(maxWidth: .infinity, alignment: align)
        } else {
            v.frame(width: width, alignment: align)
        }
    }
}

struct AveragesTable: View {
    var groups: [GroupAverage]

    var body: some View {
        GeometryReader { geo in
            let fit = max(0, Int((geo.size.height - rowHeight * 2 - 22) / rowHeight))
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    Text("Running average per config").font(Theme.font(13, .semibold)).foregroundColor(Theme.dim)
                    Spacer()
                }
                .frame(height: rowHeight)
                HStack(spacing: 10) {
                    h("Config", 0, .leading); h("Runs", 54, .trailing); h("Decode", 90, .trailing)
                    h("Prefill", 90, .trailing); h("TTFT", 80, .trailing); h("Peak", 96, .trailing)
                }
                .frame(height: rowHeight)
                .overlay(alignment: .bottom) { Rectangle().fill(Theme.panelEdge).frame(height: 1) }
                if groups.isEmpty {
                    Text("Averages build up as reps complete.").font(Theme.font(14)).foregroundStyle(Theme.faint).padding(.top, 10)
                }
                ForEach(groups.prefix(fit)) { g in
                    HStack(spacing: 10) {
                        (Text(g.model).font(Theme.font(14, .medium)).foregroundColor(Theme.text)
                         + Text("  " + [g.runner, g.quant, g.kind == "media" ? nil : Fmt.context(g.contextTokens), g.needle ? "needle" : nil].compactMap { $0 }.joined(separator: " · "))
                            .font(Theme.font(13)).foregroundColor(Theme.dim))
                            .lineLimit(1).truncationMode(.tail)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Text(g.fails > 0 ? "\(g.runs)+\(g.fails)✕" : "\(g.runs)")
                            .font(Theme.numerals(14, .regular)).foregroundColor(g.fails > 0 ? Theme.amber : Theme.text)
                            .frame(width: 54, alignment: .trailing)
                        Text(g.kind == "media" ? (g.sPerStep.map { "\(Fmt.num($0, 1)) s/step" } ?? "—") : Fmt.speed(g.decodeTPS))
                            .font(Theme.numerals(14, .semibold)).foregroundColor(Theme.cyan).lineLimit(1).minimumScaleFactor(0.8).frame(width: 90, alignment: .trailing)
                        Text(Fmt.speed(g.prefillTPS)).font(Theme.numerals(14, .regular)).foregroundColor(Theme.text).frame(width: 90, alignment: .trailing)
                        Text(g.ttftS.map { "\(Fmt.num($0, 2))s" } ?? "—").font(Theme.numerals(14, .regular)).foregroundColor(Theme.text).frame(width: 80, alignment: .trailing)
                        Text(g.peakMemGB.map { "\(Fmt.num($0, 1)) GB" } ?? "—").font(Theme.numerals(14, .regular)).foregroundColor(Theme.amber).frame(width: 96, alignment: .trailing)
                    }
                    .frame(height: rowHeight)
                }
                if groups.count > fit, fit > 0 {
                    Text("+ \(groups.count - fit) more configs").font(Theme.font(13)).foregroundStyle(Theme.faint)
                }
            }
        }
    }

    private func h(_ t: String, _ w: CGFloat, _ a: Alignment) -> some View {
        Group {
            if w == 0 {
                Text(t).font(Theme.font(13, .semibold)).foregroundColor(Theme.dim).frame(maxWidth: .infinity, alignment: a)
            } else {
                Text(t).font(Theme.font(13, .semibold)).foregroundColor(Theme.dim).frame(width: w, alignment: a)
            }
        }
    }
}
