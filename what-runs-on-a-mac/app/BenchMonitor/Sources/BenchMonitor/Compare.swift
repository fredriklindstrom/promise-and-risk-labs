import SwiftUI

// MARK: - Compare mode: up to five models side by side
//
// A contender is one model + runner + quant (the unit the harness measures), with its running
// averages per context taken from the same totals as the results table. Context rows line up
// across the columns so the eye can compare horizontally; the best decode in each row is marked.

struct Contender: Identifiable, Equatable {
    var id: String { label }
    var model: String
    var runner: String?
    var quant: String?
    var arch: String?
    var activeParams: String?
    var cells: [Int: GroupAverage]       // context tokens -> averages for that context
    var lastT: Date

    var label: String { [model, runner, quant].compactMap { $0 }.joined(separator: " · ") }

    /// Time to first token expected at `ctx`: this cell's average when it has one, else (estimated)
    /// the prompt length over a prefill rate extrapolated from the two largest smaller contexts this
    /// contender ran, using their per-doubling slowdown (prefill slows as attention grows).
    func expectedTTFT(_ ctx: Int) -> (seconds: Double, estimated: Bool)? {
        if let g = cells[ctx], g.runs > 0, let t = g.ttftS { return (t, false) }
        let known = cells.filter { $0.key < ctx && $0.value.runs > 0 && ($0.value.prefillTPS ?? 0) > 0 }.sorted { $0.key < $1.key }
        guard let last = known.last, let r2 = last.value.prefillTPS else { return nil }
        var rate = r2
        if known.count >= 2, let r1 = known[known.count - 2].value.prefillTPS, r1 > 0 {
            let c1 = Double(known[known.count - 2].key), c2 = Double(last.key)
            let perDoubling = log(r2 / r1) / log2(c2 / c1)
            rate = r2 * pow(2, perDoubling * log2(Double(ctx) / c2))
        }
        let prompt = Double(ctx - 576)          // the harness trims prompts to N - 512 output - 64 slack
        return rate > 0 ? (prompt / rate, true) : nil
    }
    var archLabel: String? { RunInfo.archLabel(arch, activeParams) }
    var runs: Int { cells.values.reduce(0) { $0 + $1.runs } }
    /// Largest context with at least one completed run.
    var maxFitContext: Int? { cells.filter { $0.value.runs > 0 }.keys.max() }
    /// Smallest context that only ever failed (did not fit).
    var firstFailContext: Int? { cells.filter { $0.value.runs == 0 && $0.value.fails > 0 }.keys.min() }

    /// LLM groups only (media has no context axis), newest contender first. Speed runs give the numbers
    /// (needle runs answer in 64 tokens); a context where only needle runs exist and all of them failed
    /// still shows as "did not fit", because not fitting is a property of the context, not the workload.
    static func build(_ groups: [GroupAverage]) -> [Contender] {
        var byKey: [String: Contender] = [:]
        let speed = groups.filter { !$0.needle }
        let speedKeys = Set(speed.map { [$0.model, $0.runner ?? "", $0.quant ?? "", $0.contextTokens.map(String.init) ?? ""].joined(separator: "|") })
        let needleOnlyFails = groups.filter { g in
            g.needle && g.runs == 0 && g.fails > 0
                && !speedKeys.contains([g.model, g.runner ?? "", g.quant ?? "", g.contextTokens.map(String.init) ?? ""].joined(separator: "|"))
        }
        for g in speed + needleOnlyFails where g.kind == "llm" {
            guard let ctx = g.contextTokens else { continue }
            let key = [g.model, g.runner ?? "", g.quant ?? ""].joined(separator: "|")
            var c = byKey[key] ?? Contender(model: g.model, runner: g.runner, quant: g.quant, arch: g.arch,
                                            activeParams: g.activeParams, cells: [:], lastT: g.lastT)
            c.cells[ctx] = g
            c.lastT = max(c.lastT, g.lastT)
            if c.arch == nil { c.arch = g.arch }
            if c.activeParams == nil { c.activeParams = g.activeParams }
            byKey[key] = c
        }
        return byKey.values.sorted { $0.lastT > $1.lastT }
    }
}

