import AppKit
import SwiftUI

/// Settings keys (UserDefaults, domain = the bundle id):
///   projectDir, historyFile, extraVolume   applied with the Apply button
///   footerNote                             live: right-hand note in the footer (empty = none)
///   remoteControl                          live: accept distributed-notification remote control
struct SettingsView: View {
    @Environment(MonitorModel.self) private var m
    @AppStorage("projectDir") private var projectDir = ""        // empty = discover (see MonitorModel)
    @AppStorage("historyFile") private var historyFile = "history.jsonl"
    @AppStorage("extraVolume") private var extraVolume = ""      // empty = no extra volume in the footer
    @AppStorage("footerNote") private var footerNote = ""
    @AppStorage(RemoteControl.defaultsKey) private var remoteControl = false

    var body: some View {
        Form {
            Section {
                HStack {
                    TextField("Project folder", text: $projectDir, prompt: Text(MonitorModel.discoveredProjectDir))
                        .textFieldStyle(.roundedBorder)
                        .frame(minWidth: 420)
                    Button("Choose…") { choose(into: $projectDir) }
                }
                Text("Bench Monitor follows <project>/runs/current (events.jsonl, samples.jsonl) and builds the results table from <project>/runs/\(historyFile). Several history files, comma-separated, are merged (e.g. history.jsonl, history.jais3.jsonl). Leave the project empty to use the one this app was built in.")
                    .font(.system(size: 14)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                TextField("History file(s) in runs/, comma-separated", text: $historyFile)
                    .textFieldStyle(.roundedBorder)
                HStack {
                    TextField("Extra volume", text: $extraVolume, prompt: Text("optional, e.g. /Volumes/Models"))
                        .textFieldStyle(.roundedBorder)
                    Button("Choose…") { choose(into: $extraVolume, start: "/Volumes") }
                }
                Text("Shown in the footer as \"<name> free N GB\" while it is mounted.")
                    .font(.system(size: 14)).foregroundStyle(.secondary)
                let env = ProcessInfo.processInfo.environment
                if env["BENCH_PROJECT"] != nil || env["BENCH_HISTORY"] != nil || env["BENCH_EXTRA_VOLUME"] != nil {
                    Text("Note: BENCH_PROJECT / BENCH_HISTORY / BENCH_EXTRA_VOLUME are set in the environment for this launch; Apply overrides them until the next launch.")
                        .font(.system(size: 14)).foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
                HStack {
                    Text("Active: \(m.config.projectDir) · \(m.config.historyFile)\(m.config.extraVolume.map { " · \($0)" } ?? "")")
                        .font(.system(size: 14)).foregroundStyle(.secondary).lineLimit(2)
                    Spacer()
                    Button("Apply") { m.updateConfig(projectDir: projectDir, historyFile: historyFile, extraVolume: extraVolume) }
                        .keyboardShortcut(.defaultAction)
                }
            }
            Section {
                TextField("Footer note", text: $footerNote, prompt: Text("optional, e.g. a disclaimer shown on screen"))
                    .textFieldStyle(.roundedBorder)
            }
            Section {
                Toggle("Allow remote control (distributed notifications)", isOn: $remoteControl)
                Text("Lets any process of this user toggle Presentation (\(RemoteControl.presentationName)) and save snapshots into the screenshots folder (\(RemoteControl.snapshotName)). Off by default; BENCH_REMOTE=1 turns it on for one launch.")
                    .font(.system(size: 14)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .formStyle(.grouped)
        .padding(20)
        .frame(width: 720)
        .font(.system(size: 14))
    }

    private func choose(into binding: Binding<String>, start: String? = nil) {
        let p = NSOpenPanel()
        p.canChooseDirectories = true
        p.canChooseFiles = false
        p.allowsMultipleSelection = false
        let current = binding.wrappedValue.isEmpty ? (start ?? m.config.projectDir) : binding.wrappedValue
        p.directoryURL = URL(fileURLWithPath: (current as NSString).expandingTildeInPath)
        if p.runModal() == .OK, let url = p.url { binding.wrappedValue = url.path }
    }
}
