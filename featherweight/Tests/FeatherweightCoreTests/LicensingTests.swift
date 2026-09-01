import XCTest
@testable import FeatherweightCore

final class LicensingTests: XCTestCase {
    // MARK: validate response parsing

    func testParsesGrantedValidation() throws {
        let json = Data("""
        {"id": "lk_1", "status": "granted", "expires_at": null, "usage": 0, "validations": 3}
        """.utf8)
        let info = try PolarLicenseValidator.parseValidation(json, key: "KEY-1", activationID: "act_9")
        XCTAssertEqual(info.status, .granted)
        XCTAssertNil(info.expiresAt)
        XCTAssertEqual(info.activationID, "act_9")
        XCTAssertTrue(info.isUsable)
    }

    func testParsesRevokedValidation() throws {
        let json = Data(#"{"status": "revoked", "expires_at": null}"#.utf8)
        let info = try PolarLicenseValidator.parseValidation(json, key: "KEY-1", activationID: nil)
        XCTAssertEqual(info.status, .revoked)
        XCTAssertFalse(info.isUsable)
    }

    func testUnknownStatusIsNotUsable() throws {
        let json = Data(#"{"status": "some_future_status"}"#.utf8)
        let info = try PolarLicenseValidator.parseValidation(json, key: "K", activationID: nil)
        XCTAssertFalse(info.isUsable)
    }

    func testExpiredKeyIsNotUsable() throws {
        let json = Data(#"{"status": "granted", "expires_at": "2020-01-01T00:00:00Z"}"#.utf8)
        let info = try PolarLicenseValidator.parseValidation(json, key: "K", activationID: nil)
        XCTAssertEqual(info.status, .granted)
        XCTAssertFalse(info.isUsable)
    }

    func testParsesFractionalSecondDates() throws {
        let json = Data(#"{"status": "granted", "expires_at": "2999-06-15T12:30:45.123456Z"}"#.utf8)
        let info = try PolarLicenseValidator.parseValidation(json, key: "K", activationID: nil)
        XCTAssertNotNil(info.expiresAt)
        XCTAssertTrue(info.isUsable)
    }

    func testMalformedValidationThrows() {
        XCTAssertThrowsError(
            try PolarLicenseValidator.parseValidation(Data("not json".utf8), key: "K", activationID: nil)
        ) { error in
            XCTAssertEqual(error as? LicenseError, .malformedResponse)
        }
    }

    // MARK: activate response parsing

    func testParsesActivationEnvelope() throws {
        let json = Data("""
        {"id": "act_42", "label": "Walt's Mac",
         "license_key": {"id": "lk_1", "status": "granted", "expires_at": null}}
        """.utf8)
        let info = try PolarLicenseValidator.parseActivation(json, key: "KEY-1")
        XCTAssertEqual(info.activationID, "act_42")
        XCTAssertEqual(info.status, .granted)
        XCTAssertTrue(info.isUsable)
    }

    func testActivationWithoutLicenseKeyThrows() {
        let json = Data(#"{"id": "act_42"}"#.utf8)
        XCTAssertThrowsError(try PolarLicenseValidator.parseActivation(json, key: "K")) { error in
            XCTAssertEqual(error as? LicenseError, .malformedResponse)
        }
    }

    // MARK: misc

    func testEmptyOrganizationIDFailsFastWithoutNetwork() async {
        let validator = PolarLicenseValidator(organizationID: "")
        do {
            _ = try await validator.validate(key: "K", activationID: nil)
            XCTFail("expected notConfigured")
        } catch {
            XCTAssertEqual(error as? LicenseError, .notConfigured)
        }
    }
}
