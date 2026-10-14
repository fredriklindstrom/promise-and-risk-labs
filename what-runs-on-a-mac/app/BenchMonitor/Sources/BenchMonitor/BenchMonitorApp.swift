import AppKit
import SwiftUI

@main
struct BenchMonitorApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @State private var model = MonitorModel()

    var body: some Scene {
        Window("Bench Monitor", id: "main") {
            ContentView()
                .environment(model)
                .onAppear {
                    model.start()
                    appDelegate.model = model
                }
        }
        .defaultSize(width: 1920, height: 1080)
        .windowToolbarStyle(.unifiedCompact(showsTitle: true))
        .commands {
            CommandGroup(replacing: .printItem) {
                Button("Save Snapshot PNG") { Snapshotter.save(model: model, name: nil) }
                    .keyboardShortcut("s", modifiers: [.command, .shift])
            }
            // View menu: which screen is up (ticked), a quick switch, and Presentation for the dashboard.
            CommandGroup(before: .toolbar) {
                Toggle("Dashboard", isOn: Binding(get: { !model.compareMode }, set: { if $0 { model.compareMode = false } }))
                    .keyboardShortcut("1", modifiers: .command)
                Toggle("Compare Models", isOn: Binding(get: { model.compareMode }, set: { if $0 { model.compareMode = true } }))
                    .keyboardShortcut("2", modifiers: .command)
                Button("Switch View") { model.compareMode.toggle() }
                    .keyboardShortcut("k", modifiers: .command)
                Divider()
                Toggle("Presentation", isOn: Binding(get: { model.presentation }, set: { model.presentation = $0 }))
                    .keyboardShortcut("p", modifiers: .command)
                    .disabled(model.compareMode)      // Presentation enlarges the dashboard's speed numbers only
                Divider()
            }
            CommandGroup(replacing: .newItem) {}
            // GPU Memory: the kernel's GPU limit (iogpu.wired_limit_mb). Every change asks for confirmation and an
            // administrator password; presets that would leave macOS under 4 GB are greyed out.
            CommandMenu("GPU Memory") {
                Text(GPULimit.statusLine(current: model.gpuLimitMiB))
                Divider()
                Toggle("macOS Default: \(Fmt.num(Double(GPULimit.defaultMiB) / 1024, 1)) GB for the GPU",
                       isOn: Binding(get: { model.gpuLimitMiB == 0 }, set: { if $0 { model.requestGPULimit(nil) } }))
                ForEach(GPULimitPreset.allCases) { preset in
                    let plan = GPULimit.plan(preset)
                    Toggle(GPULimit.menuLabel(plan),
                           isOn: Binding(get: { model.gpuLimitMiB == plan.limitMiB }, set: { if $0 { model.requestGPULimit(preset) } }))
                        .disabled(!plan.available)
                }
                Divider()
                Toggle("Max Runs: Sleep the Display When Each Run Starts",
                       isOn: Binding(get: { model.maxRuns }, set: { model.setMaxRuns($0) }))
            }
            // Power: the built-in sampler (energy counters, no password). On by default; the choice is remembered.
            CommandMenu("Power") {
                Text(model.powerOn ? "Power sampling: on" : "Power sampling: off")
                if model.powerOn, PowerSampler.shared.deferringToExternal {
                    Text("powermetrics is running: it writes the power log, Bench Monitor only displays")
                }
                Divider()
                Button("Start Power Sampling") { model.startPowerSampling() }
                    .disabled(model.powerOn)
                Button("Stop Power Sampling") { model.stopPowerSampling() }
                    .disabled(!model.powerOn)
            }
        }

        Settings {
            SettingsView().environment(model)
        }
    }
}

