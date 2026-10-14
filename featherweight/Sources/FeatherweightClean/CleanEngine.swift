import FeatherweightCore
import Foundation

/// Executes a category's CleanAction and reports what was freed.
/// Lives in the app target because trashItem is macOS-only.
enum CleanEngine {
    struct Removal: Sendable {
        let path: String
        let bytes: UInt64
        /// True when the item went to the Trash (recoverable, still on disk)
        /// rather than being deleted outright.
        let trashed: Bool
    }

    struct Report: Sendable {
        /// Bytes actually released back to the filesystem (hard deletes,
        /// command cleans measured before/after).
        var freedBytes: UInt64 = 0
        /// Bytes moved to the Trash — recoverable, and not free until the
        /// user empties it, so never conflated with freedBytes.
        var trashedBytes: UInt64 = 0
        var removals: [Removal] = []
        var skippedPaths: [String] = []
    }

    private static let engine = ScanEngine()

    /// Trash each path wholesale.
    static func trashPaths(_ paths: [String], allowHardDelete: Bool) async -> Report {
        var report = Report()
        for path in paths {
            await remove(path, allowHardDelete: allowHardDelete, into: &report)
        }
        return report
    }

    /// Trash the children of each directory, preserving the directory itself.
    /// `excluding` lists paths owned by other categories (the same set the
    /// scan subtracts): a child is skipped when it is, or contains, an
    /// excluded path, so deselecting a nested category really preserves it.
    static func trashContents(of directories: [String], excluding: [String] = [], allowHardDelete: Bool) async -> Report {
        var report = Report()
        for directory in directories {
            guard let children = try? FileManager.default.contentsOfDirectory(atPath: directory) else { continue }
            for child in children {
                let path = (directory as NSString).appendingPathComponent(child)
                if excluding.contains(where: { $0 == path || $0.hasPrefix(path + "/") }) { continue }
                await remove(path, allowHardDelete: allowHardDelete, into: &report)
            }
        }
        return report
    }

    /// Re-run the category's scan command and trash the paths it reports.
    static func trashScannedPaths(command: String, allowHardDelete: Bool) async -> Report {
        guard !command.isEmpty else { return Report() }
        let output = await ProcessRunner.runShell(command)
        return await trashPaths(OutputParser.absolutePaths(from: output), allowHardDelete: allowHardDelete)
    }

    /// Run a clean command that frees space itself (tmutil, docker prune…).
    /// Freed bytes are measured by re-scanning before and after — never trusted
    /// from the command.
    static func cleanViaCommand(_ command: String, measuringPaths: [String], measuringCommand: String?) async -> Report {
        let before = await measure(paths: measuringPaths, command: measuringCommand)
        _ = await ProcessRunner.runShell(command)
        let after = await measure(paths: measuringPaths, command: measuringCommand)
        return Report(freedBytes: before > after ? before - after : 0, removals: [])
    }

    private static func measure(paths: [String], command: String?) async -> UInt64 {
        if let command { return await engine.scanCommand(command) }
        return await engine.scanPaths(paths, excluding: []).bytes
    }

    /// Move to Trash (undoable); fall back to rm -rf when trashing fails, but
    /// only when allowHardDelete is set (safe cache-type categories). Otherwise
    /// the path is skipped and recorded so the user can audit it in the log.
    /// Success of the fallback is judged by the path actually being gone.
    private static func remove(_ path: String, allowHardDelete: Bool, into report: inout Report) async {
        guard FileManager.default.fileExists(atPath: path) else { return }
        let size = await DirectorySizer.size(ofPath: path).bytes
        do {
            try FileManager.default.trashItem(at: URL(fileURLWithPath: path), resultingItemURL: nil)
            report.trashedBytes += size
            report.removals.append(Removal(path: path, bytes: size, trashed: true))
        } catch {
            guard allowHardDelete else {
                report.skippedPaths.append(path)
                return
            }
            _ = await ProcessRunner.run("/bin/rm", ["-rf", path])
            guard !FileManager.default.fileExists(atPath: path) else {
                // Both the trash move and the fallback left the item in
                // place — record it so the clean log stays a complete audit.
                report.skippedPaths.append(path)
                return
            }
            report.freedBytes += size
            report.removals.append(Removal(path: path, bytes: size, trashed: false))
        }
    }
}
