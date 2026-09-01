import Foundation
import ZapKit

var failures = 0
func check(_ cond: @autoclosure () throws -> Bool, _ what: String, file: String = #fileID, line: Int = #line) {
    if (try? cond()) == true { print("  ok   \(what)") } else { failures += 1; print("  FAIL \(what)  (\(file):\(line))") }
}
func expectThrows<T>(_ body: @autoclosure () throws -> T, _ what: String, _ verify: (Error) -> Bool = { _ in true }) {
    do { _ = try body(); failures += 1; print("  FAIL \(what): did not throw") }
    catch { check(verify(error), what) }
}
func stub(_ table: [String: String]) -> ZapClient {
    ZapClient { args in
        guard let body = table[args[0]] else { throw NSError(domain: "t", code: 1) }
        return Data(body.utf8)
    }
}
final class Box: @unchecked Sendable { var seen: [String] = [] }

print("ZapKit checks")
do {
    let c = stub(["list": #"{"ok":true,"data":{"apps":[{"name":"b","identifier":"b","protected":false},{"name":"Finder","identifier":"f","protected":true},{"name":"A","identifier":"a","protected":false}]}}"#])
    check(try c.listApps().map(\.name) == ["A", "b"], "list hides protected apps and sorts")
}
do {
    let box = Box()
    let c = ZapClient { args in
        box.seen = args
        return Data(#"{"ok":true,"data":{"app":{"name":"G","identifier":"g","protected":false},"artifacts":[{"id":"a1","path":"/x","category":"caches","match":{"confidence":"heuristic"},"risk":"normal","size_bytes":2048,"auto_selected":false,"requires_root":true,"shared":false}]}}"#.utf8)
    }
    let plan = try c.scan("g", aggressive: true, includeLow: false)
    check(box.seen == ["scan", "g", "--aggressive"], "scan passes flags")
    check(plan.artifacts[0].tier == "name-only" && plan.artifacts[0].risky, "artifact tier/risk decode")
}
do {
    let box = Box()
    let c = ZapClient { args in
        box.seen = args
        return Data(#"{"ok":true,"data":{"counts":{"removed":1},"bytes_reclaimed":10,"results":[{"id":"a1","path":"/x","status":"removed"}]}}"#.utf8)
    }
    let r = try c.uninstall("g", only: ["a1", "a2"])
    check(Array(box.seen.suffix(2)) == ["--only", "a1,a2"], "uninstall sends only ticked ids")
    check(r.removed == 1 && r.needsAttention.isEmpty, "report decode")
}
do {
    let c = stub(["uninstall": #"{"ok":false,"data":{"counts":{"blocked":1},"bytes_reclaimed":0,"results":[{"id":"a1","path":"/x","status":"blocked","reason":"foreign app"}]},"error":{"code":"blocked","message":"m"}}"#])
    check(try c.uninstall("g", only: ["a1"]).needsAttention.map(\.reason) == ["foreign app"], "partial report decodes when ok=false")
}
expectThrows(try stub(["scan": #"{"ok":false,"data":null,"error":{"code":"app_not_found","message":"nope"}}"#]).scan("x", aggressive: false, includeLow: false),
             "error envelope → ZapFailure") { ($0 as? ZapFailure) == ZapFailure(code: "app_not_found", message: "nope") }
expectThrows(try stub(["history": "Traceback: boom"]).history(), "garbage output → bad_output") { ($0 as? ZapFailure)?.code == "bad_output" }
expectThrows(try stub([:]).history(), "spawn failure → spawn_failed") { ($0 as? ZapFailure)?.code == "spawn_failed" }
check(humanBytes(500) == "500 B" && humanBytes(2048) == "2.0 KB" && humanBytes(5 << 30) == "5.0 GB", "humanBytes")
do {
    let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("zapbin-\(UUID())").path
    FileManager.default.createFile(atPath: tmp, contents: Data("#!/bin/sh\n".utf8), attributes: [.posixPermissions: 0o755])
    check(ZapClient.locateBinary(env: ["ZAP_BIN": tmp, "PATH": ""]) == tmp, "locateBinary honours ZAP_BIN")
    check(ZapClient.locateBinary(env: ["HOME": "/nonexistent", "PATH": "/nonexistent"]) == nil, "locateBinary nil when absent")
    try? FileManager.default.removeItem(atPath: tmp)
}
do {
    let c = stub(["uninstall": #"{"ok":false,"data":null,"error":{"code":"permission_required","message":"m","details":{"blockers":[{"code":"needs_root","message":"sudo pls","paths":["/a","/b"]}]}}}"#])
    do { _ = try c.uninstall("g", only: ["a1"]); failures += 1; print("  FAIL blockers: did not throw") }
    catch let e as ZapFailure {
        check(e.code == "permission_required" && e.blockers?.first?.paths == ["/a", "/b"], "permission_required blockers decode")
    } catch { failures += 1; print("  FAIL blockers: wrong error") }
}
do {
    let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("fda-\(UUID())").path
    check(FullDiskAccess.granted(home: tmp) == nil, "FDA probe inconclusive for missing db")
    try? FileManager.default.createDirectory(atPath: "\(tmp)/Library/Application Support/com.apple.TCC", withIntermediateDirectories: true)
    FileManager.default.createFile(atPath: "\(tmp)/Library/Application Support/com.apple.TCC/TCC.db", contents: Data())
    check(FullDiskAccess.granted(home: tmp) == true, "FDA probe true for readable db")
    try? FileManager.default.removeItem(atPath: tmp)
}

print(failures == 0 ? "all checks passed" : "\(failures) FAILED")
exit(failures == 0 ? 0 : 1)
