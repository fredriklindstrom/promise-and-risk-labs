import SwiftUI

/// 2. Speed — three big numbers (LLM) or step/clip progress (media).
/// Between runs, and until the next run produces numbers, the last completed run stays on screen
/// labelled "Last run". The small line under each number is the running average for that config.
struct SpeedView: View {
    @Environment(MonitorModel.self) private var m

    var body: some View {
        let showLive = m.isActive && ((m.run?.isMedia == true) ? m.media != nil : (m.live?.hasNumbers ?? false))
        VStack(alignment: .leading, spacing: m.presentation ? 18 : 10) {
            HStack(spacing: 14) {
                if showLive {
                    SectionLabel(m.status == .stale ? "Speed · last values (no data)" : "Speed · live",
                                 color: m.status == .stale ? Theme.amber : Theme.cyan)
                } else if let last = m.lastCompleted {
                    SectionLabel("Last run", color: Theme.dim)
                    Text(lastRunCaption(last))
                        .font(Theme.font(18, .medium))
                        .foregroundStyle(Theme.text.opacity(0.8))
                        .lineLimit(1)
                    if m.isActive {
                        Text("· next numbers when \(m.run?.isMedia == true ? "the first step lands" : "prefill starts")")
                            .font(Theme.font(16)).foregroundStyle(Theme.faint)
                    }
                } else {
                    SectionLabel("Speed", color: Theme.dim)
                }
                Spacer()
            }
            if showLive, let run = m.run, run.isMedia {
                mediaTiles(m.media, run: run, dim: m.status == .stale)
            } else if showLive {
                llmTiles(decode: m.live?.decodeTPS, prefill: m.live?.prefillTPS, ttft: m.live?.ttftS,
                         group: m.currentGroup, dim: m.status == .stale, tokens: m.live?.tokens)
            } else if let last = m.lastCompleted {
                if last.failed {
                    failedTile(last)
                } else if last.isMedia {
                    lastMediaTiles(last)
                } else {
                    llmTiles(decode: last.decodeTPS, prefill: last.prefillTPS, ttft: last.ttftS,
                             group: m.group(for: last), dim: true, tokens: nil)
                }
            } else {
                llmTiles(decode: nil, prefill: nil, ttft: nil, group: nil, dim: true, tokens: nil)
            }
        }
    }

    private func lastRunCaption(_ r: ResultRow) -> String {
        [r.model, r.runner, r.quant, Fmt.context(r.contextTokens)].compactMap { $0 }.joined(separator: " · ")
    }

    private var bigSize: CGFloat { m.presentation ? 168 : 104 }

    private func llmTiles(decode: Double?, prefill: Double?, ttft: Double?, group: GroupAverage?, dim: Bool, tokens: Int?) -> some View {
        let avgNote: (Double?, String) -> String? = { v, unit in
            guard let g = group, g.runs > 0, let v else { return nil }
            return "avg \(unit == "s" ? Fmt.num(v, 2) : Fmt.speed(v)) \(unit) over \(g.runs) run\(g.runs == 1 ? "" : "s")"
        }
        return HStack(spacing: 18) {
            BigNumber(label: "Decode", value: Fmt.speed(decode), unit: "tok/s", color: Theme.cyan, size: bigSize,
                      dim: dim, note: avgNote(group?.decodeTPS, "tok/s") ?? tokens.map { "\($0) tokens generated" })
            BigNumber(label: "Prefill", value: Fmt.speed(prefill), unit: "tok/s", color: Theme.text, size: bigSize * 0.78,
                      dim: dim, note: avgNote(group?.prefillTPS, "tok/s"))
            BigNumber(label: "Time to first token", value: ttft.map { Fmt.num($0, $0 < 10 ? 2 : 1) } ?? "—", unit: "s",
                      color: Theme.text, size: bigSize * 0.78, dim: dim, note: avgNote(group?.ttftS, "s"))
        }
    }

