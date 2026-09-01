import XCTest
@testable import FeatherweightCore

final class CategoryTests: XCTestCase {
    private func decode(_ json: String) throws -> Category {
        try JSONDecoder().decode(Category.self, from: Data(json.utf8))
    }

    func testDecodesMinimalCategory() throws {
        let category = try decode("""
        {"id": "npm", "name": "npm Cache", "icon": "box", "risk": "safe", "group": "Dev Tools", "paths": ["~/.npm"]}
        """)
        XCTAssertEqual(category.id, "npm")
        XCTAssertEqual(category.risk, .safe)
        XCTAssertEqual(category.paths, ["~/.npm"])
        XCTAssertNil(category.scanCommand)
        XCTAssertEqual(category.cleanAction, .trashPaths)
    }

    func testPathsDefaultToEmptyWhenAbsent() throws {
        let category = try decode("""
        {"id": "tm", "name": "TM", "icon": "clock", "risk": "safe", "group": "System", "scanCommand": "tmutil ..."}
        """)
        XCTAssertEqual(category.paths, [])
    }

    func testCleanActionCommandWinsOverEverything() throws {
        let category = try decode("""
        {"id": "docker", "name": "Docker", "icon": "box", "risk": "warning", "group": "Dev Tools",
         "scanCommand": "docker system df ...", "cleanMode": "contents", "cleanCommand": "docker system prune -f"}
        """)
        XCTAssertEqual(category.cleanAction, .command("docker system prune -f"))
    }

    func testCleanActionContentsMode() throws {
        let category = try decode("""
        {"id": "caches", "name": "Caches", "icon": "gear", "risk": "safe", "group": "System",
         "paths": ["~/Library/Caches"], "cleanMode": "contents"}
        """)
        XCTAssertEqual(category.cleanAction, .trashContents)
    }

    func testCleanActionScannedPathsWhenOnlyScanCommand() throws {
        let category = try decode("""
        {"id": "old", "name": "Old Downloads", "icon": "arrow", "risk": "warning", "group": "Deep Clean",
         "scanCommand": "find ~/Downloads ..."}
        """)
        XCTAssertEqual(category.cleanAction, .trashScannedPaths)
    }

    func testDescriptionDecodesIntoDetails() throws {
        let category = try decode("""
        {"id": "x", "name": "X", "icon": "i", "risk": "safe", "group": "G",
         "description": "What this is and what cleaning it costs.", "paths": ["~/x"]}
        """)
        XCTAssertEqual(category.details, "What this is and what cleaning it costs.")
    }

    func testDetailsDefaultToNilWhenAbsent() throws {
        let category = try decode("""
        {"id": "x", "name": "X", "icon": "i", "risk": "safe", "group": "G", "paths": ["~/x"]}
        """)
        XCTAssertNil(category.details)
    }

    func testRiskLevelOrdering() {
        XCTAssertLessThan(RiskLevel.safe, .warning)
        XCTAssertLessThan(RiskLevel.warning, .danger)
    }

    func testBundledConfigShapeDecodes() throws {
        let config = try JSONDecoder().decode(CategoriesConfig.self, from: Data("""
        {"categories": [
          {"id": "a", "name": "A", "icon": "i", "risk": "safe", "group": "G", "paths": ["~/a"]},
          {"id": "b", "name": "B", "icon": "i", "risk": "danger", "group": "G", "scanCommand": "find ..."}
        ]}
        """.utf8))
        XCTAssertEqual(config.categories.count, 2)
        XCTAssertEqual(config.categories[1].cleanAction, .trashScannedPaths)
    }
}
