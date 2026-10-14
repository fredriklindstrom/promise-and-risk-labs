import SwiftUI

/// 1. "Now testing" strip.
struct HeaderView: View {
    @Environment(MonitorModel.self) private var m

    var body: some View {
        let flash = m.findingFlashing
        let finding = m.finding
        HStack(alignment: .center, spacing: 28) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 14) {
                    SectionLabel(titleLabel, color: finding != nil ? Theme.amber : Theme.cyan)
                    if m.status != .queueComplete { StatusBadge() }
                }
                Text(modelName)
                    .font(Theme.font(m.presentation ? 52 : 46, .semibold))
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
                if let f = finding {
                    FindingLine(row: f)
                } else {
                    chips
                }
            }
            Spacer(minLength: 20)
            rightColumn
        }
        .padding(.horizontal, 28).padding(.vertical, 20)
        .frame(maxWidth: .infinity)
        .background(
            RoundedRectangle(cornerRadius: 14)
                .fill(finding != nil ? Theme.amber.opacity(flash ? 0.55 : 0.14) : Theme.panel)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(finding != nil ? Theme.amber : Theme.panelEdge, lineWidth: finding != nil ? 2 : 1)
        )
        // One flash per failure: bright amber for ~2 s (engine-timed), then eases back to the
        // subtle "finding" tint, which stays until the next run starts.
        .animation(.easeOut(duration: 1.5), value: flash)
    }

    private var titleLabel: String {
        if let f = m.finding {
            switch f.failKind {
            case .error: return "Run error · harness retries it"
            case .skipped: return "Skipped"
            default: return "Result · does not fit"
            }
        }
        switch m.status {
        case .running, .stale: return "Now testing"
        case .queueComplete: return "Queue complete"
        case .waiting, .noRunFolder: return m.run == nil ? "Waiting for run" : "Waiting for next run"
        }
    }

    private var modelName: String {
        if let r = m.run { return r.model }
        if let l = m.lastCompleted { return l.model }
        return "Waiting for run"
    }

    @ViewBuilder private var chips: some View {
        if let r = m.run {
            HStack(spacing: 10) {
                if let a = r.archLabel { Chip(text: a, color: a.hasPrefix("MoE") ? Theme.cyan : Theme.text) }
                if let rl = r.runnerLabel { Chip(text: rl) }
                if let q = r.quant { Chip(text: q) }
                if r.isMedia {
                    if let w = r.width, let h = r.height { Chip(text: "\(w)×\(h)") }
                    if let s = r.seconds { Chip(text: "\(Fmt.num(s, s.rounded() == s ? 0 : 1)) s clip") }
                    if let l = r.lora { Chip(text: "LoRA \(l)") }
                } else if let ct = m.cacheTurn {
                    CacheTurnLine(ct: ct)
                } else if let c = Fmt.context(r.contextTokens) {
                    Chip(text: "\(c) context")
                }
                if let l = r.loadLabel { Chip(text: l, color: l.lowercased() == "clean" ? Theme.text : Theme.amber) }
                memoryModeChip
            }
        } else if let l = m.lastCompleted {
            HStack(spacing: 10) {
                if let rn = l.runner { Chip(text: rn) }
                if let q = l.quant { Chip(text: q) }
                if !l.isMedia, let c = Fmt.context(l.contextTokens) { Chip(text: "\(c) context") }
                memoryModeChip
                Text("last completed run").font(Theme.font(18)).foregroundStyle(Theme.dim)
            }
        } else {
            Text("Start the harness — this screen follows runs/current automatically.")
                .font(Theme.font(20))
                .foregroundStyle(Theme.dim)
        }
    }

    /// Which of the four GPU Memory options is in effect on this Mac (kernel iogpu.wired_limit_mb).
    private var memoryModeChip: some View {
        let mode = GPULimit.mode(current: m.gpuLimitMiB)
        return HStack(spacing: 10) {
            Chip(text: "Memory mode: \(mode.name)", color: mode.color)
            if m.maxRuns { Chip(text: "Max Runs", color: Theme.amber) }
        }
    }

    private var rightColumn: some View {
        VStack(alignment: .trailing, spacing: 8) {
            if let r = m.run, let pos = r.queuePos, let total = r.queueTotal {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("Run").font(Theme.font(20)).foregroundStyle(Theme.dim)
                    Text("\(pos) of \(total)").font(Theme.numerals(30)).foregroundStyle(Theme.text)
                    if r.warmup {
                        Text("· warm-up").font(Theme.font(20)).foregroundStyle(Theme.dim)
                    } else if let rep = r.rep, let of = r.of {
                        Text("· rep \(rep)/\(of)").font(Theme.numerals(20, .regular)).foregroundStyle(Theme.dim)
                    }
                }
            }
            if let r = m.run {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(m.runEndedAt == nil ? "Elapsed" : "Took").font(Theme.font(20)).foregroundStyle(Theme.dim)
                    ElapsedValue(start: r.startedAt, end: m.runEndedAt)
                }
            }
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("Queue ETA").font(Theme.font(20)).foregroundStyle(Theme.dim)
                QueueETAValue()
            }
        }
    }
}

