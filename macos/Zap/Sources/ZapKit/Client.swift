import Foundation

/// Runs `zap --json …` and decodes the envelope. The process runner is
/// injectable so tests never spawn anything.
public struct ZapClient: Sendable {
    public typealias Runner = @Sendable ([String]) throws -> Data

    public let runner: Runner

    public init(runner: @escaping Runner) { self.runner = runner }

    /// Locate the `zap` executable: $ZAP_BIN, then common install paths, then PATH.
    public static func locateBinary(env: [String: String] = ProcessInfo.processInfo.environment) -> String? {
        if let p = env["ZAP_BIN"], FileManager.default.isExecutableFile(atPath: p) { return p }
        let home = env["HOME"] ?? NSHomeDirectory()
        var candidates = ["\(home)/.local/bin/zap", "/opt/homebrew/bin/zap", "/usr/local/bin/zap"]
        for dir in (env["PATH"] ?? "").split(separator: ":") { candidates.append("\(dir)/zap") }
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    public static func live(binary: String, timeout: TimeInterval = 300) -> ZapClient {
        ZapClient { args in
            let p = Process()
            p.executableURL = URL(fileURLWithPath: binary)
            p.arguments = ["--json"] + args
            let out = Pipe()
            p.standardOutput = out
            p.standardError = out
            try p.run()
            let killer = DispatchWorkItem { if p.isRunning { p.terminate() } }
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: killer)
            let data = out.fileHandleForReading.readDataToEndOfFile()
            p.waitUntilExit()
            killer.cancel()
            return data
        }
    }

    public func call<T: Decodable>(_ args: [String], as type: T.Type = T.self) throws -> T {
        let data: Data
        do { data = try runner(args) } catch { throw ZapFailure(code: "spawn_failed", message: error.localizedDescription) }
        let env: Envelope<T>
        do { env = try JSONDecoder().decode(Envelope<T>.self, from: data) } catch {
            let text = String(data: data.prefix(300), encoding: .utf8) ?? ""
            throw ZapFailure(code: "bad_output", message: text.isEmpty ? "no output" : text)
        }
        if let d = env.data, env.ok || T.self == Report.self { return d }
        throw env.error ?? ZapFailure(code: "error", message: "unknown failure")
    }

    public func listApps() throws -> [InstalledApp] {
        try call(["list"], as: AppList.self).apps.filter { !$0.protected }
            .sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending }
    }

    public func scan(_ identifier: String, aggressive: Bool, includeLow: Bool) throws -> Plan {
        var args = ["scan", identifier]
        if aggressive { args.append("--aggressive") }
        if includeLow { args.append("--include-low") }
        return try call(args)
    }

    public func uninstall(_ identifier: String, only ids: [String]) throws -> Report {
        try call(["uninstall", identifier, "--yes", "--aggressive", "--include-low", "--only", ids.joined(separator: ",")])
    }

    public func history() throws -> [UndoOperation] { try call(["history"], as: History.self).operations }

    public func undo(_ operationId: String) throws -> UndoResult { try call(["undo", operationId]) }
}
