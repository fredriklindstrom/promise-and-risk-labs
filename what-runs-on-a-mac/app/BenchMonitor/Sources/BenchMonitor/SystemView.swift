import SwiftUI

/// 3. System — memory and GPU over the last two minutes. Drawn with Canvas (one pass per second,
/// no animation) to keep the monitor's own CPU/GPU cost negligible.
struct SystemView: View {
    var body: some View {
        HStack(spacing: 18) {
            MemoryPanel()
            GPUPanel()
        }
    }
}

private let chartWindow: Double = 120

struct MemoryPanel: View {
    var body: some View {
        Panel(padding: 18) {
            VStack(alignment: .leading, spacing: 10) {
                MemoryHeader()
                PeakLine { m in
                    [("used", m.peaks.memUsedGB.map { "\(Fmt.num($0, 1)) GB" }, Theme.amber)]
                }
                MemoryChartLive()
            }
        }
    }
}

struct MemoryHeader: View {
    @Environment(MonitorModel.self) private var m
    var body: some View {
        let s = m.latest
        HStack(alignment: .firstTextBaseline, spacing: 16) {
            SectionLabel("Memory", color: Theme.amber)
            Text(s?.memUsedGB.map { Fmt.num($0, 1) } ?? "—")
                .font(Theme.numerals(40)).foregroundStyle(Theme.amber)
            Text("GB used of \(Int(ThisMac.memoryGB.rounded()))").font(Theme.font(20)).foregroundStyle(Theme.dim)
            Spacer(minLength: 8)
            Metric(label: "Pressure", value: (s?.pressure ?? "—").capitalized,
                   color: Theme.pressureColor(s?.pressure ?? "normal"))
            Metric(label: "Swap", value: Fmt.gb(s?.swapGB),
                   color: (s?.swapGB ?? 0) > 1 ? Theme.amber : Theme.text)
            Metric(label: "Disk free", value: Fmt.diskGB(s?.diskFreeGB), color: diskColor(s?.diskFreeGB))
        }
    }

    /// Disk over-subscription warning: red below the harness floor (downloads refuse, runs pause), amber below its
    /// warn level. Both come from the active manifest's disk_guard via run_start; before any run, from this Mac's disk size.
    private func diskColor(_ gb: Double?) -> Color {
        guard let gb else { return Theme.text }
        let floor = m.run?.diskFloorGB ?? ThisMac.info.defaultDiskFloorGB
        let warn = m.run?.diskWarnGB ?? ThisMac.info.defaultDiskWarnGB
        if gb < floor { return Theme.red }
        if gb < warn { return Theme.amber }
        return Theme.text
    }
}

struct MemoryChartLive: View {
    @Environment(MonitorModel.self) private var m
    var body: some View { MemoryChart(samples: m.samples, now: m.now, budget: m.gpuBudgetGB, raised: m.gpuLimitMiB > 0) }
}

struct GPUPanel: View {
    var body: some View {
        Panel(padding: 18) {
            VStack(alignment: .leading, spacing: 10) {
                GPUHeader()
                PeakLine { m in
                    m.powerSampling || m.peaks.pkgPowerW != nil
                        ? [("GPU", m.peaks.gpuPowerW.map { "\(Fmt.num($0, 1)) W" }, Theme.text),
                           ("package", m.peaks.pkgPowerW.map { "\(Fmt.num($0, 1)) W" }, Theme.text),
                           ("thermal", m.peaks.thermalWorst?.capitalized, Theme.thermalColor(m.peaks.thermalWorst)),
                           ("die", m.peaks.socTempMaxC.map { "\(Fmt.num($0, 0)) °C" }, Theme.text)]
                          + (m.peaks.gpuFreqMinMHz.map { [("min clock", "\(Fmt.num($0, 0)) MHz", Theme.text)] } ?? [])
                        : [("power", "not sampling", Theme.faint), ("thermal", m.peaks.thermalWorst?.capitalized, Theme.thermalColor(m.peaks.thermalWorst)),
                           ("die", m.peaks.socTempMaxC.map { "\(Fmt.num($0, 0)) °C" }, Theme.text)]
                }
                GPUChartLive()
            }
        }
    }
}

