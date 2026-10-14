import SwiftUI

// Agent-loop cache test: header line and the per-turn time-to-first-token bars.

/// Header: "B · Broken at the top · Turn 4 of 10 · cached 0 of 45,120 tokens".
/// Amber when a turn after the first got no cache hit (the story the test is about).
struct CacheTurnLine: View {
    var ct: CacheTurn
    var body: some View {
        let miss = ct.turn > 1 && ct.cachedTokens == 0
        let cached: String = {
            guard let z = ct.contextTokens else { return "" }
            guard let y = ct.cachedTokens else { return " · cached … of \(grouped(z)) tokens" }
            return " · cached \(grouped(y)) of \(grouped(z)) tokens"
        }()
        Text("\(ct.conditionLabel) · Turn \(ct.turn) of \(ct.turnsTotal)\(cached)")
            .font(Theme.font(20, .medium))
            .foregroundStyle(miss ? Theme.amber : Theme.cyan)
            .padding(.horizontal, 12).padding(.vertical, 5)
            .background(Capsule().fill((miss ? Theme.amber : Theme.cyan).opacity(0.12)))
            .overlay(Capsule().strokeBorder((miss ? Theme.amber : Theme.cyan).opacity(0.4), lineWidth: 1))
            .lineLimit(1)
    }
}

private func grouped(_ n: Int) -> String {
    let f = NumberFormatter(); f.numberStyle = .decimal; f.maximumFractionDigits = 0
    return f.string(from: NSNumber(value: n)) ?? String(n)
}

/// One bar per turn of the current run, height = time to first token. The scale is the longest TTFT seen this
/// session for the same model + runner, so condition A filmed after condition B is drawn on B's scale.
struct CacheTurnBars: View {
    @Environment(MonitorModel.self) private var m

    var body: some View {
        if let ct = m.cacheTurn, let r = m.run {
            let key = r.model + "|" + (r.runner ?? "")
            let scale = max(m.cacheScaleMax[key] ?? 0, m.cacheTTFTs.values.max() ?? 0, 1)
            Panel(padding: 14) {
                VStack(alignment: .leading, spacing: 6) {
                    HStack(alignment: .firstTextBaseline, spacing: 14) {
                        SectionLabel("Time to first token per turn")
                        Text(ct.conditionLabel).font(Theme.font(16, .medium)).foregroundStyle(Theme.text.opacity(0.85))
                        Spacer()
                        Text("scale: longest \(label(scale))").font(Theme.font(14)).foregroundStyle(Theme.faint)
                    }
                    GeometryReader { geo in
                        let n = max(ct.turnsTotal, 1)
                        let w = (geo.size.width - CGFloat(n - 1) * 10) / CGFloat(n)
                        let barMax = max(0, geo.size.height - 44)
                        HStack(alignment: .bottom, spacing: 10) {
                            ForEach(1...n, id: \.self) { t in
                                VStack(spacing: 3) {
                                    Spacer(minLength: 0)
                                    if let v = m.cacheTTFTs[t] {
                                        Text(label(v)).font(Theme.numerals(15, .semibold))
                                            .foregroundStyle(t > 1 && v > 0.5 * scale ? Theme.amber : Theme.cyan).lineLimit(1).minimumScaleFactor(0.6)
                                        RoundedRectangle(cornerRadius: 4)
                                            .fill(t > 1 && v > 0.5 * scale ? Theme.amber : Theme.cyan)
                                            .frame(height: max(3, barMax * CGFloat(v / scale)))
                                    } else if t == ct.turn {
                                        Text("…").font(Theme.numerals(15, .semibold)).foregroundStyle(Theme.dim)
                                        RoundedRectangle(cornerRadius: 4).strokeBorder(Theme.cyan.opacity(0.7), style: StrokeStyle(lineWidth: 2, dash: [5, 4]))
                                            .frame(height: 18)
                                    } else {
                                        RoundedRectangle(cornerRadius: 4).fill(Color.white.opacity(0.05)).frame(height: 3)
                                    }
                                    Text("\(t)").font(Theme.numerals(14, .regular)).foregroundStyle(Theme.dim)
                                }
                                .frame(width: max(0, w))
                            }
                        }
                    }
                }
            }
        }
    }

    private func label(_ s: Double) -> String { s >= 60 ? Fmt.duration(s) : Fmt.num(s, s < 10 ? 1 : 0) + " s" }
}
