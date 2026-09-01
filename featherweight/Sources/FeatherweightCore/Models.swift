import Foundation

// MARK: - Risk

public enum RiskLevel: String, Codable, Comparable, CaseIterable, Sendable {
    case safe, warning, danger

    public var label: String {
        switch self {
        case .safe: "Safe to delete"; case .warning: "Use caution"; case .danger: "Can break things"
        }
    }

    public static func < (lhs: RiskLevel, rhs: RiskLevel) -> Bool {
        allCases.firstIndex(of: lhs)! < allCases.firstIndex(of: rhs)!
    }
}

// MARK: - Clean action

/// How a category's disk space gets reclaimed. Declarative, derived from the
/// category's JSON fields — never inferred from path-string conventions.
public enum CleanAction: Equatable, Sendable {
    /// Trash each configured path (directory or file) wholesale.
    case trashPaths
    /// Trash the *children* of each configured path, preserving the directory
    /// itself (for directories apps expect to exist, like ~/Library/Caches).
    case trashContents
    /// Trash the individual paths discovered by `scanCommand`.
    case trashScannedPaths
    /// Run a shell command that frees the space itself (tmutil, docker prune…).
    /// Freed bytes are measured by re-scanning before and after.
    case command(String)
}

// MARK: - Category

public struct Category: Codable, Identifiable, Sendable {
    public let id: String
    public let name: String
    public let icon: String
    public let risk: RiskLevel
    public let group: String
    /// Plain-language explanation of what this category contains, what
    /// cleaning it costs the user, and any caveats. Shown in the UI.
    public let details: String?
    /// Filesystem paths this category owns (may contain a leading `~`).
    public let paths: [String]
    /// Shell command whose du-style output (`KB\tpath` lines) sizes the category.
    public let scanCommand: String?
    /// "contents" ⇒ clean children of each path instead of the path itself.
    public let cleanMode: String?
    /// Shell command that performs the clean itself.
    public let cleanCommand: String?

    public var cleanAction: CleanAction {
        if let cleanCommand { return .command(cleanCommand) }
        if cleanMode == "contents" { return .trashContents }
        if scanCommand != nil { return .trashScannedPaths }
        return .trashPaths
    }

    public init(
        id: String, name: String, icon: String, risk: RiskLevel, group: String,
        details: String? = nil, paths: [String] = [], scanCommand: String? = nil,
        cleanMode: String? = nil, cleanCommand: String? = nil
    ) {
        self.id = id; self.name = name; self.icon = icon; self.risk = risk
        self.group = group; self.details = details; self.paths = paths
        self.scanCommand = scanCommand
        self.cleanMode = cleanMode; self.cleanCommand = cleanCommand
    }

    enum CodingKeys: String, CodingKey {
        case id, name, icon, risk, group, paths, scanCommand, cleanMode, cleanCommand
        case details = "description"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        name = try container.decode(String.self, forKey: .name)
        icon = try container.decode(String.self, forKey: .icon)
        risk = try container.decode(RiskLevel.self, forKey: .risk)
        group = try container.decode(String.self, forKey: .group)
        details = try container.decodeIfPresent(String.self, forKey: .details)
        paths = try container.decodeIfPresent([String].self, forKey: .paths) ?? []
        scanCommand = try container.decodeIfPresent(String.self, forKey: .scanCommand)
        cleanMode = try container.decodeIfPresent(String.self, forKey: .cleanMode)
        cleanCommand = try container.decodeIfPresent(String.self, forKey: .cleanCommand)
    }
}

public struct CategoriesConfig: Codable, Sendable {
    public let categories: [Category]
    public init(categories: [Category]) { self.categories = categories }
}

// MARK: - Scan state

public enum ScanStatus: Equatable, Sendable {
    case idle, scanning, done, error(String)
}

public struct ScanResult: Identifiable, Sendable {
    public let id = UUID()
    public let category: Category
    public var sizeBytes: UInt64
    public var status: ScanStatus
    public var selected: Bool

    public init(category: Category, sizeBytes: UInt64 = 0, status: ScanStatus = .idle, selected: Bool = false) {
        self.category = category
        self.sizeBytes = sizeBytes
        self.status = status
        self.selected = selected
    }

    public var formatted: String {
        if status == .idle { return "—" }
        if sizeBytes == 0 { return "Empty" }
        return ByteCountFormatter().string(fromByteCount: Int64(sizeBytes))
    }
}

// MARK: - History

public struct CleanHistory: Codable, Sendable {
    public let bytes: UInt64
    /// Bytes moved to the Trash by the last clean (recoverable, not yet
    /// freed). Absent in records written before this field existed.
    public let trashedBytes: UInt64
    public let date: Date

    public init(bytes: UInt64, trashedBytes: UInt64 = 0, date: Date) {
        self.bytes = bytes
        self.trashedBytes = trashedBytes
        self.date = date
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        bytes = try container.decode(UInt64.self, forKey: .bytes)
        trashedBytes = try container.decodeIfPresent(UInt64.self, forKey: .trashedBytes) ?? 0
        date = try container.decode(Date.self, forKey: .date)
    }
}
