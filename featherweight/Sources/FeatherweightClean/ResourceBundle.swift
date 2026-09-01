import Foundation

/// Locates the SwiftPM resource bundle without going through the generated
/// `Bundle.module` accessor first. That accessor only checks
/// `Bundle.main.bundleURL` and an absolute build-machine path, and calls
/// fatalError when both miss — so a packaged app that carries the bundle in
/// the conventional `Contents/Resources` location would crash on launch on
/// any other machine. Checking `resourceURL` first supports that layout;
/// `Bundle.module` remains the fallback for `swift run`/`swift test`, where
/// the bundle sits next to the executable and the accessor succeeds.
enum ResourceBundle {
    private static let name = "Featherweight_FeatherweightClean.bundle"

    static let bundle: Bundle = {
        let candidates = [Bundle.main.resourceURL, Bundle.main.bundleURL]
        for candidate in candidates {
            if let url = candidate?.appendingPathComponent(name), let found = Bundle(url: url) {
                return found
            }
        }
        return Bundle.module
    }()
}
