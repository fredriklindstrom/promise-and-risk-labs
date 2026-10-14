import BenchProbe
import Foundation

/// The Mac this app is running on, read once at launch (nothing about the machine is hard-coded).
enum ThisMac {
    static let info = MachineInfo.detect()
    static var memoryGB: Double { info.memoryGB }
    /// GPU ceiling before any run reports one: Metal's own recommendedMaxWorkingSetSize, else 3/4 of RAM.
    /// In the chart's unit (binary GB, like memoryGB and the sampler's used memory); MachineInfo reports it in 10^9 bytes.
    static var defaultBudgetGB: Double { info.metalBudgetGB.map(decimalToChartGB) ?? info.memoryGB * 0.75 }

    /// The harness and MachineInfo give the GPU budget in 10^9 bytes (83.49 on the 96 GB Studio); the memory chart,
    /// "96 GB physical" and used memory are binary GB (2^30), as Activity Monitor and oMLX count them (77.76).
    static func decimalToChartGB(_ gb: Double) -> Double { gb * 1e9 / bytesPerGB }

    /// The GPU ceiling right now (a raised iogpu.wired_limit_mb wins), in chart units. Shown when no run is active;
    /// re-read after GPU Memory changes the limit.
    static private(set) var liveBudgetGB: Double = defaultBudgetGB
    static func refreshBudget() {
        liveBudgetGB = MachineInfo.metalBudget().map(decimalToChartGB) ?? info.memoryGB * 0.75
    }
}
