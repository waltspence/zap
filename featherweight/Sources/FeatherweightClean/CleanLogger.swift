import Foundation

/// Appends a human-readable record of every clean to
/// ~/Library/Logs/Featherweight/clean.log so users can audit what was removed.
public enum CleanLogger {
    struct Entry {
        let category: String
        let freedBytes: UInt64
        let trashedBytes: UInt64
        let removals: [CleanEngine.Removal]
        let skippedPaths: [String]
    }

    public static var logFileURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/Featherweight/clean.log")
    }

    public static func ensureLogExists() {
        let fm = FileManager.default
        try? fm.createDirectory(at: logFileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !fm.fileExists(atPath: logFileURL.path) {
            fm.createFile(atPath: logFileURL.path, contents: Data())
        }
    }

    static func append(entries: [Entry]) {
        guard !entries.isEmpty else { return }
        ensureLogExists()

        let stamp = ISO8601DateFormatter().string(from: Date())
        let formatter = ByteCountFormatter()
        var lines: [String] = []
        for entry in entries {
            var parts: [String] = []
            if entry.freedBytes > 0 { parts.append("freed \(formatter.string(fromByteCount: Int64(entry.freedBytes)))") }
            if entry.trashedBytes > 0 { parts.append("moved \(formatter.string(fromByteCount: Int64(entry.trashedBytes))) to Trash") }
            if parts.isEmpty { parts.append("freed Zero KB") }
            lines.append("[\(stamp)] \(entry.category): \(parts.joined(separator: ", "))")
            for removal in entry.removals {
                let verb = removal.trashed ? "trashed" : "deleted"
                lines.append("[\(stamp)]   \(verb) \(removal.path) (\(formatter.string(fromByteCount: Int64(removal.bytes))))")
            }
            for path in entry.skippedPaths {
                lines.append("[\(stamp)]   skipped \(path) (could not move to Trash)")
            }
        }
        guard let data = (lines.joined(separator: "\n") + "\n").data(using: .utf8),
              let handle = try? FileHandle(forWritingTo: logFileURL)
        else { return }
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: data)
    }
}