/// The run in progress, when it belongs to a contender on screen: its cell shows live numbers.
struct LiveCell: Equatable {
    var contenderID: String       // Contender.label of the running model + runner + quant
    var ctx: Int
    var phase: String?            // loading | prefill | decode | done
    var decodeTPS: Double?        // from the harness's progress events (decode only)
    var tokens: Int?
    var watts: Double?            // package power from the newest sample (power sampler running)
    var elapsed: Double?
    var stale: Bool
    var warmup: Bool
    var maxTokens: Int            // 512 for the speed workload, 64 for needle runs
    var expectedTTFT: Double? = nil   // filled per contender (Contender.expectedTTFT)
    var ttftEstimated = false

    /// Progress of the current phase, 0...1: decode = tokens / max tokens (exact); prefill = elapsed /
    /// expected time to first token (this cell's average, else an extrapolation, capped at 99 %).
    var progress: Double? {
        if phase == "decode", let t = tokens { return min(1, Double(t) / Double(max(1, maxTokens))) }
        if phase == "prefill" || phase == "loading", let e = elapsed, let x = expectedTTFT, x > 0 {
            let p = e / x
            return p.isFinite ? min(0.99, max(0, p)) : nil     // crafted times can't make Int() trap (review M3)
        }
        return nil
    }
}

enum CompareSelection {
    static let maxModels = 5            // five fit side by side at the default window size
    static let defaultsKey = "compareModels"

    /// `spec`: entries separated by ";" or newlines. Each entry is a contender label
    /// ("Model · Runner · Quant", case-insensitive), or else a case-insensitive substring of one
    /// (the newest match not already picked wins). Empty spec = the most recently run, up to maxModels.
    static func resolve(_ spec: String, _ all: [Contender]) -> [Contender] {
        let entries = entries(spec)
        guard !entries.isEmpty else { return Array(all.prefix(maxModels)) }
        var picked: [Contender] = []
        for e in entries where picked.count < maxModels {
            let low = e.lowercased()
            let free = all.filter { c in !picked.contains { $0.id == c.id } }
            if let exact = free.first(where: { $0.label.lowercased() == low }) {
                picked.append(exact)
            } else if let partial = free.first(where: { $0.label.lowercased().contains(low) }) {
                picked.append(partial)
            }
        }
        return picked
    }

    static func entries(_ spec: String) -> [String] {
        spec.split(whereSeparator: { $0 == ";" || $0 == "\n" })
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }

    /// Spec after toggling one contender in the current selection (no-op when adding a fifth).
    static func toggled(_ c: Contender, current: [Contender]) -> String {
        var labels = current.map(\.label)
        if let i = labels.firstIndex(of: c.label) {
            labels.remove(at: i)
        } else if labels.count < maxModels {
            labels.append(c.label)
        }
        return labels.joined(separator: "; ")
    }
}

// MARK: - View

struct CompareView: View {
    @Environment(MonitorModel.self) private var m

