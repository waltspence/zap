import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// MARK: - License model

public enum LicenseStatus: String, Sendable {
    case granted, revoked, disabled
}

public struct LicenseInfo: Sendable, Equatable {
    public let key: String
    public let status: LicenseStatus
    public let expiresAt: Date?
    /// Device activation identifier, when the key uses activation limits.
    public let activationID: String?

    public init(key: String, status: LicenseStatus, expiresAt: Date? = nil, activationID: String? = nil) {
        self.key = key
        self.status = status
        self.expiresAt = expiresAt
        self.activationID = activationID
    }

    public var isUsable: Bool {
        status == .granted && (expiresAt.map { $0 > Date() } ?? true)
    }
}

public enum LicenseError: Error, Equatable, Sendable {
    /// The store isn't wired up yet (no organization ID configured).
    case notConfigured
    /// The key doesn't exist or doesn't belong to this organization.
    case invalidKey
    /// The key is valid but its device-activation limit is exhausted.
    case activationLimitReached
    /// Connectivity problem — the caller may fall back to an offline grace period.
    case network(String)
    case server(Int)
    case malformedResponse
}

public protocol LicenseValidator: Sendable {
    func activate(key: String, label: String) async throws -> LicenseInfo
    func validate(key: String, activationID: String?) async throws -> LicenseInfo
    func deactivate(key: String, activationID: String) async throws
}

// MARK: - Polar

/// License validation against Polar's customer-portal license key API.
/// Endpoint shapes per https://docs.polar.sh/api-reference — verify against
/// current docs when configuring the production organization ID.
public struct PolarLicenseValidator: LicenseValidator {
    public let organizationID: String
    private let baseURL: URL

    public init(
        organizationID: String,
        baseURL: URL = URL(string: "https://api.polar.sh/v1/customer-portal/license-keys")!
    ) {
        self.organizationID = organizationID
        self.baseURL = baseURL
    }

    public func activate(key: String, label: String) async throws -> LicenseInfo {
        let data = try await post("activate", body: [
            "key": key, "organization_id": organizationID, "label": label,
        ])
        return try Self.parseActivation(data, key: key)
    }

    public func validate(key: String, activationID: String?) async throws -> LicenseInfo {
        var body: [String: Any] = ["key": key, "organization_id": organizationID]
        if let activationID { body["activation_id"] = activationID }
        let data = try await post("validate", body: body)
        return try Self.parseValidation(data, key: key, activationID: activationID)
    }

    public func deactivate(key: String, activationID: String) async throws {
        _ = try await post("deactivate", body: [
            "key": key, "organization_id": organizationID, "activation_id": activationID,
        ])
    }

    // MARK: Transport

    private func post(_ endpoint: String, body: [String: Any]) async throws -> Data {
        guard !organizationID.isEmpty else { throw LicenseError.notConfigured }
        var request = URLRequest(url: baseURL.appendingPathComponent(endpoint))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw LicenseError.network(error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else {
            throw LicenseError.network("No HTTP response")
        }
        switch http.statusCode {
        case 200, 201, 204: return data
        case 404: throw LicenseError.invalidKey
        case 403: throw LicenseError.activationLimitReached
        default: throw LicenseError.server(http.statusCode)
        }
    }

    // MARK: Parsing (internal for tests)

    private struct ValidatePayload: Decodable {
        let status: String
        let expiresAt: Date?
        enum CodingKeys: String, CodingKey {
            case status
            case expiresAt = "expires_at"
        }
    }

    private struct ActivationEnvelope: Decodable {
        let id: String
        let licenseKey: ValidatePayload
        enum CodingKeys: String, CodingKey {
            case id
            case licenseKey = "license_key"
        }
    }

    static func parseValidation(_ data: Data, key: String, activationID: String?) throws -> LicenseInfo {
        guard let payload = try? decoder.decode(ValidatePayload.self, from: data) else {
            throw LicenseError.malformedResponse
        }
        return LicenseInfo(
            key: key,
            status: LicenseStatus(rawValue: payload.status) ?? .disabled,
            expiresAt: payload.expiresAt,
            activationID: activationID
        )
    }

    static func parseActivation(_ data: Data, key: String) throws -> LicenseInfo {
        guard let envelope = try? decoder.decode(ActivationEnvelope.self, from: data) else {
            throw LicenseError.malformedResponse
        }
        return LicenseInfo(
            key: key,
            status: LicenseStatus(rawValue: envelope.licenseKey.status) ?? .disabled,
            expiresAt: envelope.licenseKey.expiresAt,
            activationID: envelope.id
        )
    }

    private static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { dec in
            let raw = try dec.singleValueContainer().decode(String.self)
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = formatter.date(from: raw) { return date }
            formatter.formatOptions = [.withInternetDateTime]
            if let date = formatter.date(from: raw) { return date }
            throw DecodingError.dataCorrupted(.init(
                codingPath: dec.codingPath, debugDescription: "Unrecognized date: \(raw)"
            ))
        }
        return decoder
    }()
}
