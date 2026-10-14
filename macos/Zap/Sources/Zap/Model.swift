import FeatherweightClean
import Foundation
import ZapKit

/// What the sidebar can select: the Reclaim Space tool, or an installed app.
enum SidebarItem: Hashable {
    case reclaim
    case app(InstalledApp.ID)
}

@MainActor
final class Model: ObservableObject {
    @Published var apps: [InstalledApp] = []
    @Published var filter = ""
    @Published var sidebarSelection: SidebarItem?
    @Published var plan: Plan?
    @Published var checked: Set<String> = []
    @Published var aggressive = false { didSet { if oldValue != aggressive { rescan() } } }
    @Published var includeLow = false { didSet { if oldValue != includeLow { rescan() } } }
    @Published var busy = false
    @Published var status = ""
    @Published var attention: [ActionResult] = []
    @Published var operations: [UndoOperation]?     // non-nil = undo sheet visible
    @Published var confirmTrash = false
    @Published var fdaMissing = false      // launch gate: require Full Disk Access first
    @Published var updateInfo: String?     // non-nil = update alert visible
    @Published var updateReady = false     // true when "Update Now" applies
    @Published var blockers: [Blocker] = []

    let client: ZapClient
    let reclaim: ReclaimScanner
    private var reclaimScanKicked = false

    init(client: ZapClient? = nil) {
        let bin = ZapClient.locateBinary()
        if let client { self.client = client }
        else if let bin { self.client = .live(binary: bin) }
        else { self.client = ZapClient { _ in throw ZapFailure(code: "not_installed", message: "zap not found; pip install -e . first") } }
        // The Reclaim scanner shells out to zap for orphan detection when
        // available; the GUI decides when the first scan runs.
        reclaim = ReclaimScanner(zapBinary: bin ?? "", autoScanOnFirstLaunch: false)
        fdaMissing = FullDiskAccess.granted() == false
        refresh()
    }

    var selectedApp: InstalledApp.ID? {
        if case .app(let id) = sidebarSelection { return id }
        return nil
    }

    var visibleApps: [InstalledApp] {
        let f = filter.lowercased()
        return f.isEmpty ? apps : apps.filter { $0.title.lowercased().contains(f) || ($0.bundleId ?? "").lowercased().contains(f) }
    }
    var selectedArtifacts: [Artifact] { (plan?.artifacts ?? []).filter { checked.contains($0.id) } }
    var selectedBytes: Int64 { selectedArtifacts.reduce(0) { $0 + $1.sizeBytes } }
    var canTrash: Bool { !busy && !selectedArtifacts.isEmpty }

    // MARK: actions

    func refresh() {
        let client = client
        run { try client.listApps() } then: { apps in
            self.apps = apps
            if self.status.isEmpty { self.status = "\(apps.count) apps" }
        }
    }

    func select(_ item: SidebarItem?) {
        sidebarSelection = item
        plan = nil; checked = []
        switch item {
        case .app:
            rescan()
        case .reclaim:
            // First visit kicks off a scan; after that the user drives it.
            if !reclaimScanKicked {
                reclaimScanKicked = true
                reclaim.scan()
            }
        case nil:
            break
        }
    }

    func rescan() {
        guard let id = selectedApp else { return }
        let (agg, low, client) = (aggressive, includeLow, client)
        run { try client.scan(id, aggressive: agg, includeLow: low) } then: { plan in
            self.plan = plan
            self.checked = Set(plan.artifacts.filter(\.autoSelected).map(\.id))
            self.updateFooter()
        }
    }

    func toggle(_ id: String) {
        if checked.contains(id) { checked.remove(id) } else { checked.insert(id) }
        updateFooter()
    }

    func trash() {
        guard let plan, !selectedArtifacts.isEmpty else { return }
        let ids = selectedArtifacts.map(\.id)
        let (client, ident) = (client, plan.app.identifier)
        run { try client.uninstall(ident, only: ids) } then: { report in
            self.attention = report.needsAttention
            self.status = "\(report.removed) removed · \(humanBytes(report.bytesReclaimed)) reclaimed"
                + (self.attention.isEmpty ? "" : " · \(self.attention.count) need attention")
            self.plan = nil; self.checked = []; self.sidebarSelection = nil
            self.refresh()
        }
    }

    func recheckAccess() { fdaMissing = FullDiskAccess.granted() == false }

    func checkForUpdates() {
        guard !busy else { return }
        busy = true
        Task.detached(priority: .userInitiated) {
            let status = Updater.check()
            await MainActor.run {
                self.busy = false
                if let status {
                    self.updateReady = status.updateAvailable
                    self.updateInfo = status.summary
                } else {
                    self.updateReady = false
                    self.updateInfo = "Could not check: the source checkout was not found (rebuild with make app)."
                }
            }
        }
    }

    func showHistory() {
        let client = client
        run { try client.history() } then: { ops in
            if ops.isEmpty { self.status = "Nothing to undo" } else { self.operations = ops }
        }
    }

    func restore(_ op: UndoOperation) {
        operations = nil
        let (client, oid) = (client, op.operationId)
        run { try client.undo(oid) } then: { r in
            self.status = "restored \(r.restored) of \(r.results.count) — \(op.appName)"
            self.refresh()
        }
    }

    // MARK: plumbing

    private func updateFooter() {
        guard let plan else { return }
        status = "\(selectedArtifacts.count) of \(plan.artifacts.count) selected · \(humanBytes(selectedBytes))"
    }

    private func run<T: Sendable>(_ work: @escaping @Sendable () throws -> T, then done: @escaping (T) -> Void) {
        guard !busy else { return }
        busy = true
        Task.detached(priority: .userInitiated) {
            let result = Result { try work() }
            await MainActor.run {
                self.busy = false
                switch result {
                case .success(let v): done(v)
                case .failure(let e as ZapFailure):
                    self.status = "\(e.code): \(e.message)"
                    if e.code == "permission_required" { self.blockers = e.blockers ?? [] }
                case .failure(let e): self.status = e.localizedDescription
                }
            }
        }
    }
}