/// Remote control for filming / testing, via distributed notifications (no permissions needed).
/// Opt-in: any process running as this user can post these, so they are only observed when the
/// Settings toggle is on or the app was launched with BENCH_REMOTE=1.
///   io.github.fredriklindstrom.benchmonitor.presentation  object "on" | "off" | "toggle"
///   io.github.fredriklindstrom.benchmonitor.snapshot      object = file name ([A-Za-z0-9._-], ≤ 64 chars;
///                                                          ".png" appended if missing); PNG written to the screenshots folder
///   io.github.fredriklindstrom.benchmonitor.compare       object "on" | "off" | "toggle", or "models:<spec>" (≤ 512 chars,
///                                                          see CompareSelection.resolve; also turns Compare on)
enum RemoteControl {
    static let presentationName = "io.github.fredriklindstrom.benchmonitor.presentation"
    static let snapshotName = "io.github.fredriklindstrom.benchmonitor.snapshot"
    static let maxSnapshotsPerLaunch = 500
    static let compareName = "io.github.fredriklindstrom.benchmonitor.compare"
    static let maxSpecLength = 512
    static let defaultsKey = "remoteControl"
    static let minSnapshotInterval: TimeInterval = 1

    static var enabled: Bool {
        ProcessInfo.processInfo.environment["BENCH_REMOTE"] == "1" || UserDefaults.standard.bool(forKey: defaultsKey)
    }

    /// A safe PNG file name from a remote request, or nil: 1–64 ASCII letters, digits, ".", "_" or "-",
    /// not starting with "." (so no "..", no hidden files, no path separators).
    static func snapshotFileName(_ raw: String) -> String? {
        let scalars = raw.unicodeScalars
        guard (1...64).contains(scalars.count), raw.first != "." else { return nil }
        let ok = scalars.allSatisfy { u in
            switch u {
            case "A"..."Z", "a"..."z", "0"..."9", ".", "_", "-": return true
            default: return false
            }
        }
        guard ok else { return nil }
        return raw.hasSuffix(".png") ? raw : raw + ".png"
    }
}

