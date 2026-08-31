import Foundation

/// The `zap --json` envelope. Only the fields the GUI needs are decoded.
public struct Envelope<T: Decodable>: Decodable {
    public let ok: Bool
    public let data: T?
    public let error: ZapFailure?
}

public struct ZapFailure: Decodable, Error, Equatable {
    public let code: String
    public let message: String
    public let blockers: [Blocker]?

    public init(code: String, message: String, blockers: [Blocker]? = nil) {
        self.code = code; self.message = message; self.blockers = blockers
    }

    enum CodingKeys: String, CodingKey { case code, message, details }
    enum DetailsKeys: String, CodingKey { case blockers }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        code = try c.decode(String.self, forKey: .code)
        message = try c.decode(String.self, forKey: .message)
        let d = try? c.nestedContainer(keyedBy: DetailsKeys.self, forKey: .details)
        blockers = try? d?.decodeIfPresent([Blocker].self, forKey: .blockers)
    }

    public static func == (a: ZapFailure, b: ZapFailure) -> Bool { a.code == b.code && a.message == b.message }
}

public struct InstalledApp: Decodable, Identifiable, Hashable {
    public let name: String
    public let identifier: String
    public let bundleId: String?
    public let displayName: String?
    public let version: String?
    public let protected: Bool
    public var id: String { identifier }
    public var title: String { displayName ?? name }

    enum CodingKeys: String, CodingKey {
        case name, identifier, version, protected
        case bundleId = "bundle_id", displayName = "display_name"
    }
}

public struct AppList: Decodable { public let apps: [InstalledApp] }

public struct Artifact: Decodable, Identifiable, Hashable {
    public struct Match: Decodable, Hashable { public let confidence: String }
    public let id: String
    public let path: String
    public let category: String
    public let match: Match
    public let risk: String
    public let sizeBytes: Int64
    public let autoSelected: Bool
    public let requiresRoot: Bool
    public let shared: Bool

    enum CodingKeys: String, CodingKey {
        case id, path, category, match, risk, shared
        case sizeBytes = "size_bytes", autoSelected = "auto_selected", requiresRoot = "requires_root"
    }

    public var tier: String {
        switch match.confidence {
        case "exact": return "exact"
        case "derived": return "derived"
        case "heuristic": return "name-only"
        default: return match.confidence
        }
    }
    public var risky: Bool { requiresRoot || shared || risk == "high" }
}

public struct Plan: Decodable {
    public let app: InstalledApp
    public let artifacts: [Artifact]
}

public struct ActionResult: Decodable, Identifiable {
    public let id: String
    public let path: String
    public let status: String
    public let reason: String?
}

public struct Report: Decodable {
    public let counts: [String: Int]
    public let bytesReclaimed: Int64
    public let results: [ActionResult]
    enum CodingKeys: String, CodingKey { case counts, results, bytesReclaimed = "bytes_reclaimed" }
    public var removed: Int { counts["removed"] ?? 0 }
    public var needsAttention: [ActionResult] { results.filter { $0.status != "removed" && $0.status != "dry_run" } }
}

public struct UndoOperation: Decodable, Identifiable, Hashable {
    public let operationId: String
    public let appName: String
    public let createdAt: String
    public let method: String
    public let itemCount: Int
    public let reversibleCount: Int
    public var id: String { operationId }
    enum CodingKeys: String, CodingKey {
        case method
        case operationId = "operation_id", appName = "app_name", createdAt = "created_at"
        case itemCount = "item_count", reversibleCount = "reversible_count"
    }
}

public struct History: Decodable { public let operations: [UndoOperation] }

public struct UndoResult: Decodable {
    public struct Row: Decodable { public let status: String }
    public let results: [Row]
    public var restored: Int { results.filter { $0.status == "restored" }.count }
}

public func humanBytes(_ n: Int64) -> String {
    var x = Double(n)
    for unit in ["B", "KB", "MB", "GB"] {
        if x < 1024 || unit == "GB" {
            return unit == "B" ? "\(Int(x)) B" : String(format: "%.1f %@", x, unit)
        }
        x /= 1024
    }
    return String(format: "%.1f GB", x)
}
