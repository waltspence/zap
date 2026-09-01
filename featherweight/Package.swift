// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Featherweight",
    platforms: [.macOS(.v14)],
    targets: [
        // Platform-neutral engine: models, parsing, sizing, process running.
        // Foundation-only so it builds and tests on Linux too.
        .target(
            name: "FeatherweightCore",
            path: "Sources/FeatherweightCore"
        ),
        .executableTarget(
            name: "Featherweight",
            dependencies: ["FeatherweightCore"],
            path: "Sources/Featherweight",
            resources: [.process("categories.json"), .copy("app-leftovers.sh")]
        ),
        .testTarget(
            name: "FeatherweightCoreTests",
            dependencies: ["FeatherweightCore"],
            path: "Tests/FeatherweightCoreTests"
        ),
    ]
)