struct GPUHeader: View {
    @Environment(MonitorModel.self) private var m
    var body: some View {
        let s = m.latest
        HStack(alignment: .firstTextBaseline, spacing: 16) {
            SectionLabel("GPU", color: Theme.cyan)
            Text(s?.gpuUtilPct.map { Fmt.num($0, 0) } ?? "—")
                .font(Theme.numerals(40)).foregroundStyle(Theme.cyan)
            Text("% busy").font(Theme.font(20)).foregroundStyle(Theme.dim)
            Spacer(minLength: 8)
            Metric(label: "Thermal", value: (s?.thermalState ?? "—").capitalized + (s?.socTempMaxC.map { " · \(Fmt.num($0, 0)) °C" } ?? ""),
                   color: Theme.thermalColor(s?.thermalState))
            if let mhz = s?.gpuFreqMHz { Metric(label: "Clock", value: "\(Fmt.num(mhz, 0)) MHz", color: Theme.text) }
            if m.powerSampling {
                // Legend by colour: cyan = utilisation (left axis), white = GPU power (right axis).
                Metric(label: "GPU power (white)", value: s?.gpuPowerW.map { "\(Fmt.num($0, 1)) W" } ?? "—", color: Theme.text)
                Metric(label: "Package", value: s?.pkgPowerW.map { "\(Fmt.num($0, 1)) W" } ?? "—", color: Theme.text)
            } else {
                Text("power: not sampling · enable in the Power menu").font(Theme.font(18)).foregroundStyle(Theme.dim)
            }
        }
    }
}

struct GPUChartLive: View {
    @Environment(MonitorModel.self) private var m
    var body: some View { GPUChart(samples: m.samples, now: m.now) }
}

/// "Peak this run: …" while a run is live; "Peak last run: …" once it has ended (values held on screen).
struct PeakLine: View {
    @Environment(MonitorModel.self) private var m
    var items: (MonitorModel) -> [(String, String?, Color)]
    var body: some View {
        let live = m.run != nil && m.runEndedAt == nil
        HStack(alignment: .firstTextBaseline, spacing: 14) {
            Text(m.run == nil ? "Peak" : (live ? "Peak this run" : "Peak last run"))
                .font(Theme.font(17, .semibold)).foregroundStyle(Theme.dim)
            ForEach(Array(items(m).enumerated()), id: \.offset) { _, it in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(it.0).font(Theme.font(17)).foregroundStyle(Theme.dim)
                    Text(it.1 ?? "—").font(Theme.numerals(22)).foregroundStyle(it.2)
                }
            }
            Spacer(minLength: 0)
        }
    }
}

struct Metric: View {
    var label: String
    var value: String
    var color: Color
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(label).font(Theme.font(17)).foregroundStyle(Theme.dim)
            Text(value).font(Theme.numerals(24)).foregroundStyle(color)
        }
    }
}

// MARK: - Chart drawing

private struct Plot {
    let rect: CGRect
    let now: Date
    func x(_ t: Date) -> CGFloat {
        let age = now.timeIntervalSince(t)
        return rect.maxX - CGFloat(age / chartWindow) * rect.width
    }
    /// Values come from files: clamp to the axis so a stray huge, negative or non-finite value stays on the chart.
    func y(_ v: Double, max: Double) -> CGFloat {
        guard v.isFinite, max.isFinite, max > 0 else { return rect.maxY }
        return rect.maxY - CGFloat(Swift.min(Swift.max(v, 0), max) / max) * rect.height
    }
}

/// Splits samples into continuous runs (gap > 5 s breaks the line).
private func segments(_ samples: [SamplePoint], _ value: (SamplePoint) -> Double?) -> [[(Date, Double)]] {
    var out: [[(Date, Double)]] = []
    var cur: [(Date, Double)] = []
    var lastT: Date?
    for s in samples {
        guard let v = value(s) else { if !cur.isEmpty { out.append(cur); cur = [] }; lastT = nil; continue }
        if let lt = lastT, s.t.timeIntervalSince(lt) > 5, !cur.isEmpty { out.append(cur); cur = [] }
        cur.append((s.t, v)); lastT = s.t
    }
    if !cur.isEmpty { out.append(cur) }
    return out
}

private func drawTimeAxis(_ ctx: inout GraphicsContext, _ p: Plot) {
    for sec in stride(from: 0.0, through: chartWindow, by: 30) {
        let x = p.rect.maxX - CGFloat(sec / chartWindow) * p.rect.width
        var line = Path(); line.move(to: CGPoint(x: x, y: p.rect.minY)); line.addLine(to: CGPoint(x: x, y: p.rect.maxY))
        ctx.stroke(line, with: .color(.white.opacity(0.05)), lineWidth: 1)
        let label = sec == 0 ? "now" : "−\(Int(sec))s"
        ctx.draw(Text(label).font(Theme.font(14)).foregroundColor(Theme.faint),
                 at: CGPoint(x: x, y: p.rect.maxY + 6), anchor: sec == 0 ? .topTrailing : .top)
    }
}

