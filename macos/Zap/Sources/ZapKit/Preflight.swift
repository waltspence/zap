import Foundation

public enum FullDiskAccess {
    /// True/False when determinable, nil when inconclusive.
    /// Probe: the per-user TCC database is readable only with Full Disk Access.
    public static func granted(home: String = NSHomeDirectory()) -> Bool? {
        let probe = "\(home)/Library/Application Support/com.apple.TCC/TCC.db"
        guard FileManager.default.fileExists(atPath: probe) else { return nil }
        guard let fh = FileHandle(forReadingAtPath: probe) else { return false }
        try? fh.close()
        return true
    }

    public static let settingsURL = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")!
}

public struct Blocker: Decodable, Identifiable, Hashable {
    public let code: String
    public let message: String
    public let paths: [String]
    public var id: String { code }
}