extension UserDefaults {
    /// KVO-observable mirror of the "remoteControl" key (the property name must match the key).
    @objc dynamic var remoteControl: Bool { bool(forKey: RemoteControl.defaultsKey) }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: MonitorModel?
    private var observers: [NSObjectProtocol] = []
    private var defaultsObserver: NSKeyValueObservation?
    private var lastRemoteSnapshot = Date.distantPast
    private var remoteSnapshotCount = 0

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Also works when launched as a bare executable (swift run) rather than from the .app bundle.
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)

        syncRemoteControl()
        // The Settings toggle (or `defaults write … remoteControl`) takes effect at once.
        defaultsObserver = UserDefaults.standard.observe(\.remoteControl, options: [.new]) { [weak self] _, _ in
            DispatchQueue.main.async { self?.syncRemoteControl() }
        }
    }

    private func syncRemoteControl() {
        let dnc = DistributedNotificationCenter.default()
        let want = RemoteControl.enabled
        guard want != !observers.isEmpty else { return }
        guard want else {
            observers.forEach { dnc.removeObserver($0) }
            observers = []
            NSLog("Bench Monitor: remote control off")
            return
        }
        observers.append(dnc.addObserver(forName: .init(RemoteControl.presentationName), object: nil, queue: .main) { [weak self] n in
            guard let m = self?.model else { return }
            switch n.object as? String {
            case "on": m.presentation = true
            case "off": m.presentation = false
            case "toggle", nil: m.presentation.toggle()
            default: break
            }
        })
        observers.append(dnc.addObserver(forName: .init(RemoteControl.compareName), object: nil, queue: .main) { [weak self] n in
            guard let m = self?.model else { return }
            let raw = n.object as? String
            switch raw {
            case "on": m.compareMode = true
            case "off": m.compareMode = false
            case "toggle", nil: m.compareMode.toggle()
            case let s? where s.hasPrefix("models:"):
                // Only display text: the spec is matched against labels already on screen, never used as a path.
                guard s.count <= RemoteControl.maxSpecLength + 7 else {
                    NSLog("Bench Monitor: compare request rejected (spec too long)")
                    return
                }
                m.setCompareSpec(String(s.dropFirst(7)).filter { !$0.isNewline || $0 == "\n" })
                m.compareMode = true
            default: break
            }
        })
        observers.append(dnc.addObserver(forName: .init(RemoteControl.snapshotName), object: nil, queue: .main) { [weak self] n in
            guard let self, let m = self.model else { return }
            let now = Date()
            guard now.timeIntervalSince(self.lastRemoteSnapshot) >= RemoteControl.minSnapshotInterval else {
                NSLog("Bench Monitor: snapshot request ignored (more than one per second)")
                return
            }
            var name: String? = nil
            if let raw = n.object as? String {
                guard let safe = RemoteControl.snapshotFileName(raw) else {
                    NSLog("Bench Monitor: snapshot request rejected (invalid file name)")
                    return
                }
                name = safe
            }
            // At most this many remote snapshots per launch, so a stream of requests can't fill the disk (review L1).
            guard self.remoteSnapshotCount < RemoteControl.maxSnapshotsPerLaunch else {
                NSLog("Bench Monitor: snapshot request ignored (limit of %d per launch reached)", RemoteControl.maxSnapshotsPerLaunch)
                return
            }
            self.remoteSnapshotCount += 1
            self.lastRemoteSnapshot = now
            MainActor.assumeIsolated { _ = Snapshotter.save(model: m, name: name) }
        })
        NSLog("Bench Monitor: remote control on")
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

struct ContentView: View {
    @Environment(MonitorModel.self) private var m

    var body: some View {
        // GeometryReader stops the window's min-size query from measuring every Text in the
        // dashboard on each 1 Hz update (a measurable share of the app's CPU in profiling).
        GeometryReader { _ in DashboardView() }
            .frame(minWidth: 1280, minHeight: 720)
            .background(Theme.navy)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Toggle(isOn: Binding(get: { m.compareMode }, set: { m.compareMode = $0 })) {
                        Label("Compare", systemImage: "square.grid.2x2")
                    }
                    .toggleStyle(.button)
                    .help("Compare (⌘2, or ⌘K to switch): up to five models side by side")
                }
                ToolbarItem(placement: .primaryAction) {
                    CompareMenu()
                }
                ToolbarItem(placement: .primaryAction) {
                    Toggle(isOn: Binding(get: { m.presentation }, set: { m.presentation = $0 })) {
                        Label("Presentation", systemImage: m.presentation ? "rectangle.inset.filled" : "rectangle.split.3x1")
                    }
                    .toggleStyle(.button)
                    .help("Presentation (⌘P): hide the results table, enlarge the speed numbers")
                }
            }
            .toolbarBackground(Theme.navy, for: .windowToolbar)
            .preferredColorScheme(.dark)
            .alert("Raise the GPU memory limit?",
                   isPresented: Binding(get: { m.gpuLimitPending != nil }, set: { if !$0 { m.gpuLimitPending = nil } }),
                   presenting: m.gpuLimitPending.map { GPULimit.plan($0) }) { plan in
                Button("Raise to \(Fmt.num(plan.limitGB, 1)) GB (\(plan.preset.title))", role: .destructive) {
                    m.gpuLimitPending = nil
                    m.applyGPULimit(mib: plan.limitMiB)
                }
                Button("Cancel", role: .cancel) { m.gpuLimitPending = nil }
            } message: { plan in
                Text(GPULimit.warning(plan))
            }
            .alert("Return to the macOS default?", isPresented: Binding(get: { m.gpuLimitDefaultPending }, set: { m.gpuLimitDefaultPending = $0 })) {
                Button("Return to macOS Default") {
                    m.gpuLimitDefaultPending = false
                    m.applyGPULimit(mib: 0)
                }
                Button("Cancel", role: .cancel) { m.gpuLimitDefaultPending = false }
            } message: {
                Text("Sets iogpu.wired_limit_mb back to 0, so the GPU gets macOS's own \(Fmt.num(Double(GPULimit.defaultMiB) / 1024, 1)) GB. "
                     + "Do this with no model loaded. macOS will ask for an administrator password.")
            }
            .alert("Power sampling", isPresented: Binding(get: { m.powerMessage != nil }, set: { if !$0 { m.powerMessage = nil } })) {
                Button("OK", role: .cancel) { m.powerMessage = nil }
            } message: {
                Text(m.powerMessage ?? "")
            }
            .alert("GPU memory limit", isPresented: Binding(get: { m.gpuLimitError != nil }, set: { if !$0 { m.gpuLimitError = nil } })) {
                Button("OK", role: .cancel) { m.gpuLimitError = nil }
            } message: {
                Text(m.gpuLimitError ?? "")
            }
    }
}

