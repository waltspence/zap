import XCTest
@testable import FeatherweightCore

final class DirectorySizerTests: XCTestCase {
    private var tempDir: URL!

    override func setUpWithError() throws {
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("DirectorySizerTests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tempDir)
    }

    private func write(_ name: String, bytes: Int) throws {
        let url = tempDir.appendingPathComponent(name)
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try Data(repeating: 0x41, count: bytes).write(to: url)
    }

    func testMissingPathIsZeroNotDenied() async {
        let outcome = await DirectorySizer.size(ofPath: tempDir.appendingPathComponent("nope").path)
        XCTAssertEqual(outcome.bytes, 0)
        XCTAssertFalse(outcome.denied)
    }

    func testEmptyDirectoryIsZero() async {
        let outcome = await DirectorySizer.size(ofPath: tempDir.path)
        XCTAssertEqual(outcome.bytes, 0)
        XCTAssertFalse(outcome.denied)
    }

    func testCountsNestedFiles() async throws {
        try write("a.bin", bytes: 10_000)
        try write("sub/b.bin", bytes: 20_000)
        try write("sub/deep/c.bin", bytes: 5_000)
        let outcome = await DirectorySizer.size(ofPath: tempDir.path)
        // Allocated size is block-rounded, so it's at least the logical size
        XCTAssertGreaterThanOrEqual(outcome.bytes, 35_000)
        XCTAssertFalse(outcome.denied)
    }

    func testSizesSingleFile() async throws {
        try write("single.bin", bytes: 12_345)
        let outcome = await DirectorySizer.size(ofPath: tempDir.appendingPathComponent("single.bin").path)
        XCTAssertGreaterThanOrEqual(outcome.bytes, 12_345)
    }
}
