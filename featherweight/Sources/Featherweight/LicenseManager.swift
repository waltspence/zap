import AppKit
import FeatherweightCore
import Foundation
import Security
import SwiftUI

// MARK: - Keychain storage

enum KeychainStore {
    private static let service = "com.walt.featherweight.license"
    private static let account = "license"

    private static var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    static func save(_ data: Data) {
        SecItemDelete(baseQuery as CFDictionary)
        var attributes = baseQuery
        attributes[kSecValueData as String] = data
        SecItemAdd(attributes as CFDictionary, nil)
    }

    static func load() -> Data? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess else { return nil }
        return item as? Data
    }

    static func delete() {
        SecItemDelete(baseQuery as CFDictionary)
    }
}

// MARK: - License manager

@MainActor
final class LicenseManager: ObservableObject {
    // TODO before launch: set from the Polar dashboard (Settings → identifier)
    // and create the Featherweight Pro product + checkout link.
    static let polarOrganizationID = ""
    static let purchaseURL: URL? = nil // e.g. URL(string: "https://buy.polar.sh/...")

    @Published private(set) var isPro = false
    @Published private(set) var statusText = "Free version"
    @Published private(set) var isBusy = false
    @Published var lastError: String?

    /// Keep Pro working without network for this long after the last
    /// successful validation.
    private static let offlineGracePeriod: TimeInterval = 14 * 24 * 3600

    private struct StoredLicense: Codable {
        var key: String
        var activationID: String?
        var lastValidated: Date
    }

    private let validator: any LicenseValidator
    private var stored: StoredLicense?

    init(validator: (any LicenseValidator)? = nil) {
        self.validator = validator ?? PolarLicenseValidator(organizationID: Self.polarOrganizationID)
        if let data = KeychainStore.load(),
           let license = try? JSONDecoder().decode(StoredLicense.self, from: data) {
            stored = license
            // Trust the stored license optimistically; revalidation below corrects it
            isPro = true
            statusText = "Pro — licensed"
        }
        Task { await revalidate() }
    }

    var maskedKey: String? {
        guard let key = stored?.key, key.count > 4 else { return stored?.key }
        return "••••-" + key.suffix(4)
    }

    func activate(key: String) async {
        let trimmed = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        isBusy = true
        lastError = nil
        defer { isBusy = false }
        do {
            let label = Host.current().localizedName ?? "Mac"
            let info = try await validator.activate(key: trimmed, label: label)
            apply(info)
        } catch {
            lastError = Self.describe(error)
        }
    }

    func deactivate() async {
        isBusy = true
        lastError = nil
        defer { isBusy = false }
        if let stored, let activationID = stored.activationID {
            try? await validator.deactivate(key: stored.key, activationID: activationID)
        }
        clear()
    }

    func revalidate() async {
        guard let current = stored else { return }
        do {
            let info = try await validator.validate(key: current.key, activationID: current.activationID)
            apply(info)
        } catch let error as LicenseError {
            switch error {
            case .network, .server, .malformedResponse:
                // Transient failure (offline, 5xx, garbled reply): honor the
                // grace period rather than punishing flaky wifi or a server
                // outage — and never delete the stored key for these.
                if Date().timeIntervalSince(current.lastValidated) > Self.offlineGracePeriod {
                    isPro = false
                    statusText = "License needs revalidation — connect to the internet"
                }
            case .notConfigured:
                break // store not wired up yet; leave state alone
            case .invalidKey, .activationLimitReached:
                // Definitive rejection — drop the stored key
                clear()
                statusText = "License invalid or revoked"
            }
        } catch {
            // Unknown failure — treat like a network error within grace
            if Date().timeIntervalSince(current.lastValidated) > Self.offlineGracePeriod {
                isPro = false
                statusText = "License needs revalidation — connect to the internet"
            }
        }
    }

    private func apply(_ info: LicenseInfo) {
        guard info.isUsable else {
            clear()
            statusText = info.status == .granted ? "License expired" : "License \(info.status.rawValue)"
            return
        }
        let record = StoredLicense(
            key: info.key,
            activationID: info.activationID ?? stored?.activationID,
            lastValidated: Date()
        )
        stored = record
        if let data = try? JSONEncoder().encode(record) {
            KeychainStore.save(data)
        }
        isPro = true
        statusText = "Pro — licensed"
    }

    private func clear() {
        stored = nil
        KeychainStore.delete()
        isPro = false
        statusText = "Free version"
    }

    private static func describe(_ error: Error) -> String {
        guard let licenseError = error as? LicenseError else { return error.localizedDescription }
        switch licenseError {
        case .notConfigured: return "The store isn't configured yet."
        case .invalidKey: return "That license key wasn't recognized."
        case .activationLimitReached: return "This key has reached its device limit. Deactivate another Mac first."
        case .network(let detail): return "Couldn't reach the license server: \(detail)"
        case .server(let code): return "License server error (\(code)). Try again later."
        case .malformedResponse: return "Unexpected response from the license server."
        }
    }
}
