import XCTest
@testable import FeatherweightCore

final class OutputParserTests: XCTestCase {
    // MARK: duBytes

    func testSumsTabSeparatedKilobytes() {
        let output = "100\t/a/b\n200\t/c/d\n"
        XCTAssertEqual(OutputParser.duBytes(from: output), 300 * 1024)
    }

    func testFallsBackToSpaceSeparated() {
        let output = "50 /a/b\n25 /c/d"
        XCTAssertEqual(OutputParser.duBytes(from: output), 75 * 1024)
    }

    func testIgnoresGarbageLines() {
        let output = "not-a-number\t/a\n100\t/b\n\n"
        XCTAssertEqual(OutputParser.duBytes(from: output), 100 * 1024)
    }

    func testEmptyOutputIsZero() {
        XCTAssertEqual(OutputParser.duBytes(from: ""), 0)
    }

    func testLiteralBackslashTIsNotATab() {
        // Regression: `echo "KB\tlabel"` emits a literal backslash-t, which
        // must not parse as a valid entry (the old Time Machine bug).
        let output = #"500000\ttm-snapshots"#
        XCTAssertEqual(OutputParser.duBytes(from: output), 0)
    }

    // MARK: absolutePaths

    func testExtractsAbsolutePaths() {
        let output = "100\t/Users/x/proj/node_modules\n200\t/Users/x/other\n"
        XCTAssertEqual(
            OutputParser.absolutePaths(from: output),
            ["/Users/x/proj/node_modules", "/Users/x/other"]
        )
    }

    func testRejectsPlaceholderLabels() {
        // Estimate-only commands emit labels, never deletable paths
        let output = "1000000\ttm-snapshots\n500\tdocker\n100\t/real/path"
        XCTAssertEqual(OutputParser.absolutePaths(from: output), ["/real/path"])
    }

    func testPreservesPathsContainingTabsAfterFirst() {
        let output = "100\t/a/weird\tname"
        XCTAssertEqual(OutputParser.absolutePaths(from: output), ["/a/weird\tname"])
    }

    func testLinesWithoutTabYieldNoPaths() {
        XCTAssertEqual(OutputParser.absolutePaths(from: "100 /space/separated"), [])
    }
}
