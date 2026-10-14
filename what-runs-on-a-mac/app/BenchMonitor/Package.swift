// swift-tools-version:5.10
// Bench Monitor — the on-screen dashboard for the "What runs on a Mac" benchmark harness.
// Apple frameworks only. The one dependency is the local sampler package (BenchProbe), by path:
// nothing is fetched from the network.
import PackageDescription

let package = Package(
    name: "BenchMonitor",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(path: "../../sampler")
    ],
    targets: [
        .executableTarget(
            name: "BenchMonitor",
            dependencies: [.product(name: "BenchProbe", package: "sampler")],
            path: "Sources/BenchMonitor")
    ]
)
