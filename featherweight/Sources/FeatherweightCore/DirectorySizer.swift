import Foundation

/// Native replacement for shelling out to `du -sk`: sizes a path with
/// FileManager enumeration. No process-spawn overhead, and permission
/// failures are reported instead of silently read as "empty".
public enum DirectorySizer {
    public struct Outcome: Sendable {
        public let bytes: UInt64
        /// True when the path exists but listing it was refused (TCC /
        /// Full Disk Access), so a 0 size means "unknown", not "empty".
        public let denied: Bool
        public init(bytes: UInt64, denied: Bool) {
            self.bytes = bytes
            self.denied = denied
        }
    }

    /// Async wrapper that keeps the blocking filesystem walk off the
    /// Swift-concurrency cooperative pool.
    public static func size(ofPath path: String) async -> Outcome {
        await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .utility).async {
                continuation.resume(returning: sizeSync(ofPath: path))
            }
        }
    }

    public static func sizeSync(ofPath path: String) -> Outcome {
        let fm = FileManager.default
        var isDir: ObjCBool = false
        guard fm.fileExists(atPath: path, isDirectory: &isDir) else {
            return Outcome(bytes: 0, denied: false)
        }
        let url = URL(fileURLWithPath: path)

        if !isDir.boolValue {
            return Outcome(bytes: allocatedSize(of: url), denied: false)
        }

        // Distinguish "empty" from "not allowed to look"
        do {
            _ = try fm.contentsOfDirectory(atPath: path)
        } catch {
            let nsError = error as NSError
            let denied = nsError.domain == NSCocoaErrorDomain && nsError.code == NSFileReadNoPermissionError
            return Outcome(bytes: 0, denied: denied)
        }

        guard let enumerator = fm.enumerator(
            at: url,
            includingPropertiesForKeys: Array(resourceKeys),
            options: [],
            errorHandler: { _, _ in true }
        ) else { return Outcome(bytes: 0, denied: false) }

        var total: UInt64 = 0
        for case let fileURL as URL in enumerator {
            total += allocatedSize(of: fileURL)
        }
        return Outcome(bytes: total, denied: false)
    }

    private static let resourceKeys: Set<URLResourceKey> = [
        .isRegularFileKey, .totalFileAllocatedSizeKey, .fileAllocatedSizeKey, .fileSizeKey,
    ]

    private static func allocatedSize(of url: URL) -> UInt64 {
        guard let values = try? url.resourceValues(forKeys: resourceKeys) else { return 0 }
        // Directories and symlinks contribute no file bytes themselves
        if values.isRegularFile == false { return 0 }
        let size = values.totalFileAllocatedSize ?? values.fileAllocatedSize ?? values.fileSize ?? 0
        return UInt64(max(0, size))
    }
}
