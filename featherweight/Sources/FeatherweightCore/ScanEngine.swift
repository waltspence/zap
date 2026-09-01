import Foundation

/// Sizes categories. Command inputs must arrive fully resolved (tokens like
/// {RESOURCES}/{PROJECT_ROOTS} substituted, tildes expanded) — the engine
/// has no knowledge of app configuration.
public struct ScanEngine: Sendable {
    public init() {}

    /// Sizes a command-based category from its du-style output.
    public func scanCommand(_ resolvedCommand: String) async -> UInt64 {
        OutputParser.duBytes(from: await ProcessRunner.runShell(resolvedCommand))
    }

    /// Sizes a path-based category, subtracting `excluding` descendants that
    /// belong to other categories so totals never double-count.
    public func scanPaths(_ expandedPaths: [String], excluding: [String]) async -> DirectorySizer.Outcome {
        var total: UInt64 = 0
        var denied = false
        await withTaskGroup(of: DirectorySizer.Outcome.self) { group in
            for path in expandedPaths {
                group.addTask { await DirectorySizer.size(ofPath: path) }
            }
            for await outcome in group {
                total += outcome.bytes
                denied = denied || outcome.denied
            }
        }
        for excludedPath in excluding {
            let excluded = await DirectorySizer.size(ofPath: excludedPath).bytes
            total = total > excluded ? total - excluded : 0
        }
        return DirectorySizer.Outcome(bytes: total, denied: denied)
    }
}
