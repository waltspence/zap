import Foundation

/// Parses du-style command output: one `KB\tpath` line per entry.
public enum OutputParser {
    /// Sums the leading KB field of every line and returns bytes.
    /// Tolerates space-separated output as a fallback.
    public static func duBytes(from output: String) -> UInt64 {
        var totalKB: UInt64 = 0
        for line in output.split(separator: "\n") {
            if let kb = line.split(separator: "\t").first.flatMap({ UInt64($0) }) {
                totalKB += kb
            } else if let kb = line.split(separator: " ").first.flatMap({ UInt64($0) }) {
                totalKB += kb
            }
        }
        return totalKB * 1024
    }

    /// Extracts the path field (after the first tab) from each line.
    /// Only absolute paths are returned — placeholder labels emitted by
    /// estimate-only commands (e.g. "tm-snapshots") must never be deletable.
    public static func absolutePaths(from output: String) -> [String] {
        output.split(separator: "\n").compactMap { line in
            let parts = line.split(separator: "\t", maxSplits: 1)
            guard parts.count > 1 else { return nil }
            let path = String(parts[1])
            return path.hasPrefix("/") ? path : nil
        }
    }
}