    private func mediaTiles(_ p: MediaProgress?, run: RunInfo, dim: Bool) -> some View {
        let clips = p?.clips ?? run.clips
        let clip = p?.clip ?? run.clip
        let done = clip.map { max(0, $0 - 1) }
        return HStack(spacing: 18) {
            BigNumber(label: "Step", value: "\(p?.step.map(String.init) ?? "—") / \(p?.steps.map(String.init) ?? run.steps.map(String.init) ?? "—")",
                      unit: "", color: Theme.cyan, size: bigSize * 0.9, dim: dim, note: nil)
            BigNumber(label: "Seconds per step", value: Fmt.num(p?.sPerStep, (p?.sPerStep ?? 0) >= 100 ? 0 : 1), unit: "s",
                      color: Theme.text, size: bigSize * 0.78, dim: dim,
                      note: m.currentGroup?.sPerStep.map { "avg \(Fmt.num($0, 1)) s over \(m.currentGroup?.runs ?? 0) runs" })
            BigNumber(label: "ETA this clip", value: Fmt.duration(etaRemaining(p)), unit: "", color: Theme.text,
                      size: bigSize * 0.78, dim: dim, note: nil)
            BigNumber(label: "Clips done", value: "\(done.map(String.init) ?? "—") / \(clips.map(String.init) ?? "—")", unit: "",
                      color: Theme.text, size: bigSize * 0.78, dim: dim, note: clip.map { "rendering clip \($0)" })
        }
    }

    /// eta_s counts down between media_progress events.
    private func etaRemaining(_ p: MediaProgress?) -> Double? {
        guard let p, let eta = p.etaS else { return nil }
        return max(0, eta - m.now.timeIntervalSince(p.receivedAt))
    }

    private func lastMediaTiles(_ r: ResultRow) -> some View {
        HStack(spacing: 18) {
            BigNumber(label: "Seconds per step", value: Fmt.num(r.sPerStep, 1), unit: "s", color: Theme.cyan, size: bigSize,
                      dim: true, note: m.group(for: r)?.sPerStep.map { "avg \(Fmt.num($0, 1)) s" })
            BigNumber(label: "Wall time", value: Fmt.duration(r.wallS), unit: "", color: Theme.text, size: bigSize * 0.78, dim: true, note: nil)
            BigNumber(label: "Peak memory", value: Fmt.num(r.peakMemGB, 1), unit: "GB", color: Theme.amber, size: bigSize * 0.78, dim: true, note: nil)
        }
    }

    private func failedTile(_ r: ResultRow) -> some View {
        let title: String = r.failKind == .didNotFit ? "Result · does not fit" : (r.failKind == .error ? "Run error" : "Skipped")
        let color = r.failKind == .didNotFit ? Theme.amber : Theme.dim
        return HStack(spacing: 18) {
            BigNumber(label: title, value: r.shortReason, unit: "", color: color, size: bigSize * 0.5, dim: false,
                      note: [r.reasonDetail, r.failKind == .error ? "retried on restart" : "recorded in the results table"]
                        .compactMap { $0 }.joined(separator: " · "))
                .frame(maxWidth: .infinity)
            if let peak = r.peakMemGB {
                BigNumber(label: "Peak memory", value: Fmt.num(peak, 1), unit: "GB", color: Theme.amber,
                          size: bigSize * 0.62, dim: false, note: nil)
                    .frame(width: 420)
            } else if let ctx = Fmt.context(r.contextTokens) {
                BigNumber(label: "Context tried", value: ctx, unit: "", color: Theme.text,
                          size: bigSize * 0.62, dim: false, note: [r.runner, r.quant].compactMap { $0 }.joined(separator: " · "))
                    .frame(width: 420)
            }
        }
    }
}

struct BigNumber: View {
    var label: String
    var value: String
    var unit: String
    var color: Color
    var size: CGFloat
    var dim: Bool
    var note: String?

    var body: some View {
        Panel(padding: 22) {
            VStack(alignment: .leading, spacing: 4) {
                Text(label).font(Theme.font(22, .medium)).foregroundStyle(Theme.dim)
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(value)
                        .font(Theme.numerals(size, .semibold))
                        .foregroundStyle(color.opacity(dim ? 0.62 : 1))
                        .lineLimit(1)
                        .minimumScaleFactor(0.4)
                    if !unit.isEmpty {
                        Text(unit).font(Theme.font(max(22, size * 0.26), .medium)).foregroundStyle(Theme.dim)
                    }
                }
                Spacer(minLength: 0)
                Text(note ?? " ")
                    .font(Theme.numerals(18, .regular))
                    .foregroundStyle(Theme.dim)
                    .lineLimit(1)
            }
        }
    }
}