/// Leaf views that read the 1 Hz-changing values, so only they re-render each second.
struct ElapsedValue: View {
    @Environment(MonitorModel.self) private var m
    var start: Date
    var end: Date?
    var body: some View {
        Text(Fmt.duration((end ?? m.now).timeIntervalSince(start)))
            .font(Theme.numerals(30)).foregroundStyle(Theme.text)
    }
}

struct QueueETAValue: View {
    @Environment(MonitorModel.self) private var m
    var body: some View {
        if m.status == .queueComplete {
            Text("done").font(Theme.numerals(30)).foregroundStyle(Theme.cyan)
        } else if let eta = m.queueETA {
            Text(Fmt.eta(eta)).font(Theme.numerals(30)).foregroundStyle(Theme.text)
        } else {
            Text("after first run").font(Theme.font(20)).foregroundStyle(Theme.faint)
        }
    }
}

struct FindingLine: View {
    var row: ResultRow
    var body: some View {
        HStack(spacing: 12) {
            Text(row.shortReason)
                .font(Theme.font(26, .semibold))
                .foregroundStyle(Theme.amber)
            let parts = [row.runner, row.quant, Fmt.context(row.contextTokens).map { "\($0) context" }].compactMap { $0 }
            if !parts.isEmpty {
                Text("· " + parts.joined(separator: " · "))
                    .font(Theme.font(22))
                    .foregroundStyle(Theme.text.opacity(0.85))
            }
        }
        .lineLimit(1)
        .minimumScaleFactor(0.7)
    }
}

struct StatusBadge: View {
    @Environment(MonitorModel.self) private var m
    private var status: RunStatus { m.status }
    private var phase: String? { m.phase }
    private var eventsAge: Double? { m.eventsAge }
    private var hasFinding: Bool { m.finding != nil }

    var body: some View {
        let (text, color) = label
        HStack(spacing: 8) {
            Circle().fill(color).frame(width: 11, height: 11)
            Text(text).font(Theme.font(16, .semibold)).tracking(1.2).foregroundStyle(color)
        }
        .padding(.horizontal, 12).padding(.vertical, 4)
        .background(Capsule().fill(color.opacity(0.14)))
    }

    private var label: (String, Color) {
        switch status {
        case .stale:
            return ("NO DATA · \(clampedInt(eventsAge ?? 0) ?? 0) s", Theme.amber)
        case .running:
            let p = (phase ?? "running").uppercased()
            return ("LIVE · \(p)", Theme.cyan)
        case .queueComplete:
            return ("QUEUE COMPLETE", Theme.cyan)
        case .waiting:
            return (hasFinding ? "RECORDED" : "IDLE", Theme.dim)
        case .noRunFolder:
            return ("NO ACTIVE RUN", Theme.dim)
        }
    }
}
