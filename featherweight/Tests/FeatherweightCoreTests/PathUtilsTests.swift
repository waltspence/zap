import XCTest
@testable import FeatherweightCore

final class PathUtilsTests: XCTestCase {
    // MARK: expandTilde

    func testExpandsLeadingTilde() {
        XCTAssertEqual(PathUtils.expandTilde("~/Library", home: "/Users/x"), "/Users/x/Library")
    }

    func testBareTilde() {
        XCTAssertEqual(PathUtils.expandTilde("~", home: "/Users/x"), "/Users/x")
    }

    func testMidPathTildeIsLiteral() {
        XCTAssertEqual(PathUtils.expandTilde("/tmp/~backup", home: "/Users/x"), "/tmp/~backup")
    }

    func testAbsolutePathUnchanged() {
        XCTAssertEqual(PathUtils.expandTilde("/var/log", home: "/Users/x"), "/var/log")
    }

    // MARK: exclusions

    func testFindsDescendantsOfOtherCategories() {
        let all = [
            (id: "caches", path: "/Users/x/Library/Caches"),
            (id: "homebrew", path: "/Users/x/Library/Caches/Homebrew"),
            (id: "pip", path: "/Users/x/Library/Caches/pip"),
            (id: "npm", path: "/Users/x/.npm/_cacache"),
        ]
        let result = PathUtils.exclusions(
            ownId: "caches",
            ownPaths: ["/Users/x/Library/Caches"],
            all: all
        )
        XCTAssertEqual(Set(result), ["/Users/x/Library/Caches/Homebrew", "/Users/x/Library/Caches/pip"])
    }

    func testOwnPathsAreNotExcluded() {
        let all = [(id: "caches", path: "/Users/x/Library/Caches")]
        XCTAssertEqual(
            PathUtils.exclusions(ownId: "caches", ownPaths: ["/Users/x/Library/Caches"], all: all),
            []
        )
    }

    func testPrefixMatchRespectsPathBoundary() {
        // /a/bc is NOT inside /a/b
        let all = [(id: "other", path: "/a/bc")]
        XCTAssertEqual(PathUtils.exclusions(ownId: "own", ownPaths: ["/a/b"], all: all), [])
    }

    func testExactSamePathIsNotADescendant() {
        let all = [(id: "other", path: "/a/b")]
        XCTAssertEqual(PathUtils.exclusions(ownId: "own", ownPaths: ["/a/b"], all: all), [])
    }

    func testEmptyOwnPathsShortCircuits() {
        let all = [(id: "other", path: "/a/b/c")]
        XCTAssertEqual(PathUtils.exclusions(ownId: "own", ownPaths: [], all: all), [])
    }
}