/// The whole screen. Kept separate from ContentView so the snapshot renderer can draw it offscreen.
struct DashboardView: View {
    @Environment(MonitorModel.self) private var m

    var body: some View {
        VStack(spacing: 18) {
            HeaderView()
            if m.compareMode {
                CompareView()
                    .frame(maxHeight: .infinity)
            } else {
                SpeedView()
                    .frame(height: m.presentation ? 330 : 230)
                if m.cacheTurn != nil {
                    CacheTurnBars()          // agent-loop cache test only
                        .frame(height: m.presentation ? 300 : 230)
                }
                SystemView()
                    .frame(maxHeight: m.presentation ? .infinity : 330)
                if !m.presentation {
                    ResultsView()
                        .frame(maxHeight: .infinity)
                }
            }
            FooterLine()
        }
        .padding(.horizontal, 24)
        .padding(.top, 14)
        .padding(.bottom, 10)
        .background(Theme.navy)
    }
}

struct FooterLine: View {
    @Environment(MonitorModel.self) private var m
    @AppStorage("footerNote") private var footerNote = ""     // e.g. a disclaimer about the test machine
    var body: some View {
        HStack(spacing: 18) {
            Text("Promise & Risk of AI · \(m.machineLabel)")
            if !footerNote.isEmpty {
                Text(footerNote)
                    .foregroundStyle(Theme.amber.opacity(0.9))
                    .layoutPriority(1)
            }
            Spacer()
            if let ext = m.externalDiskGB, let name = m.extraVolumeName {
                Text("\(name) free \(Fmt.diskGB(Double(ext)))")
            }
            Text("samples: \(m.sampleSource)")
            if let f = m.currentRunFolder { Text("runs/\(f)") }
        }
        .font(Theme.font(14))
        .foregroundStyle(Theme.faint)
        .lineLimit(1)
    }
}

// MARK: - Offscreen snapshot (no Screen Recording permission needed)

enum Snapshotter {
    /// `name` must already be a plain file name (menu: nil; remote: RemoteControl.snapshotFileName).
    /// Never overwrites and never writes through a symlink: the file is created with O_EXCL|O_NOFOLLOW,
    /// and a taken name gets "-2", "-3", … appended.
    @MainActor
    @discardableResult
    static func save(model: MonitorModel, name: String?) -> String? {
        let size = NSApp.windows.first { $0.isVisible && $0.contentView != nil && $0.title == "Bench Monitor" }?
            .contentLayoutRect.size ?? CGSize(width: 1920, height: 1052)
        let view = DashboardView()
            .environment(model)
            .frame(width: size.width, height: size.height)
            .background(Theme.navy)
            .environment(\.colorScheme, .dark)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 1
        guard let cg = renderer.cgImage else { return nil }
        let rep = NSBitmapImageRep(cgImage: cg)
        guard let png = rep.representation(using: .png, properties: [:]) else { return nil }
        let dir = ProcessInfo.processInfo.environment["BENCH_SNAPSHOT_DIR"].flatMap { $0.isEmpty ? nil : $0 }
            ?? (model.config.projectDir + "/app/BenchMonitor/screenshots")
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        let f = DateFormatter(); f.dateFormat = "yyyyMMdd-HHmmss"
        var file = ((name ?? "bench-monitor-\(f.string(from: Date()))") as NSString).lastPathComponent
        if !file.hasSuffix(".png") { file += ".png" }
        let stem = String(file.dropLast(4))
        for i in 1...100 {
            let path = dir + "/" + (i == 1 ? file : "\(stem)-\(i).png")
            let fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o644)
            if fd < 0 {
                if errno == EEXIST { continue }      // taken (or a symlink): try the next name
                NSLog("Bench Monitor: cannot write snapshot %@: %s", path, strerror(errno))
                return nil
            }
            let ok = png.withUnsafeBytes { buf -> Bool in
                var off = 0
                while off < buf.count {
                    let n = write(fd, buf.baseAddress! + off, buf.count - off)
                    if n <= 0 { return false }
                    off += n
                }
                return true
            }
            close(fd)
            if !ok { unlink(path); return nil }
            return path
        }
        return nil
    }
}