    var body: some View {
        let picked = m.comparedContenders
        let live = m.liveCompareCell.flatMap { l in picked.contains { $0.id == l.contenderID } ? l : nil }
        let contexts = Array(Set(picked.flatMap { $0.cells.keys } + (live.map { [$0.ctx] } ?? []))).sorted()
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 18) {
                SectionLabel("Compare · \(picked.count) of up to \(CompareSelection.maxModels)", color: Theme.cyan)
                Text("Same workload on this Mac · mean of the timed reps · ▲ fastest decode in each row")
                    .font(Theme.font(15)).foregroundStyle(Theme.dim)
                Spacer(minLength: 0)
                if m.compareSpec.isEmpty && !picked.isEmpty {
                    Text("auto: the \(picked.count) most recently run").font(Theme.font(14)).foregroundStyle(Theme.faint)
                }
            }
            if picked.isEmpty {
                Panel {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Nothing to compare yet").font(Theme.font(28, .semibold)).foregroundStyle(Theme.text)
                        Text("Completed language-model runs appear here. Pick up to \(CompareSelection.maxModels) in the toolbar (Models).")
                            .font(Theme.font(18)).foregroundStyle(Theme.dim)
                    }
                }
            } else {
                let best = bestDecode(picked, contexts)
                HStack(alignment: .top, spacing: 16) {
                    ForEach(picked) { c in
                        ContenderColumn(c: c, contexts: contexts, best: best, maxDecode: maxDecode(picked, contexts),
                                        live: live?.contenderID == c.id ? live.map { l in
                                            var l = l
                                            if let x = c.expectedTTFT(l.ctx) { l.expectedTTFT = x.seconds; l.ttftEstimated = x.estimated }
                                            return l
                                        } : nil)
                    }
                    // keep column widths stable when fewer than the maximum are picked
                    ForEach(0..<max(0, CompareSelection.maxModels - picked.count), id: \.self) { _ in
                        Color.clear.frame(maxWidth: .infinity)
                    }
                }
            }
        }
    }

    /// Context -> id of the contender with the fastest mean decode there (only when two or more ran it).
    private func bestDecode(_ cs: [Contender], _ contexts: [Int]) -> [Int: String] {
        var out: [Int: String] = [:]
        for ctx in contexts {
            let ran = cs.compactMap { c in c.cells[ctx]?.decodeTPS.map { (c.id, $0) } }
            if ran.count >= 2, let top = ran.max(by: { $0.1 < $1.1 }) { out[ctx] = top.0 }
        }
        return out
    }

    /// Context -> highest mean decode among the picked contenders (bar scale per row).
    private func maxDecode(_ cs: [Contender], _ contexts: [Int]) -> [Int: Double] {
        var out: [Int: Double] = [:]
        for ctx in contexts {
            out[ctx] = cs.compactMap { $0.cells[ctx]?.decodeTPS }.max()
        }
        return out
    }
}

private struct ContenderColumn: View {
    var c: Contender
    var contexts: [Int]
    var best: [Int: String]
    var maxDecode: [Int: Double]
    var live: LiveCell?