private func drawSeries(_ ctx: inout GraphicsContext, _ p: Plot, _ segs: [[(Date, Double)]], max: Double,
                        color: Color, width: CGFloat, fill: Double) {
    for seg in segs {
        var line = Path()
        for (i, pt) in seg.enumerated() {
            let c = CGPoint(x: Swift.max(p.rect.minX, p.x(pt.0)), y: p.y(pt.1, max: max))
            i == 0 ? line.move(to: c) : line.addLine(to: c)
        }
        if fill > 0, let first = seg.first, let last = seg.last {
            var area = line
            area.addLine(to: CGPoint(x: p.x(last.0), y: p.rect.maxY))
            area.addLine(to: CGPoint(x: Swift.max(p.rect.minX, p.x(first.0)), y: p.rect.maxY))
            area.closeSubpath()
            ctx.fill(area, with: .color(color.opacity(fill)))
        }
        if seg.count == 1, let only = seg.first {
            let c = CGPoint(x: p.x(only.0), y: p.y(only.1, max: max))
            ctx.fill(Path(ellipseIn: CGRect(x: c.x - 3, y: c.y - 3, width: 6, height: 6)), with: .color(color))
        } else {
            ctx.stroke(line, with: .color(color), style: StrokeStyle(lineWidth: width, lineCap: .round, lineJoin: .round))
        }
    }
}

// Each chart = a dynamic layer (data, redrawn at 1 Hz) under a static layer (grid, axis labels,
// reference lines). The static layer is its own view with plain value inputs, so SwiftUI skips
// re-rendering it unless those inputs change — the per-second redraw only touches the data path.

private func plotRect(_ size: CGSize, rightInset: CGFloat) -> CGRect {
    CGRect(x: 64, y: 8, width: size.width - 64 - rightInset, height: size.height - 8 - 28)
}

struct MemoryChart: View {
    var samples: [SamplePoint]
    var now: Date
    var budget: Double
    var raised = false

    var body: some View {
        ZStack {
            MemorySeries(samples: samples, now: now)
            MemoryGrid(budget: budget, raised: raised)
        }
    }
}

/// Memory axis sized to THIS Mac: physical RAM plus headroom, gridlines at a step that gives ~4 lines.
private var memYMax: Double { ThisMac.memoryGB * 1.08 }
private var memGridStep: Double {
    [2.0, 4, 8, 16, 24, 32, 64, 128, 256].first { ThisMac.memoryGB / $0 <= 4 } ?? 128
}

struct MemorySeries: View {
    var samples: [SamplePoint]
    var now: Date
    var body: some View {
        Canvas { ctx, size in
            let plot = Plot(rect: plotRect(size, rightInset: 8), now: now)
            // Pressure tint behind the data (subtle): green normal, amber warn, red critical.
            for (i, s) in samples.enumerated() {
                let x0 = max(plot.rect.minX, plot.x(s.t))
                let x1 = i + 1 < samples.count ? plot.x(samples[i + 1].t) : plot.rect.maxX
                guard x1 > x0 else { continue }
                let op = s.pressure == "critical" ? 0.22 : s.pressure == "warn" ? 0.16 : 0.07
                ctx.fill(Path(CGRect(x: x0, y: plot.rect.minY, width: x1 - x0, height: plot.rect.height)),
                         with: .color(Theme.pressureColor(s.pressure).opacity(op)))
            }
            drawSeries(&ctx, plot, segments(samples) { $0.memUsedGB }, max: memYMax, color: Theme.amber, width: 3, fill: 0.28)
        }
    }
}

