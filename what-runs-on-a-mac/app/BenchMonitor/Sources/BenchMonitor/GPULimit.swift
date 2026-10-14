import AppKit
import BenchProbe
import Foundation
import Metal
import SwiftUI

/// Raising the GPU memory limit (kernel `iogpu.wired_limit_mb`) from the app (2026-10-22).
///
/// Three presets set what macOS keeps (2026-10-22): Aggressive 10% of RAM, DangerAggressive a fixed 5 GB,
/// SuperAggressive the midpoint of the two. A preset is greyed out when it would leave macOS under `minReserveMiB`
/// (4 GB) or would not raise the limit above what macOS already gives the GPU. The menu offers macOS Default plus the
/// three presets.
/// The change needs an administrator password (macOS's own dialog via `do shell script … with administrator
/// privileges`; the app never sees the password), lasts until restart, and "macOS Default" sets it back to 0.
enum GPULimitPreset: String, CaseIterable, Identifiable {
    case aggressive, superAggressive, dangerAggressive
    var id: String { rawValue }

    var title: String {
        switch self {
        case .aggressive: return "Aggressive"
        case .superAggressive: return "SuperAggressive"
        case .dangerAggressive: return "DangerAggressive"
        }
    }

    /// What macOS keeps, in MiB, on a Mac with `phys` MiB of RAM.
    func reserveMiB(physicalMiB phys: Int) -> Int {
        let aggressive = Int((Double(phys) * 0.10).rounded(.up))      // 10% of RAM
        let danger = 5 * 1024                                          // 5 GB, any Mac
        switch self {
        case .aggressive: return aggressive
        case .dangerAggressive: return danger
        case .superAggressive: return (aggressive + danger + 1) / 2   // midpoint of the two
        }
    }

    /// How the reserve is worked out, for the menu.
    var rule: String {
        switch self {
        case .aggressive: return "10% of RAM"
        case .superAggressive: return "midpoint"
        case .dangerAggressive: return "fixed 5 GB"
        }
    }
}

enum GPULimit {
    static let minReserveMiB = 4096
    static let sysctlName = "iogpu.wired_limit_mb"

    struct Plan: Equatable {
        var preset: GPULimitPreset
        var limitMiB: Int              // value for iogpu.wired_limit_mb (MiB)
        var reserveMiB: Int            // what macOS keeps
        var leavesTooLittle: Bool      // would leave macOS under 4 GB: greyed out
        var raisesLimit: Bool          // false: no higher than macOS's own default here: greyed out
        var available: Bool { !leavesTooLittle && raisesLimit }

        var limitGB: Double { Double(limitMiB) / 1024 }
        var reserveGB: Double { Double(reserveMiB) / 1024 }
        var physicalMiB: Int
        var reservePercent: Double { Double(reserveMiB) / Double(physicalMiB) * 100 }
    }

    static var physicalMiB: Int { Int(ProcessInfo.processInfo.physicalMemory / 1_048_576) }

    /// Current kernel setting in MiB; 0 = macOS default.
    static func currentMiB() -> Int {
        var v: Int32 = 0
        var size = MemoryLayout<Int32>.size
        guard sysctlbyname(sysctlName, &v, &size, nil, 0) == 0 else { return 0 }
        return Int(v)
    }

    /// macOS's default GPU ceiling here, in MiB: Metal's recommendedMaxWorkingSetSize, remembered the first time it is
    /// read with the sysctl at 0, so a limit raised before launch is never mistaken for the default.
    static let defaultMiB: Int = {
        let key = "gpuDefaultLimitMiB"
        let metal = MTLCreateSystemDefaultDevice().map { Int($0.recommendedMaxWorkingSetSize / 1_048_576) }
        if currentMiB() == 0, let metal {
            UserDefaults.standard.set(metal, forKey: key)
            return metal
        }
        let saved = UserDefaults.standard.integer(forKey: key)
        return saved > 0 ? saved : (metal ?? physicalMiB * 3 / 4)
    }()

