// swift-tools-version:5.10
// Bench sampler for the "What runs on a Mac" benchmark harness.
// Apple frameworks only — no package dependencies, nothing fetched from the network.
import PackageDescription

let package = Package(
    name: "BenchSampler",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "bench-sampler", targets: ["bench-sampler"]),
        .executable(name: "bench-power", targets: ["bench-power"]),
        // Shared with app/BenchMonitor (local path dependency) for its direct-read fallback.
        .library(name: "BenchProbe", targets: ["BenchProbe"]),
    ],
    targets: [
        .target(name: "BenchProbe", path: "Sources/BenchProbe"),
        .executableTarget(name: "bench-sampler", dependencies: ["BenchProbe"], path: "Sources/bench-sampler"),
        .executableTarget(name: "bench-power", dependencies: ["BenchProbe"], path: "Sources/bench-power"),
    ]
)