struct MemoryGrid: View {
    var budget: Double
    var raised = false
    var body: some View {
        Canvas { ctx, size in
            let plot = Plot(rect: plotRect(size, rightInset: 8), now: Date())
            for g in stride(from: 0.0, through: ThisMac.memoryGB, by: memGridStep) {
                let y = plot.y(g, max: memYMax)
                var line = Path(); line.move(to: CGPoint(x: plot.rect.minX, y: y)); line.addLine(to: CGPoint(x: plot.rect.maxX, y: y))
                ctx.stroke(line, with: .color(.white.opacity(0.06)), lineWidth: 1)
                ctx.draw(Text("\(Int(g)) GB").font(Theme.numerals(14, .regular)).foregroundColor(Theme.faint),
                         at: CGPoint(x: plot.rect.minX - 8, y: y), anchor: .trailing)
            }
            drawTimeAxis(&ctx, plot)
            // Reference lines: physical RAM (label right, above) and the GPU budget (label left, below).
            let y96 = plot.y(ThisMac.memoryGB, max: memYMax)
            var l96 = Path(); l96.move(to: CGPoint(x: plot.rect.minX, y: y96)); l96.addLine(to: CGPoint(x: plot.rect.maxX, y: y96))
            ctx.stroke(l96, with: .color(Theme.text.opacity(0.7)), style: StrokeStyle(lineWidth: 1.5, dash: [8, 6]))
            ctx.draw(Text("\(Int(ThisMac.memoryGB.rounded())) GB physical").font(Theme.font(15, .medium)).foregroundColor(Theme.text.opacity(0.9)),
                     at: CGPoint(x: plot.rect.maxX - 8, y: y96 - 3), anchor: .bottomTrailing)
            let yb = plot.y(budget, max: memYMax)
            var lb = Path(); lb.move(to: CGPoint(x: plot.rect.minX, y: yb)); lb.addLine(to: CGPoint(x: plot.rect.maxX, y: yb))
            ctx.stroke(lb, with: .color(Theme.amber.opacity(0.8)), style: StrokeStyle(lineWidth: 1.5, dash: [8, 6]))
            ctx.draw(Text("GPU budget \(Fmt.num(budget, 1)) GB" + (raised ? " · raised (GPU Memory menu)" : "")).font(Theme.font(15, .medium)).foregroundColor(Theme.amber),
                     at: CGPoint(x: plot.rect.minX + 10, y: yb + 3), anchor: .topLeading)
        }
    }
}

struct GPUChart: View {
    static let maxPowerScale: Double = 1000
    var samples: [SamplePoint]
    var now: Date

    var body: some View {
        // Right axis: 40 W minimum, rounded up to 10 W, capped at 1000 W (no Mac draws more; a bad
        // value in power.jsonl must not blow up the scale).
        let maxPower = min(samples.compactMap { $0.gpuPowerW }.filter(\.isFinite).max() ?? 0, Self.maxPowerScale)
        let powerScale = min(Self.maxPowerScale, max(40, ((maxPower * 1.2) / 10).rounded(.up) * 10))
        ZStack {
            GPUSeries(samples: samples, now: now, powerScale: powerScale, hasPower: maxPower > 0)
            GPUGrid(powerScale: powerScale, hasPower: maxPower > 0)
        }
    }
}

private let gpuRightInset: CGFloat = 60

struct GPUSeries: View {
    var samples: [SamplePoint]
    var now: Date
    var powerScale: Double
    var hasPower: Bool
    var body: some View {
        Canvas { ctx, size in
            let plot = Plot(rect: plotRect(size, rightInset: gpuRightInset), now: now)
            drawSeries(&ctx, plot, segments(samples) { $0.gpuUtilPct }, max: 100, color: Theme.cyan, width: 3, fill: 0.22)
            if hasPower {
                drawSeries(&ctx, plot, segments(samples) { $0.gpuPowerW }, max: powerScale, color: Theme.text, width: 2, fill: 0)
            }
        }
    }
}

struct GPUGrid: View {
    var powerScale: Double
    var hasPower: Bool
    var body: some View {
        Canvas { ctx, size in
            let plot = Plot(rect: plotRect(size, rightInset: gpuRightInset), now: Date())
            for g in stride(from: 0.0, through: 100, by: 25) {
                let y = plot.y(g, max: 100)
                var line = Path(); line.move(to: CGPoint(x: plot.rect.minX, y: y)); line.addLine(to: CGPoint(x: plot.rect.maxX, y: y))
                ctx.stroke(line, with: .color(.white.opacity(0.06)), lineWidth: 1)
                ctx.draw(Text("\(Int(g))%").font(Theme.numerals(14, .regular)).foregroundColor(Theme.faint),
                         at: CGPoint(x: plot.rect.minX - 8, y: y), anchor: .trailing)
                if hasPower {
                    ctx.draw(Text("\(clampedInt(powerScale * g / 100) ?? 0) W").font(Theme.numerals(14, .regular)).foregroundColor(Theme.dim),
                             at: CGPoint(x: plot.rect.maxX + 8, y: y), anchor: .leading)
                }
            }
            drawTimeAxis(&ctx, plot)
        }
    }
}
