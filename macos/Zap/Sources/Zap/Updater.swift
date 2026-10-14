import AppKit
import Foundation

/// Knows how the running app was built (repo path + commit baked into
/// Info.plist by scripts/make_macos_app.py) and what "newer" looks like.
enum Updater {
    struct Status {
        let buildCommit: String       // commit this .app was built from
        let repoCommit: String        // current HEAD of the checkout
        let behindOrigin: Int         // commits HEAD is behind origin (-1 = unknown/offline)
        var appStale: Bool { !buildCommit.isEmpty && !repoCommit.isEmpty && !repoCommit.hasPrefix(buildCommit) }
        var updateAvailable: Bool { appStale || behindOrigin > 0 }
        var summary: String {
            if behindOrigin > 0 { return "Checkout is \(behindOrigin) commit(s) behind origin." }
            if appStale { return "The app was built from \(buildCommit); the checkout is now at \(repoCommit.prefix(7))." }
            var s = "Up to date (\(buildCommit.isEmpty ? "unknown build" : buildCommit))."
            if behindOrigin < 0 { s += " Could not reach origin — checked the local checkout only." }
            return s
        }
    }

    static var repoPath: String? { Bundle.main.object(forInfoDictionaryKey: "ZapRepoPath") as? String }
    static var buildCommit: String { Bundle.main.object(forInfoDictionaryKey: "ZapBuildCommit") as? String ?? "" }

    static func git(_ args: [String], in repo: String, timeout: TimeInterval = 20) -> String? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        p.arguments = ["-C", repo] + args
        let out = Pipe()
        p.standardOutput = out
        p.standardError = Pipe()
        do { try p.run() } catch { return nil }
        let killer = DispatchWorkItem { if p.isRunning { p.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: killer)
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        killer.cancel()
        guard p.terminationStatus == 0 else { return nil }
        return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Blocking; call off the main thread.
    static func check() -> Status? {
        guard let repo = repoPath, let head = git(["rev-parse", "HEAD"], in: repo) else { return nil }
        var behind = -1
        if git(["fetch", "--quiet", "origin"], in: repo, timeout: 30) != nil,
           let branch = git(["rev-parse", "--abbrev-ref", "HEAD"], in: repo),
           let n = git(["rev-list", "--count", "HEAD..origin/\(branch)"], in: repo).flatMap({ Int($0) }) {
            behind = n
        }
        return Status(buildCommit: buildCommit, repoCommit: head, behindOrigin: behind)
    }

    /// Detach scripts/update_app.sh (pull + rebuild + relaunch) and quit.
    static func updateNowAndQuit() {
        guard let repo = repoPath else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["\(repo)/scripts/update_app.sh"]
        try? p.run()          // detached: we don't wait, the script relaunches us
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { NSApp.terminate(nil) }
    }
}