    static func plan(_ preset: GPULimitPreset, physicalMiB phys: Int = physicalMiB, defaultMiB def: Int = defaultMiB) -> Plan {
        let reserve = preset.reserveMiB(physicalMiB: phys)
        let limit = phys - reserve
        return Plan(preset: preset, limitMiB: limit, reserveMiB: reserve,
                    leavesTooLittle: reserve < minReserveMiB, raisesLimit: limit > def, physicalMiB: phys)
    }

    /// Only these values ever reach the privileged command: 0 (macOS default) or a limit above the default that still
    /// leaves macOS 4 GB.
    static func isAllowed(_ mib: Int) -> Bool {
        mib == 0 || (mib > defaultMiB && mib <= physicalMiB - minReserveMiB)
    }

    /// Runs `/usr/sbin/sysctl iogpu.wired_limit_mb=<mib>` as root through macOS's administrator prompt.
    /// The command is built from an integer only. Returns nil on success, or a message (cancelled, refused, …).
    @MainActor
    static func apply(mib: Int) -> String? {
        guard isAllowed(mib) else { return "Refused: \(mib) MiB is outside the allowed range." }
        let source = "do shell script \"/usr/sbin/sysctl \(sysctlName)=\(mib)\" with administrator privileges " +
            "with prompt \"Bench Monitor wants to change the GPU memory limit (\(sysctlName)).\""
        var err: NSDictionary?
        NSAppleScript(source: source)?.executeAndReturnError(&err)
        if let err {
            if (err[NSAppleScript.errorNumber] as? Int) == -128 { return "Cancelled. Nothing was changed." }
            return (err[NSAppleScript.errorMessage] as? String) ?? "The change failed."
        }
        let now = currentMiB()
        if now != mib { return "macOS reports \(sysctlName) = \(now) after the change; expected \(mib)." }
        ThisMac.refreshBudget()
        return nil
    }

    /// The active option for the header chip: macOS Default, one of the presets, or "Custom" for a value set elsewhere.
    static func mode(current cur: Int) -> (name: String, color: Color) {
        if cur == 0 { return ("macOS Default", Theme.text) }
        switch GPULimitPreset.allCases.first(where: { plan($0).limitMiB == cur }) {
        case .aggressive?: return ("Aggressive", Theme.amber)
        case .superAggressive?: return ("SuperAggressive", Theme.amber)
        case .dangerAggressive?: return ("DangerAggressive", Theme.red)
        case nil: return ("Custom \(Fmt.num(Double(cur) / 1024, 1)) GB", Theme.amber)
        }
    }

    /// What the menu says the Mac is set to now.
    static func statusLine(current cur: Int = currentMiB()) -> String {
        if cur == 0 { return "GPU limit: macOS default (\(Fmt.num(Double(defaultMiB) / 1024, 1)) GB of \(physicalMiB / 1024) GB)" }
        let match = GPULimitPreset.allCases.first { plan($0).limitMiB == cur }
        return "GPU limit: raised to \(Fmt.num(Double(cur) / 1024, 1)) GB of \(physicalMiB / 1024) GB" + (match.map { " (\($0.title))" } ?? "")
    }

    static func menuLabel(_ p: Plan) -> String {
        "\(p.preset.title): \(Fmt.num(p.limitGB, 1)) GB for the GPU, \(Fmt.num(p.reserveGB, 1)) GB kept for macOS (\(p.preset.rule), \(Fmt.num(p.reservePercent, 1))%)"
            + (p.leavesTooLittle ? " (under the 4 GB minimum)" : p.raisesLimit ? "" : " (not above the macOS default)")
    }

    static func warning(_ p: Plan) -> String {
        var s = "This lets the GPU take up to \(Fmt.num(p.limitGB, 1)) GB of this Mac's \(physicalMiB / 1024) GB, leaving "
            + "\(Fmt.num(p.reserveGB, 1)) GB for macOS and every other app.\n\n"
            + "This may take down the machine. If a model fills that memory, the Mac can freeze or restart without "
            + "warning, and unsaved work in any app is lost. Close other apps first.\n\n"
            + "It lasts until you restart; GPU Memory › macOS Default undoes it. macOS will ask for an "
            + "administrator password."
        if p.preset == .dangerAggressive {
            s += "\n\nDangerAggressive leaves macOS almost nothing. Expect a hard reset if a model reaches the limit."
        }
        return s
    }
}
