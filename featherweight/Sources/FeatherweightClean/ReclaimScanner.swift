import Combine
import FeatherweightCore
import Foundation

/// Observable scan/clean orchestrator behind the Reclaim Space UI.
/// (Named to avoid colliding with Foundation.Scanner in consuming modules.)
@MainActor
public final class ReclaimScanner: ObservableObject {
    @Published public var results: [ScanResult] = []
    @Published public var isScanning = false
    @Published public var isCleaning = false
    @Published public var totalReclaimable: UInt64 = 0
    @Published public var totalSelected: UInt64 = 0
    @Published public var lastCleanedBytes: UInt64 = 0
    @Published public var lastTrashedBytes: UInt64 = 0
    @Published public var lastCleanedDate: Date?
    @Published public var scanProgress: Double = 0
    @Published public var searchText = ""
    @Published public var showAll = false
    @Published public var isFirstLaunch = false
    @Published public var loadError: String?
    @Published public var projectRoots: [String] {
        didSet { UserDefaults.standard.set(projectRoots, forKey: Self.rootsKey) }
    }

    /// Absolute path to the `zap` binary, substituted for {ZAP} in scan
    /// commands; empty when zap isn't installed (commands fall back).
    public var zapBinary: String

    public static let rootsKey = "featherweight.projectRoots"
    public static let defaultRoots = ["~/dev", "~/lab", "~/wrkflw", "~/Projects", "~/Desktop"]
    private let defaultsKey = "featherweight.lastCleaned"

    private let homeDir = FileManager.default.homeDirectoryForCurrentUser.path
    private let engine = ScanEngine()
    private var scanTask: Task<Void, Never>?
    private var hasScanned = false

    // MARK: - Derived state

    public var filteredResults: [ScanResult] {
        let base = searchText.isEmpty ? results : results.filter { $0.category.name.localizedCaseInsensitiveContains(searchText) }
        if showAll { return base }
        // Only show categories that have content or are still scanning/pending
        return base.filter { $0.sizeBytes > 0 || $0.status != .done || !hasScanned }
    }

    public var hiddenCount: Int {
        guard hasScanned else { return 0 }
        return results.filter { $0.sizeBytes == 0 && $0.status == .done }.count
    }

    public var selectedNames: [String] {
        results.filter(\.selected).map { $0.category.name }
    }

    // MARK: - Lifecycle