    var body: some View {
        Panel(padding: 18) {
            VStack(alignment: .leading, spacing: 10) {
                header
                Rectangle().fill(Theme.panelEdge).frame(height: 1)
                GeometryReader { geo in
                    let rowH = contexts.isEmpty ? 0 : (geo.size.height - CGFloat(contexts.count - 1) * 8) / CGFloat(contexts.count)
                    VStack(spacing: 8) {
                        ForEach(contexts, id: \.self) { ctx in
                            ContextCell(ctx: ctx, g: c.cells[ctx], isBest: best[ctx] == c.id,
                                        maxDecode: maxDecode[ctx], height: rowH, width: geo.size.width,
                                        live: live?.ctx == ctx ? live : nil)
                                .frame(height: max(0, rowH))
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(c.model)
                .font(Theme.font(30, .semibold)).foregroundStyle(Theme.text)
                .lineLimit(1).minimumScaleFactor(0.6)
            Text([c.runner, c.quant].compactMap { $0 }.joined(separator: " · "))
                .font(Theme.font(17, .medium)).foregroundStyle(Theme.text.opacity(0.8))
                .lineLimit(1).minimumScaleFactor(0.8)
            // Full line when it fits; narrower columns (five on screen) drop the run count, then shorten.
            ViewThatFits(in: .horizontal) {
                statLine(fitsPrefix: "fits up to ", showRuns: true)
                statLine(fitsPrefix: "fits up to ", showRuns: false)
                statLine(fitsPrefix: "≤ ", showRuns: false)
            }
        }
    }
}

private extension ContenderColumn {
    func statLine(fitsPrefix: String, showRuns: Bool) -> some View {
        HStack(spacing: 14) {
            if let a = c.archLabel {
                Text(a).font(Theme.font(16, .medium)).foregroundStyle(Theme.dim)
            }
            if let fit = c.maxFitContext.flatMap(Fmt.context) {
                Text(fitsPrefix + fit).font(Theme.font(16, .semibold)).foregroundStyle(Theme.cyan)
            }
            if let fail = c.firstFailContext.flatMap(Fmt.context) {
                Text("✕ \(fail)").font(Theme.font(16, .semibold)).foregroundStyle(Theme.amber)
            }
            Spacer(minLength: 0)
            if showRuns {
                Text("\(c.runs) run\(c.runs == 1 ? "" : "s")").font(Theme.numerals(15, .regular)).foregroundStyle(Theme.faint)
            }
        }
        .lineLimit(1)
    }
}

private struct ContextCell: View {
    var ctx: Int
    var g: GroupAverage?
    var isBest: Bool
    var maxDecode: Double?
    var height: CGFloat
    var width: CGFloat = 420
    var live: LiveCell? = nil

    var body: some View {
        if let live { liveBody(live) } else { resultBody }
    }

    /// The running cell: live tok/s (or prefill time) and live watts, with this cell's averages underneath.
    private func liveBody(_ l: LiveCell) -> some View {
        let big = min(92, max(22, height * 0.3), max(22, width * 0.16))
        let tall = height > 220
        let color = l.stale ? Theme.amber : Theme.cyan
        let decoding = l.phase == "decode" && l.decodeTPS != nil
        return VStack(alignment: .leading, spacing: 4) {
            // Narrow columns: drop "warm-up", then the phase, before anything gets clipped.
            ViewThatFits(in: .horizontal) {
                liveHeader(l, color: color, tall: tall, phase: true, warmup: l.warmup)
                liveHeader(l, color: color, tall: tall, phase: true, warmup: false)
                liveHeader(l, color: color, tall: tall, phase: false, warmup: false)
            }
            Spacer(minLength: 0)
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if decoding {
                    Text(Fmt.speed(l.decodeTPS)).font(Theme.numerals(big, .semibold)).foregroundStyle(Theme.cyan)
                        .lineLimit(1).minimumScaleFactor(0.5)
                    Text("tok/s").font(Theme.font(max(15, big * 0.26), .medium)).foregroundStyle(Theme.dim)
                } else {
                    Text(Fmt.duration(l.elapsed)).font(Theme.numerals(big * 0.8, .semibold)).foregroundStyle(Theme.text)
                        .lineLimit(1).minimumScaleFactor(0.5)
                    Text(l.phase == "loading" ? "loading" : "prefill").font(Theme.font(max(15, big * 0.24), .medium)).foregroundStyle(Theme.dim)
                }
                Spacer(minLength: 6)
                Text(l.watts.map { Fmt.num($0, 0) } ?? "—").font(Theme.numerals(big * 0.6, .semibold))
                    .foregroundStyle(Theme.text).lineLimit(1).minimumScaleFactor(0.5)
                Text("W").font(Theme.font(max(15, big * 0.24), .medium)).foregroundStyle(Theme.dim)
            }
            meter(l, tall: tall).padding(.vertical, tall ? 4 : 2)
            Text(liveLine1(l)).font(Theme.numerals(tall ? 19 : 15, .regular)).foregroundStyle(Theme.text.opacity(0.8))
                .lineLimit(1).minimumScaleFactor(0.7)
            if tall {
                Text(liveLine2(l)).font(Theme.numerals(19, .regular)).foregroundStyle(Theme.dim)
                    .lineLimit(1).minimumScaleFactor(0.7)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 10).fill(color.opacity(0.10)))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(color.opacity(0.85), lineWidth: 2))
    }

    private func liveHeader(_ l: LiveCell, color: Color, tall: Bool, phase: Bool, warmup: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(Fmt.context(ctx) ?? "—").font(Theme.font(tall ? 24 : 19, .semibold)).foregroundStyle(Theme.dim)
            HStack(spacing: 6) {
                Circle().fill(color).frame(width: 10, height: 10)
                Text(l.stale ? "LIVE · NO DATA" : (phase ? "LIVE · \((l.phase ?? "running").uppercased())" : "LIVE"))
                    .font(Theme.font(tall ? 17 : 14, .semibold)).tracking(1.1).foregroundStyle(color)
            }
            .padding(.horizontal, 9).padding(.vertical, 3)
            .background(Capsule().fill(color.opacity(0.14)))
            Spacer(minLength: 0)
            if warmup { Text("warm-up").font(Theme.font(tall ? 16 : 14, .medium)).foregroundStyle(Theme.dim) }
        }
        .lineLimit(1)
    }

    /// Progress meter for the live cell: thick bar plus the percentage and what it is measured against.
    private func meter(_ l: LiveCell, tall: Bool) -> some View {
        let p = l.progress
        let label: String = {
            guard let p else { return l.phase == "prefill" ? "prefill · no estimate yet" : (l.phase ?? "") }
            let pct = "\(Int((p * 100).rounded(.down)))%"
            if l.phase == "decode" { return "\(pct) · \(l.tokens ?? 0) / \(l.maxTokens) tokens" }
            let of = l.expectedTTFT.map { Fmt.duration($0) } ?? "—"
            return l.ttftEstimated ? "\(pct) of prefill · est. ~\(of)" : "\(pct) of prefill · avg \(of)"
        }()
        return VStack(alignment: .leading, spacing: 4) {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.white.opacity(0.08))
                    Capsule().fill(Theme.cyan.opacity(l.ttftEstimated && l.phase != "decode" ? 0.6 : 0.95))
                        .frame(width: geo.size.width * CGFloat(p ?? 0))
                }
            }
            .frame(height: tall ? 12 : 8)
            Text(label).font(Theme.numerals(tall ? 19 : 15, .semibold)).foregroundStyle(Theme.cyan)
                .lineLimit(1).minimumScaleFactor(0.7)
        }
    }

    /// Averages for this cell (completed timed reps), or a note that this is the first run here.
    private func liveLine1(_ l: LiveCell) -> String {
        guard let g, g.runs > 0 else { return "first run at this context · no average yet" }
        var parts = ["avg \(Fmt.speed(g.decodeTPS)) tok/s"]
        if let w = g.peakPkgPowerW { parts.append("\(Fmt.num(w, 0)) W peak") }
        if let p = g.peakMemGB { parts.append("\(Fmt.num(p, 1)) GB") }
        return parts.joined(separator: " · ")
    }

    private func liveLine2(_ l: LiveCell) -> String {
        if let g, g.runs > 0, let ttft = g.ttftS {
            return "avg TTFT \(ttft >= 60 ? Fmt.duration(ttft) : Fmt.num(ttft, 1) + " s") · prefill \(Fmt.speed(g.prefillTPS)) tok/s"
        }
        return "prefill sends no numbers until the first token"
    }

    private var resultBody: some View {
        // Type scales with the row so 2 contexts and 6 contexts both fill the column; never below 14 pt.
        // Width matters too: five columns on a 1920 px screen leave ~330 px per cell.
        let big = min(92, max(22, height * 0.3), max(22, width * 0.16))
        let tall = height > 220
        return VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(Fmt.context(ctx) ?? "—").font(Theme.font(tall ? 24 : 19, .semibold)).foregroundStyle(Theme.dim)
                if isBest {
                    Text("▲ fastest").font(Theme.font(tall ? 17 : 14, .semibold)).foregroundStyle(Theme.cyan)
                }
                Spacer(minLength: 0)
                fitText
            }
            Spacer(minLength: 0)
            if let g, g.runs > 0 {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(Fmt.speed(g.decodeTPS)).font(Theme.numerals(big, .semibold)).foregroundStyle(Theme.cyan)
                        .lineLimit(1).minimumScaleFactor(0.5)
                    Text("tok/s").font(Theme.font(max(15, big * 0.26), .medium)).foregroundStyle(Theme.dim)
                    Spacer(minLength: 6)
                    Text(g.peakMemGB.map { Fmt.num($0, 1) } ?? "—").font(Theme.numerals(big * 0.6, .semibold))
                        .foregroundStyle(Theme.amber).lineLimit(1).minimumScaleFactor(0.5)
                    Text("GB").font(Theme.font(max(15, big * 0.24), .medium)).foregroundStyle(Theme.dim)
                }
                bar(g.decodeTPS).padding(.vertical, tall ? 6 : 2)
                if tall {
                    // Two lines when there is room: speed detail, then power and energy.
                    Text(speedDetail(g)).font(Theme.numerals(19, .regular)).foregroundStyle(Theme.text.opacity(0.8))
                        .lineLimit(1).minimumScaleFactor(0.7)
                    if let p = powerDetail(g) {
                        Text(p).font(Theme.numerals(19, .regular)).foregroundStyle(Theme.dim).lineLimit(1).minimumScaleFactor(0.7)
                    }
                } else if height > 120 {
                    Text([speedDetail(g), powerDetail(g)].compactMap { $0 }.joined(separator: " · "))
                        .font(Theme.numerals(15, .regular)).foregroundStyle(Theme.dim).lineLimit(1).minimumScaleFactor(0.8)
                }
            } else {
                Text(g == nil ? "not run" : "did not fit")
                    .font(Theme.font(min(34, max(16, big * 0.45)), .semibold))
                    .foregroundStyle(g == nil ? Theme.faint : Theme.amber)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 10).fill(isBest ? Theme.cyan.opacity(0.08) : Color.white.opacity(0.025)))
    }

    private var fitText: some View {
        Group {
            if let g {
                if g.runs > 0 && g.fails > 0 {
                    Text("\(g.runs) ✓ · \(g.fails) ✕").foregroundStyle(Theme.amber)
                } else if g.runs > 0 {
                    Text("✓ fits").foregroundStyle(Theme.cyan)
                } else {
                    Text("✕ no").foregroundStyle(Theme.amber)
                }
            }
        }
        .font(Theme.font(height > 220 ? 18 : 15, .medium))
    }

    private func bar(_ v: Double?) -> some View {
        GeometryReader { geo in
            let f = (v != nil && (maxDecode ?? 0) > 0) ? CGFloat(v! / maxDecode!) : 0
            ZStack(alignment: .leading) {
                Capsule().fill(Color.white.opacity(0.06))
                Capsule().fill(Theme.cyan.opacity(isBest ? 0.95 : 0.55)).frame(width: geo.size.width * max(0, min(1, f)))
            }
        }
        .frame(height: 6)
    }

    private func speedDetail(_ g: GroupAverage) -> String {
        // TTFT over a minute as m:ss (a 128K prefill can take 20 min)
        let ttft = g.ttftS.map { $0 >= 60 ? Fmt.duration($0) : Fmt.num($0, $0 < 10 ? 2 : 1) + " s" } ?? "—"
        return "prefill \(Fmt.speed(g.prefillTPS)) tok/s · TTFT \(ttft)"
    }

    private func powerDetail(_ g: GroupAverage) -> String? {
        var parts: [String] = []
        if let w = g.peakPkgPowerW { parts.append("\(Fmt.num(w, 0)) W peak") }
        if let e = g.energyWh { parts.append("\(Fmt.num(e, 2)) Wh per run") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

/// Toolbar menu: pick up to maxModels contenders, or let the most recent fill in.
struct CompareMenu: View {
    @Environment(MonitorModel.self) private var m

    var body: some View {
        let all = m.contenders
        let picked = m.comparedContenders
        Menu {
            Button {
                m.setCompareSpec("")
            } label: {
                if m.compareSpec.isEmpty { Label("Auto: \(CompareSelection.maxModels) most recent", systemImage: "checkmark") } else { Text("Auto: \(CompareSelection.maxModels) most recent") }
            }
            Divider()
            if all.isEmpty { Text("No completed language-model runs yet") }
            ForEach(all) { c in
                let on = picked.contains { $0.id == c.id }
                Button {
                    // From Auto, picking a model not on screen starts a fresh selection with it;
                    // unticking one keeps the other three.
                    if m.compareSpec.isEmpty && !on {
                        m.setCompareSpec(c.label)
                    } else {
                        m.setCompareSpec(CompareSelection.toggled(c, current: picked))
                    }
                } label: {
                    if on { Label(c.label, systemImage: "checkmark") } else { Text(c.label) }
                }
                .disabled(!on && picked.count >= CompareSelection.maxModels && !m.compareSpec.isEmpty)
            }
        } label: {
            Label("Models", systemImage: "rectangle.split.2x2")
        }
        .help("Models shown in Compare (up to \(CompareSelection.maxModels))")
    }
}
