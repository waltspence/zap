// swift-tools-version:5.9
import PackageDescription

// Note: the Command Line Tools ship no XCTest, so ZapKitCheck is a plain
// executable of assertions (`swift run zapkit-check`) rather than a testTarget.
let package = Package(
    name: "Zap",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "ZapKit"),
        .executableTarget(name: "Zap", dependencies: ["ZapKit"]),
        .executableTarget(name: "zapkit-check", dependencies: ["ZapKit"], path: "Sources/ZapKitCheck"),
    ]
)