    public init(zapBinary: String = "", autoScanOnFirstLaunch: Bool = true) {
        self.zapBinary = zapBinary
        projectRoots = UserDefaults.standard.stringArray(forKey: Self.rootsKey) ?? Self.defaultRoots
        loadCategories()
        loadHistory()
        if lastCleanedDate == nil && autoScanOnFirstLaunch {
            isFirstLaunch = true
            // Auto-detect on first launch
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 500_000_000) // let UI render first
                scan()
            }
        }
    }

    private func loadCategories() {
        guard let url = ResourceBundle.bundle.url(forResource: "categories", withExtension: "json") else {
            loadError = "categories.json is missing from the app's resources."
            return
        }
        do {
            let data = try Data(contentsOf: url)
            let config = try JSONDecoder().decode(CategoriesConfig.self, from: data)
            results = config.categories.map { ScanResult(category: $0, selected: $0.risk == .safe) }
        } catch {
            loadError = "Could not load categories: \(error.localizedDescription)"
        }
    }

    private func loadHistory() {
        guard let data = UserDefaults.standard.data(forKey: defaultsKey),
              let history = try? JSONDecoder().decode(CleanHistory.self, from: data)
        else { return }
        lastCleanedBytes = history.bytes
        lastTrashedBytes = history.trashedBytes
        lastCleanedDate = history.date
    }

    private func saveHistory() {
        let history = CleanHistory(bytes: lastCleanedBytes, trashedBytes: lastTrashedBytes, date: Date())
        if let data = try? JSONEncoder().encode(history) {
            UserDefaults.standard.set(data, forKey: defaultsKey)
        }
        lastCleanedDate = Date()
    }

    // MARK: - Scanning

    public func scan() {
        scanTask?.cancel()
        scanTask = Task { await performScan() }
    }

    public func cancelScan() {
        scanTask?.cancel()
        isScanning = false
        for i in results.indices where results[i].status == .scanning {
            results[i].status = .idle
        }
        updateTotals()
    }

    private func performScan() async {
        guard !results.isEmpty else {
            isFirstLaunch = false
            return
        }
        isScanning = true
        scanProgress = 0
        let total = Double(results.count)

        // Every category's expanded plain paths, for overlap exclusion
        let allPlainPaths = results.flatMap { r in
            r.category.paths.map { (id: r.category.id, path: expand($0)) }
        }

        // Parallel scan — 8 at a time
        let batchSize = 8
        var idx = 0
        while idx < results.count {
            if Task.isCancelled { break }
            let batch = Array(results[idx..<min(idx + batchSize, results.count)])

            await withTaskGroup(of: (Int, UInt64, Bool).self) { group in
                for (offset, result) in batch.enumerated() {
                    let i = idx + offset
                    results[i].status = .scanning
                    let engine = self.engine

                    if let scanCommand = result.category.scanCommand {
                        let resolved = resolveTokens(scanCommand)
                        group.addTask {
                            (i, await engine.scanCommand(resolved), false)
                        }
                    } else {
                        let paths = result.category.paths.map { self.expand($0) }
                        let excluded = PathUtils.exclusions(
                            ownId: result.category.id, ownPaths: paths, all: allPlainPaths
                        )
                        group.addTask {
                            let outcome = await engine.scanPaths(paths, excluding: excluded)
                            return (i, outcome.bytes, outcome.denied)
                        }
                    }
                }
                for await (i, size, denied) in group {
                    // A cancelled scan must not overwrite the idle states
                    // cancelScan() installed (or a newer scan's results) with
                    // late-arriving sizes from still-draining child tasks.
                    if Task.isCancelled { continue }
                    results[i].sizeBytes = size
                    // denied with a nonzero size means a partial count: some
                    // descendants were unreadable, so still surface the FDA
                    // state rather than presenting the scan as complete.
                    results[i].status = denied ? .error("Needs Full Disk Access") : .done
                }
            }

            idx += batchSize
            scanProgress = Double(min(idx, results.count)) / total
        }

        if !Task.isCancelled { hasScanned = true }
        updateTotals()
        isFirstLaunch = false
        isScanning = false
    }

    // MARK: - Selection

    public func toggle(_ id: UUID) {
        guard let i = results.firstIndex(where: { $0.id == id }) else { return }
        if results[i].category.risk == .danger && !results[i].selected { return } // can't select danger
        results[i].selected.toggle()
        updateTotals()
    }

    public func selectAllSafe() {
        for i in results.indices where results[i].category.risk == .safe { results[i].selected = true }
        updateTotals()
    }

    public func deselectAll() {
        for i in results.indices { results[i].selected = false }
        updateTotals()
    }

    private func updateTotals() {
        // Danger categories are reference-only (supersets / uncleanable), so
        // they never count toward the honest "Found" figure.
        totalReclaimable = results.filter { $0.category.risk != .danger }.reduce(0) { $0 + $1.sizeBytes }
        totalSelected = results.filter { $0.selected }.reduce(0) { $0 + $1.sizeBytes }
    }

    // MARK: - Cleaning

    public func clean() async {
        isCleaning = true
        var totalFreed: UInt64 = 0
        var totalTrashed: UInt64 = 0
        var logEntries: [CleanLogger.Entry] = []

        // Same overlap set the scan subtracts, so cleaning a parent category
        // (System Caches) never removes a nested category the user deselected.
        let allPlainPaths = results.flatMap { r in
            r.category.paths.map { (id: r.category.id, path: expand($0)) }
        }

        for result in results where result.selected {
            let category = result.category
            let report: CleanEngine.Report
            // The rm -rf fallback is only acceptable for safe cache-type
            // categories; user documents must reach the Trash or be skipped.
            let allowHardDelete = category.risk == .safe

            switch category.cleanAction {
            case .command(let command):
                report = await CleanEngine.cleanViaCommand(
                    resolveTokens(command),
                    measuringPaths: category.paths.map { expand($0) },
                    measuringCommand: category.scanCommand.map { resolveTokens($0) }
                )
            case .trashScannedPaths:
                report = await CleanEngine.trashScannedPaths(
                    command: resolveTokens(category.scanCommand ?? ""),
                    allowHardDelete: allowHardDelete
                )
            case .trashContents:
                let paths = category.paths.map { expand($0) }
                report = await CleanEngine.trashContents(
                    of: paths,
                    excluding: PathUtils.exclusions(ownId: category.id, ownPaths: paths, all: allPlainPaths),
                    allowHardDelete: allowHardDelete
                )
            case .trashPaths:
                report = await CleanEngine.trashPaths(
                    category.paths.map { expand($0) }, allowHardDelete: allowHardDelete
                )
            }

            totalFreed += report.freedBytes
            totalTrashed += report.trashedBytes
            logEntries.append(CleanLogger.Entry(
                category: category.name, freedBytes: report.freedBytes,
                trashedBytes: report.trashedBytes,
                removals: report.removals, skippedPaths: report.skippedPaths
            ))
        }

        CleanLogger.append(entries: logEntries)
        lastCleanedBytes = totalFreed
        lastTrashedBytes = totalTrashed
        saveHistory()
        isCleaning = false
        // Rescan through the normal scan lifecycle so Stop can cancel it.
        scan()
    }

    // MARK: - Path & token resolution

    private nonisolated func expand(_ path: String) -> String {
        PathUtils.expandTilde(path, home: homeDir)
    }

    /// Substitutes {RESOURCES} (bundled helper scripts), {PROJECT_ROOTS}
    /// (user-configured project folders, expanded and shell-quoted) and {ZAP}
    /// (the zap binary path, shell-quoted; empty when zap isn't installed).
    private func resolveTokens(_ command: String) -> String {
        var resolved = command.replacingOccurrences(
            of: "{RESOURCES}", with: ResourceBundle.bundle.resourcePath ?? ""
        )
        let roots = projectRoots
            .map { expand($0) }
            .map { Self.shellQuote($0) }
            .joined(separator: " ")
        resolved = resolved.replacingOccurrences(of: "{PROJECT_ROOTS}", with: roots)
        resolved = resolved.replacingOccurrences(of: "{ZAP}", with: zapBinary)
        return resolved
    }

    /// Single-quotes a string for bash so ", $, and other metacharacters are
    /// literal; embedded single quotes become '\''.
    private nonisolated static func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
}
