import Foundation

public enum PathUtils {
    /// Expands a *leading* tilde only; a `~` elsewhere in the path is literal.
    public static func expandTilde(_ path: String, home: String) -> String {
        path.hasPrefix("~") ? home + path.dropFirst() : path
    }

    /// Paths belonging to other categories that live strictly inside one of
    /// `ownPaths`. Subtracting their sizes prevents double-counting when a
    /// category like ~/Library/Caches contains other categories' paths.
    public static func exclusions(
        ownId: String,
        ownPaths: [String],
        all: [(id: String, path: String)]
    ) -> [String] {
        guard !ownPaths.isEmpty else { return [] }
        return all
            .filter { $0.id != ownId }
            .map(\.path)
            .filter { candidate in ownPaths.contains { candidate.hasPrefix($0 + "/") } }
    }
}
