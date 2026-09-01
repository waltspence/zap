// swift-tools-version: 6.0
import PackageDescription

// Featherweight is no longer a standalone app: these libraries are the
// disk-cleanup ("Reclaim Space") engine consumed by the Zap GUI in macos/Zap.
let package = Package(
    name: "Featherweight",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "FeatherweightCore", targets: ["FeatherweightCore"]),
        .library(name: "FeatherweightClean", targets: ["FeatherweightClean"]),
    ],
    targets: [
        // Platform-neutral engine: models, parsing, sizing, process running.
        // Foundation-only so it builds and tests on Linux too.
        .target(
            name: "FeatherweightCore",
            path: "Sources/FeatherweightCore"
        ),
        // macOS cleanup layer: category config, scan orchestration, trashing,
        // and the clean log. UI-framework-free apart from ObservableObject.
        .target(
            name: "FeatherweightClean",
            dependencies: ["FeatherweightCore"],
            path: "Sources/FeatherweightClean",
            resources: [.process("categories.json"), .copy("app-leftovers.sh")]
        ),
        .testTarget(
            name: "FeatherweightCoreTests",
            dependencies: ["FeatherweightCore"],
            path: "Tests/FeatherweightCoreTests"
        ),
    ]
)
