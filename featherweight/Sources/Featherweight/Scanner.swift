import FeatherweightCore
import Foundation
import SwiftUI

@MainActor
final class Scanner: ObservableObject {
    @Published var results: [ScanResult] = []
    @Published var isScanning = false
    @Published var isCleaning = false
    @Published var totalReclaimable: UInt64 = 0
    @Published var totalSelected: UInt64 = 0
    @Published var lastCleanedBytes: UInt64 = 0
    @Published var lastCleanedDate: Date?
    @Published var scanProgress: Double = 0
    @Published var searchText = ""
    @Published var showAll = false
    @Published var isFirstLaunch = false
    @Published var loadError: String?
    @Published var projectRoots: [String] {
        didSet { UserDefaults.standard.set(projectRoots, forKey: Self.rootsKey) }
    }

    static let rootsKey = "featherweight.projectRoots"
    static let defaultRoots = ["~/dev", "~/lab", "~/wrkflw", "~/Projects", "~/Desktop"]
    private let defaultsKey = "featherweight.lastCleaned"

    private let homeDir = FileManager.default.homeDirectoryForCurrentUser.path
    private let engine = ScanEngine()
    private var scanTask: Task<Void, Never>?
    private var hasScanned = false

    // MARK: - Derived state

    var filteredResults: [ScanResult] {
        let base = searchText.isEmpty ? results : results.filter { $0.category.name.localizedCaseInsensitiveContains(searchText) }
        if showAll { return base }
        // Only show categories that have content or are still scanning/pending
        return base.filter { $0.sizeBytes > 0 || $0.status != .done || !hasScanned }
    }

    var hiddenCount: Int {
        guard hasScanned else { return 0 }
        return results.filter { $0.sizeBytes == 0 && $0.status == .done }.count
    }

    var selectedNames: [String] {
        results.filter(\.selected).map { $0.category.name }
    }

    // MARK: - Lifecycle

    init() {
        projectRoots = UserDefaults.standard.stringArray(forKey: Self.rootsKey) ?? Self.defaultRoots
        loadCategories()
        loadHistory()
        if lastCleanedDate == nil {
            isFirstLaunch = true
            // Auto-detect on first launch
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 500_000_000) // let UI render first
                scan()
            }
        }
    }

    private func loadCategories() {
        guard let url = Bundle.module.url(forResource: "categories", withExtension: "json") else {
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
        lastCleanedDate = history.date
    }

    private func saveHistory() {
        let history = CleanHistory(bytes: lastCleanedBytes, date: Date())
        if let data = try? JSONEncoder().encode(history) {
            UserDefaults.standard.set(data, forKey: defaultsKey)
        }
        lastCleanedDate = Date()
    }

    // MARK: - Scanning

    func scan() {
        scanTask?.cancel()
        scanTask = Task { await performScan() }
    }

    func cancelScan() {
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
                    results[i].sizeBytes = size
                    results[i].status = (denied && size == 0) ? .error("Needs Full Disk Access") : .done
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

    func toggle(_ id: UUID) {
        guard let i = results.firstIndex(where: { $0.id == id }) else { return }
        if results[i].category.risk == .danger && !results[i].selected { return } // can't select danger
        results[i].selected.toggle()
        updateTotals()
    }

    func selectAllSafe() {
        for i in results.indices where results[i].category.risk == .safe { results[i].selected = true }
        updateTotals()
    }

    func deselectAll() {
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

    func clean() async {
        isCleaning = true
        var totalFreed: UInt64 = 0
        var logEntries: [CleanLogger.Entry] = []

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
                report = await CleanEngine.trashContents(
                    of: category.paths.map { expand($0) }, allowHardDelete: allowHardDelete
                )
            case .trashPaths:
                report = await CleanEngine.trashPaths(
                    category.paths.map { expand($0) }, allowHardDelete: allowHardDelete
                )
            }

            totalFreed += report.freedBytes
            logEntries.append(CleanLogger.Entry(
                category: category.name, freedBytes: report.freedBytes,
                removals: report.removals, skippedPaths: report.skippedPaths
            ))
        }

        CleanLogger.append(entries: logEntries)
        lastCleanedBytes = totalFreed
        saveHistory()
        isCleaning = false
        await performScan()
    }

    // MARK: - Path & token resolution

    private nonisolated func expand(_ path: String) -> String {
        PathUtils.expandTilde(path, home: homeDir)
    }

    /// Substitutes {RESOURCES} (bundled helper scripts) and {PROJECT_ROOTS}
    /// (user-configured project folders, expanded and shell-quoted).
    private func resolveTokens(_ command: String) -> String {
        var resolved = command.replacingOccurrences(
            of: "{RESOURCES}", with: Bundle.module.resourcePath ?? ""
        )
        let roots = projectRoots
            .map { expand($0) }
            .map { Self.shellQuote($0) }
            .joined(separator: " ")
        resolved = resolved.replacingOccurrences(of: "{PROJECT_ROOTS}", with: roots)
        return resolved
    }

    /// Single-quotes a string for bash so ", $, and other metacharacters are
    /// literal; embedded single quotes become '\''.
    private nonisolated static func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
}
